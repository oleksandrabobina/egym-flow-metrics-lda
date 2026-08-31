import json
import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", os.path.join("database", "dora_metrics.db"))
OUTPUT_DIR = "public"
LANDING_PAGE_PATH = "../index.html"

# Realm visual icon mappings
REALM_ICONS = {
    "Apps": "📱",
    "Core": "⚙️",
    "Machines Realm I - OS&FW": "🤖",
    "Machines Realm II - MSW & FH": "🤖",
    "Machine Realm": "🤖",
    "Wellpass": "💚",
    "Realm Support Services": "🛠️"
}

TRACKED_GAPS_FIELDS = [
    "Impact Started at", "Identified at", "Impact Fixed at", 
    "Root Cause Services", "MTTD", "MTTR", "Incident Duration", "Severity"
]

def get_table_columns(cursor, table_name):
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]
    except Exception:
        return []

def filter_missing_fields(raw_missing_str):
    if not raw_missing_str:
        return ""
    fields = [f.strip() for f in str(raw_missing_str).split(",")]
    filtered = [f for f in fields if f in TRACKED_GAPS_FIELDS]
    return ", ".join(filtered)

def load_template(filename):
    path = os.path.join("publisher", filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def get_realm_metadata(cursor):
    """
    Dynamically pulls all active realms and team counts straight from SQLite.
    Automatically scales when new teams or realms are added in the DB feed.
    """
    cursor.execute("SELECT DISTINCT realm_name FROM monthly_realm_metrics WHERE realm_name NOT LIKE 'Unmapped%'")
    realms = [r[0] for r in cursor.fetchall()]

    metadata = {}
    for r in realms:
        cursor.execute("SELECT COUNT(DISTINCT team_name) FROM service_realm_registry WHERE realm_name = ?", (r,))
        cnt_row = cursor.fetchone()
        team_count = cnt_row[0] if cnt_row and cnt_row[0] > 0 else 1

        slug = r.lower().replace(" ", "-").replace("&", "and")
        icon = REALM_ICONS.get(r, "📦") # Fallback icon for any new future realm
        is_cfr_eligible = (r != "Realm Support Services") # Business exclusion rule for Part 2

        metadata[r] = {
            "slug": slug,
            "icon": icon,
            "teams": team_count,
            "cfr": is_cfr_eligible
        }
    return metadata

def generate_mttd_mttr_dashboards(conn, available_years, default_year, year_options_html, realm_meta):
    print("Generating MTTD & MTTR Dashboards...")
    template = load_template("mttd_mttr_template.html")
    cursor = conn.cursor()

    monthly_cols = get_table_columns(cursor, "monthly_realm_metrics")
    grid_cols = get_table_columns(cursor, "realm_incident_grid")
    gaps_cols = get_table_columns(cursor, "incident_data_gaps")

    year_col_monthly = "calendar_year" if "calendar_year" in monthly_cols else ("year" if "year" in monthly_cols else None)
    year_col_grid = "calendar_year" if "calendar_year" in grid_cols else ("year" if "year" in grid_cols else None)
    year_col_gaps = "calendar_year" if "calendar_year" in gaps_cols else ("year" if "year" in gaps_cols else None)

    for realm, cfg in realm_meta.items():
        cursor.execute("SELECT team_name FROM service_realm_registry WHERE realm_name = ?", (realm,))
        teams_list = [t[0] for t in cursor.fetchall()]
        mapped_teams_str = ", ".join(teams_list) if teams_list else realm

        realm_data = {}
        for year in available_years:
            if year_col_monthly:
                cursor.execute(f"""
                    SELECT strftime('%m', ym_code || '-01') as month_num, mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours
                    FROM monthly_realm_metrics
                    WHERE realm_name = ? AND {year_col_monthly} = ?
                    ORDER BY ym_code ASC
                """, (realm, year))
            else:
                cursor.execute("""
                    SELECT strftime('%m', ym_code || '-01') as month_num, mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours
                    FROM monthly_realm_metrics
                    WHERE realm_name = ? AND ym_code LIKE ?
                    ORDER BY ym_code ASC
                """, (realm, f"{year}-%"))
            
            m_rows = cursor.fetchall()
            monthly_list = [{
                "month": r[0],
                "mean_mttd": r[1] or 0.0,
                "median_mttd": r[2] or 0.0,
                "mean_mttr": r[3] or 0.0,
                "median_mttr": r[4] or 0.0
            } for r in m_rows]

            if year_col_monthly:
                cursor.execute(f"""
                    SELECT incident_count, mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours
                    FROM ytd_realm_metrics
                    WHERE realm_name = ? AND {year_col_monthly} = ?
                """, (realm, year))
            else:
                cursor.execute("""
                    SELECT incident_count, mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours
                    FROM ytd_realm_metrics
                    WHERE realm_name = ? AND year = ?
                """, (realm, year))
            
            y_row = cursor.fetchone()
            ytd_dict = {
                "incident_count": y_row[0] if y_row else 0,
                "mean_mttd": y_row[1] if y_row else 0.0,
                "median_mttd": y_row[2] if y_row else 0.0,
                "mean_mttr": y_row[3] if y_row else 0.0,
                "median_mttr": y_row[4] if y_row else 0.0
            } if y_row else {"incident_count": 0, "mean_mttd": 0, "median_mttd": 0, "mean_mttr": 0, "median_mttr": 0}

            if year_col_grid:
                cursor.execute(f"""
                    SELECT root_cause_services, COUNT(*) 
                    FROM realm_incident_grid 
                    WHERE realm_name = ? AND {year_col_grid} = ? AND root_cause_services IS NOT NULL AND root_cause_services != ''
                    GROUP BY root_cause_services
                """, (realm, year))
            else:
                cursor.execute("""
                    SELECT root_cause_services, COUNT(*) 
                    FROM realm_incident_grid 
                    WHERE realm_name = ? AND root_cause_services IS NOT NULL AND root_cause_services != ''
                    GROUP BY root_cause_services
                """, (realm,))
            
            rc_rows = cursor.fetchall()
            root_causes = {}
            for r in rc_rows:
                raw_service = r[0]
                count = r[1]
                if "missing in list" in raw_service.lower() or "ping sre" in raw_service.lower():
                    clean_service = "Other / Service Unmapped"
                else:
                    clean_service = raw_service
                root_causes[clean_service] = root_causes.get(clean_service, 0) + count

            if year_col_gaps:
                cursor.execute(f"""
                    SELECT DISTINCT incident_id, incident_lead, missing_fields 
                    FROM incident_data_gaps 
                    WHERE realm_name = ? AND {year_col_gaps} = ?
                """, (realm, year))
            else:
                cursor.execute("""
                    SELECT DISTINCT incident_id, incident_lead, missing_fields 
                    FROM incident_data_gaps 
                    WHERE realm_name = ?
                """, (realm,))
            
            g_rows = cursor.fetchall()
            gaps_list = []
            for r in g_rows:
                filtered_missing = filter_missing_fields(r[2])
                if filtered_missing:
                    gaps_list.append({
                        "incident_id": r[0],
                        "lead": r[1] if r[1] else "Unassigned",
                        "missing": filtered_missing
                    })

            realm_data[year] = {
                "monthly": monthly_list,
                "ytd": ytd_dict,
                "root_causes": root_causes,
                "gaps": gaps_list
            }

        # Safe string replacement (avoids KeyError on JS braces)
        html_content = template.replace("{realm_name}", realm)
        html_content = html_content.replace("{mapped_teams}", mapped_teams_str)
        html_content = html_content.replace("{landing_page_path}", LANDING_PAGE_PATH)
        html_content = html_content.replace("{year_options_html}", year_options_html)
        html_content = html_content.replace("{default_year}", default_year)
        html_content = html_content.replace("{payload_json}", json.dumps(realm_data))

        out_path = os.path.join(OUTPUT_DIR, f"{cfg['slug']}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Generated MTTD/MTTR Dashboard: {out_path}")

def generate_cfr_dashboards(conn, available_years, default_year, year_options_html, realm_meta):
    print("Generating CFR Dashboards...")
    template = load_template("cfr_dr_template.html")
    cursor = conn.cursor()

    for realm, cfg in realm_meta.items():
        if not cfg["cfr"]:
            continue

        cursor.execute("SELECT team_name FROM service_realm_registry WHERE realm_name = ?", (realm,))
        teams_list = [t[0] for t in cursor.fetchall()]
        mapped_teams_str = ", ".join(teams_list) if teams_list else realm

        cfr_payload = {}
        for year in available_years:
            try:
                cursor.execute("""
                    SELECT success_status, COUNT(*) 
                    FROM wrangled_cfr_deployments 
                    WHERE realm_name = ? AND calendar_year = ?
                    GROUP BY success_status
                """, (realm, year))
                tot_rows = dict(cursor.fetchall())
            except Exception:
                tot_rows = {}

            succ_cnt = tot_rows.get('Success', 0)
            fail_cnt = tot_rows.get('Failure', 0)
            total_cnt = succ_cnt + fail_cnt
            cfr_pct = f"{((fail_cnt / total_cnt) * 100):.1f}%" if total_cnt > 0 else "0.0%"

            try:
                cursor.execute("""
                    SELECT team_name, success_status, COUNT(*) 
                    FROM wrangled_cfr_deployments 
                    WHERE realm_name = ? AND calendar_year = ?
                    GROUP BY team_name, success_status
                """, (realm, year))
                team_rows = cursor.fetchall()
            except Exception:
                team_rows = []

            teams_dict = {}
            for t_name, status, cnt in team_rows:
                if t_name not in teams_dict:
                    teams_dict[t_name] = {"success": 0, "failure": 0, "total": 0}
                if status == 'Success':
                    teams_dict[t_name]["success"] += cnt
                else:
                    teams_dict[t_name]["failure"] += cnt
                teams_dict[t_name]["total"] += cnt

            try:
                cursor.execute("""
                    SELECT issue_key, summary, component_name, resolved_date 
                    FROM unmapped_jira_releases 
                    WHERE realm_name = ? AND calendar_year = ?
                """, (realm, year))
                unmapped_rows = cursor.fetchall()
            except Exception:
                unmapped_rows = []

            unmapped_list = [{
                "key": r[0],
                "summary": r[1],
                "component": r[2] or "Unmapped",
                "resolved": r[3] or "N/A"
            } for r in unmapped_rows]

            cfr_payload[year] = {
                "total": total_cnt,
                "success": succ_cnt,
                "failure": fail_cnt,
                "cfr_pct": cfr_pct,
                "teams": teams_dict,
                "unmapped": unmapped_list
            }

        # Safe string replacement (avoids KeyError on JS braces)
        html_out = template.replace("{realm_name}", realm)
        html_out = html_out.replace("{mapped_teams}", mapped_teams_str)
        html_out = html_out.replace("{landing_page_path}", LANDING_PAGE_PATH)
        html_out = html_out.replace("{year_options_html}", year_options_html)
        html_out = html_out.replace("{default_year}", default_year)
        html_out = html_out.replace("{payload_json}", json.dumps(cfr_payload))

        slug = realm.lower().replace(" ", "-").replace("&", "and")
        out_path = os.path.join(OUTPUT_DIR, f"{slug}-cfr-dashboard.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"Generated CFR Dashboard: {out_path}")

def generate_landing_page(realm_meta, latest_month_str):
    print("Generating Master Landing Page (index.html)...")
    landing_template = load_template("landing_template.html")

    flow_cards_html = ""
    incident_cards_html = ""
    cfr_cards_html = ""

    for realm_name, cfg in realm_meta.items():
        # SECTION 1: FLOW METRICS (Preserves colleague's repository structure: apps/, core/, etc.)
        flow_cards_html += f'''
        <a href="{cfg['slug']}/" class="block bg-slate-800 p-6 rounded-xl border border-slate-700 hover:border-sky-400 transition-colors">
            <h3 class="text-xl font-bold text-sky-400">{cfg['icon']} {realm_name}</h3>
            <p class="text-sm text-slate-400 mt-2">{cfg['teams']} teams · Latest: {latest_month_str}</p>
            <div class="mt-4 flex items-center text-xs text-sky-300 font-semibold">Open Dashboard &rarr;</div>
        </a>'''

        # SECTION 2: DORA INCIDENTS - PART 1 (Points strictly to public/ DORA files)
        incident_cards_html += f'''
        <a href="public/{cfg['slug']}.html" class="block bg-slate-800 p-6 rounded-xl border border-slate-700 hover:border-sky-400 transition-colors">
            <h3 class="text-xl font-bold text-sky-400">{cfg['icon']} {realm_name}</h3>
            <p class="text-sm text-slate-400 mt-2">{cfg['teams']} teams · Latest: {latest_month_str}</p>
            <div class="mt-4 flex items-center text-xs text-sky-300 font-semibold">Open Dashboard &rarr;</div>
        </a>'''

        # SECTION 3: DORA CFR - PART 2 (Points strictly to public/ CFR files)
        if cfg["cfr"]:
            cfr_cards_html += f'''
            <a href="public/{cfg['slug']}-cfr-dashboard.html" class="block bg-slate-800 p-6 rounded-xl border border-slate-700 hover:border-sky-400 transition-colors">
                <h3 class="text-xl font-bold text-sky-400">{cfg['icon']} {realm_name}</h3>
                <p class="text-sm text-slate-400 mt-2">{cfg['teams']} teams · Latest: {latest_month_str}</p>
                <div class="mt-4 flex items-center text-xs text-sky-300 font-semibold">Open Dashboard &rarr;</div>
            </a>'''

    landing_html = landing_template.replace("{{FLOW_CARDS}}", flow_cards_html)
    landing_html = landing_html.replace("{{INCIDENT_CARDS}}", incident_cards_html)
    landing_html = landing_html.replace("{{CFR_CARDS}}", cfr_cards_html)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(landing_html)
    print("Master Landing Page index.html Generated Successfully!")

def generate_site():
    print("Starting DORA Site Generator...")
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file missing at {DB_PATH}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    monthly_cols = get_table_columns(cursor, "monthly_realm_metrics")
    year_col = "calendar_year" if "calendar_year" in monthly_cols else ("year" if "year" in monthly_cols else None)

    if year_col:
        cursor.execute(f"SELECT DISTINCT {year_col} FROM monthly_realm_metrics WHERE {year_col} IS NOT NULL AND {year_col} != 'Unknown' ORDER BY {year_col} DESC")
        available_years = [str(r[0]) for r in cursor.fetchall()]
    else:
        available_years = ["2026", "2025", "2024"]

    if not available_years:
        available_years = ["2026", "2025", "2024"]

    default_year = available_years[0]
    year_options_html = "".join([f'<option value="{yr}" {"selected" if yr == default_year else ""}>{yr}</option>\n' for yr in available_years])

    latest_month_str = datetime.now().strftime("%b %y")
    realm_meta = get_realm_metadata(cursor)

    generate_mttd_mttr_dashboards(conn, available_years, default_year, year_options_html, realm_meta)
    generate_cfr_dashboards(conn, available_years, default_year, year_options_html, realm_meta)
    generate_landing_page(realm_meta, latest_month_str)

    conn.close()
    print("Complete Site Generation Finished!")

if __name__ == "__main__":
    generate_site()