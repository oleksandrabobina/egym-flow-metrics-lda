import csv
import os
import re
import sqlite3
import statistics
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join("database", "dora_metrics.db")

def canonical_normalize(name_str):
    """
    Strips out conjunctions (&, and, +, /, with), hyphens, and extra spaces.
    Converts 'WP Offer & Acquisition' AND 'WP Offer Acquisition' to 'wp offer acquisition'.
    """
    if not name_str:
        return ""
    clean = str(name_str).lower()
    clean = re.sub(r"\b(and|or|with)\b", " ", clean)
    clean = clean.replace("&", " ").replace("+", " ").replace("/", " ")
    clean = re.sub(r"[^a-z0-9]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def parse_iso(ts_str):
    """Safely parse ISO string to datetime object."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        cleaned_str = ts_str.replace("Z", "+00:00").replace("T", " ")
        return datetime.fromisoformat(cleaned_str)
    except Exception:
        return None

def format_year_month(ts_str):
    """Extract Month and Year (e.g., 'Aug 2026') from ISO timestamp string."""
    dt = parse_iso(ts_str)
    return dt.strftime("%b %Y") if dt else "Unknown"

def format_calendar_year(ts_str):
    """Extract Calendar Year (e.g., '2026') from ISO timestamp string."""
    dt = parse_iso(ts_str)
    return dt.strftime("%Y") if dt else "Unknown"

def sec_to_hours(sec_val):
    """Convert integer seconds directly to hours rounded to 2 decimal places."""
    if sec_val is None:
        return None
    try:
        hours = float(sec_val) / 3600.0
        return round(hours, 2)
    except (ValueError, TypeError):
        return None

def load_realm_mapping(cursor):
    """
    Dynamically load team-to-realm mappings with exact and canonical normalized lookups.
    Zero hardcoded dictionaries used.
    """
    exact_mapping = {}
    normalized_mapping = {}
    try:
        cursor.execute("SELECT team_name, normalized_team_name, realm_name FROM service_realm_registry")
        rows = cursor.fetchall()
        for team, norm_team, realm in rows:
            if team and realm:
                exact_mapping[team.strip()] = realm.strip()
                normalized_mapping[norm_team.strip()] = realm.strip()
    except Exception:
        try:
            cursor.execute("SELECT team_name, realm_name FROM service_realm_registry")
            rows = cursor.fetchall()
            for team, realm in rows:
                if team and realm:
                    exact_mapping[team.strip()] = realm.strip()
                    normalized_mapping[canonical_normalize(team)] = realm.strip()
        except Exception as e:
            print(f"Notice: Could not query service_realm_registry table ({e}). Unknown teams will be marked Unmapped.")
            
    return exact_mapping, normalized_mapping

def init_wrangled_tables(cursor):
    """Initialize structured tables for realm grids, monthly metrics, YTD summaries, and data gaps."""
    cursor.execute("DROP TABLE IF EXISTS realm_incident_grid;")
    cursor.execute("""
        CREATE TABLE realm_incident_grid (
            incident_id VARCHAR(50),
            realm_name VARCHAR(100),
            status VARCHAR(50),
            severity VARCHAR(50),
            affected_team VARCHAR(255),
            affected_products VARCHAR(255),
            root_cause_services VARCHAR(255),
            year_month VARCHAR(20),
            calendar_year VARCHAR(4),
            mttd_hours REAL,
            mttr_hours REAL,
            duration_hours REAL,
            impact_started_at DATETIME
        );
    """)

    cursor.execute("DROP TABLE IF EXISTS monthly_realm_metrics;")
    cursor.execute("""
        CREATE TABLE monthly_realm_metrics (
            id VARCHAR(100) PRIMARY KEY,
            realm_name VARCHAR(100),
            calendar_year VARCHAR(4),
            year_month VARCHAR(20),
            ym_code VARCHAR(7),
            incident_count INTEGER DEFAULT 0,
            mean_mttd_hours REAL DEFAULT 0.0,
            median_mttd_hours REAL DEFAULT 0.0,
            mean_mttr_hours REAL DEFAULT 0.0,
            median_mttr_hours REAL DEFAULT 0.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("DROP TABLE IF EXISTS ytd_realm_metrics;")
    cursor.execute("""
        CREATE TABLE ytd_realm_metrics (
            id VARCHAR(100) PRIMARY KEY,
            realm_name VARCHAR(100),
            calendar_year VARCHAR(4),
            incident_count INTEGER DEFAULT 0,
            mean_mttd_hours REAL DEFAULT 0.0,
            median_mttd_hours REAL DEFAULT 0.0,
            mean_mttr_hours REAL DEFAULT 0.0,
            median_mttr_hours REAL DEFAULT 0.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("DROP TABLE IF EXISTS incident_data_gaps;")
    cursor.execute("""
        CREATE TABLE incident_data_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id VARCHAR(50),
            realm_name VARCHAR(100),
            incident_lead VARCHAR(255),
            missing_fields VARCHAR(500),
            status VARCHAR(50)
        );
    """)

def export_table_to_csv(cursor, query, csv_filename):
    """Helper function to dump SQLite queries directly into CSV files."""
    cursor.execute(query)
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    with open(csv_filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)

def wrangle_incidents():
    print("Starting Data Wrangling Engine...")
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file missing at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        exact_map, norm_map = load_realm_mapping(cursor)
        init_wrangled_tables(cursor)

        cursor.execute("""
            SELECT id, status, severity, impact_started_at, identified_at, fixed_at,
                   mttd, mttr, incident_duration, affected_team, root_cause_services,
                   affected_products, incident_lead
            FROM incidents
        """)
        raw_incidents = cursor.fetchall()

        if not raw_incidents:
            print("Notice: No raw incidents found in database.")
            conn.close()
            return

        grid_inserts = 0
        gap_inserts = 0

        for inc in raw_incidents:
            (inc_id, status, severity, impact_started_at, identified_at, fixed_at,
             mttd_sec, mttr_sec, duration_sec, affected_team_raw, root_cause_services,
             affected_products, incident_lead) = inc

            mttd_h = sec_to_hours(mttd_sec)
            mttr_h = sec_to_hours(mttr_sec)
            duration_h = sec_to_hours(duration_sec)

            year_month_str = format_year_month(impact_started_at)
            calendar_year_str = format_calendar_year(impact_started_at)
            dt_obj = parse_iso(impact_started_at)
            ym_code = dt_obj.strftime("%Y-%m") if dt_obj else "Unknown"

            # Parse Affected Teams
            teams_list = []
            if affected_team_raw and affected_team_raw != "Unassigned":
                raw_split = affected_team_raw.replace("|", ",").split(",")
                teams_list = [t.strip() for t in raw_split if t.strip()]
            
            if not teams_list:
                teams_list = ["Unassigned"]

            # Map Realm via Exact Match -> Canonical Normalized Match -> Unmapped Tag
            target_realms = set()
            for team in teams_list:
                norm_team = canonical_normalize(team)
                if team in exact_map:
                    realm = exact_map[team]
                elif norm_team in norm_map:
                    realm = norm_map[norm_team]
                else:
                    realm = f"Unmapped: {team}" if team != "Unassigned" else "Unassigned Realm"
                target_realms.add(realm)

            # Insert into Realm Data Grid
            for realm_name in target_realms:
                cursor.execute("""
                    INSERT INTO realm_incident_grid (
                        incident_id, realm_name, status, severity, affected_team,
                        affected_products, root_cause_services, year_month, calendar_year,
                        mttd_hours, mttr_hours, duration_hours, impact_started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    inc_id, realm_name, status, severity, affected_team_raw,
                    affected_products, root_cause_services, year_month_str, calendar_year_str,
                    mttd_h, mttr_h, duration_h, impact_started_at
                ))
                grid_inserts += 1

                # Data Gaps Audit
                missing = []
                if not severity: missing.append("Severity")
                if not affected_team_raw or affected_team_raw == "Unassigned": missing.append("Affected Team")
                if not affected_products: missing.append("Affected Products")
                if not root_cause_services: missing.append("Root Cause Services")
                if not impact_started_at: missing.append("Impact started at")
                if not identified_at: missing.append("Identified at")
                if not fixed_at: missing.append("Fixed at")
                if duration_h is None: missing.append("Incident duration")
                if mttd_h is None: missing.append("MTTD")
                if mttr_h is None: missing.append("MTTR")

                if missing and str(status).lower() == "closed":
                    cursor.execute("""
                        INSERT INTO incident_data_gaps (
                            incident_id, realm_name, incident_lead, missing_fields, status
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (inc_id, realm_name, incident_lead or "Unassigned", ", ".join(missing), status))
                    gap_inserts += 1

        # Aggregations per Realm, Calendar Year, and Month
        # Require ONLY valid mttd_hours AND mttr_hours to include in time metrics
        cursor.execute("""
            SELECT realm_name, calendar_year, strftime('%m', impact_started_at) as month_num,
                   mttd_hours, mttr_hours
            FROM realm_incident_grid
            WHERE realm_name NOT LIKE 'Unmapped%' 
              AND realm_name != 'Unassigned Realm'
              AND impact_started_at IS NOT NULL
              AND mttd_hours IS NOT NULL
              AND mttr_hours IS NOT NULL
        """)
        grid_records = cursor.fetchall()

        monthly_grouped = {}
        ytd_grouped = {}
        distinct_realms_years = set()

        for realm, cal_yr, month_num, mttd_h, mttr_h in grid_records:
            if not cal_yr or cal_yr == "Unknown" or not month_num:
                continue

            distinct_realms_years.add((realm, cal_yr))

            # Monthly Key
            m_key = (realm, cal_yr, month_num)
            if m_key not in monthly_grouped:
                monthly_grouped[m_key] = {"mttd": [], "mttr": []}
            monthly_grouped[m_key]["mttd"].append(mttd_h)
            monthly_grouped[m_key]["mttr"].append(mttr_h)

            # YTD Key
            y_key = (realm, cal_yr)
            if y_key not in ytd_grouped:
                ytd_grouped[y_key] = {"mttd": [], "mttr": []}
            ytd_grouped[y_key]["mttd"].append(mttd_h)
            ytd_grouped[y_key]["mttr"].append(mttr_h)

        # Fill Full Calendar Month Timeline (Jan - Dec)
        summary_count = 0
        for (realm_name, cal_yr) in distinct_realms_years:
            for m_int in range(1, 13):
                m_str = f"{m_int:02d}"
                ym_code = f"{cal_yr}-{m_str}"
                
                dt_temp = datetime(int(cal_yr), m_int, 1)
                year_month_label = dt_temp.strftime("%b %Y")

                m_key = (realm_name, cal_yr, m_str)
                metrics = monthly_grouped.get(m_key, {"mttd": [], "mttr": []})

                mttd_list = metrics["mttd"]
                mttr_list = metrics["mttr"]
                count = len(mttd_list)

                mean_mttd = round(statistics.mean(mttd_list), 2) if mttd_list else 0.0
                median_mttd = round(statistics.median(mttd_list), 2) if mttd_list else 0.0
                mean_mttr = round(statistics.mean(mttr_list), 2) if mttr_list else 0.0
                median_mttr = round(statistics.median(mttr_list), 2) if mttr_list else 0.0

                summary_id = f"{realm_name}_{ym_code}"

                cursor.execute("""
                    INSERT INTO monthly_realm_metrics (
                        id, realm_name, calendar_year, year_month, ym_code, incident_count,
                        mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    summary_id, realm_name, cal_yr, year_month_label, ym_code, count,
                    mean_mttd, median_mttd, mean_mttr, median_mttr
                ))
                summary_count += 1

        # Calculate YTD Summaries
        for (realm_name, cal_yr), metrics in ytd_grouped.items():
            mttd_list = metrics["mttd"]
            mttr_list = metrics["mttr"]
            count = len(mttd_list)

            mean_mttd = round(statistics.mean(mttd_list), 2) if mttd_list else 0.0
            median_mttd = round(statistics.median(mttd_list), 2) if mttd_list else 0.0
            mean_mttr = round(statistics.mean(mttr_list), 2) if mttr_list else 0.0
            median_mttr = round(statistics.median(mttr_list), 2) if mttr_list else 0.0

            ytd_id = f"{realm_name}_{cal_yr}_YTD"

            cursor.execute("""
                INSERT INTO ytd_realm_metrics (
                    id, realm_name, calendar_year, incident_count,
                    mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                ytd_id, realm_name, cal_yr, count,
                mean_mttd, median_mttd, mean_mttr, median_mttr
            ))

        conn.commit()

        # Export Clean CSV files
        export_table_to_csv(cursor, """
            SELECT incident_id as ID, 
                   realm_name as Realm, 
                   status as Status, 
                   severity as Severity, 
                   affected_team as "Affected Team", 
                   affected_products as "Affected Products", 
                   root_cause_services as "Root Cause Services", 
                   year_month as Year, 
                   mttd_hours as "MTTD (hours)", 
                   mttr_hours as "MTTR (hours)", 
                   duration_hours as "Incident Duration (hours)"
            FROM realm_incident_grid
        """, 'wrangled_realm_grid.csv')

        export_table_to_csv(cursor, "SELECT * FROM monthly_realm_metrics ORDER BY realm_name, ym_code", 'wrangled_monthly_metrics.csv')
        export_table_to_csv(cursor, "SELECT * FROM incident_data_gaps ORDER BY incident_id DESC", 'wrangled_data_gaps.csv')

        print(f"Wrangling Complete: Generated wrangled_realm_grid.csv ({grid_inserts} rows), wrangled_monthly_metrics.csv ({summary_count} rows), and wrangled_data_gaps.csv ({gap_inserts} rows).")

    except Exception as e:
        print(f"Error during data wrangling: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    wrangle_incidents()