import csv
import io
import json
import os
import re
import sqlite3
import urllib.request
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==============================================================================
# DYNAMIC CONFIGURATION & CONSTANTS
# ==============================================================================
DB_PATH = os.getenv("DB_PATH", os.path.join("database", "dora_metrics.db"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "public")
LANDING_PAGE_PATH = os.getenv("LANDING_PAGE_PATH", "../index.html")

# Dynamic Current Year and Rolling 3-Year Fallback
CURRENT_YEAR = datetime.now().year
DYNAMIC_YEAR_FALLBACKS = [str(CURRENT_YEAR - i) for i in range(3)]  # e.g., ['2026', '2025', '2024']

REALM_ICONS = {
    "Apps": "📱",
    "Core": "⚙️",
    "Machines Realm I - OS&FW": "🤖",
    "Machines Realm II - MSW & FH": "🤖",
    "Machine Realm": "🤖",
    "Wellpass": "💚",
    "Realm Support Services": "🛠️"
}

# Realms that do not produce deployments/CFR dashboards
NON_CFR_REALMS = {"Realm Support Services"}

TRACKED_GAPS_FIELDS = [
    "Impact started at", "Identified at", "Fixed at", 
    "Root Cause Services", "MTTD", "MTTR", "Incident duration", "Severity"
]
TRACKED_GAPS_MAP = {field.lower(): field for field in TRACKED_GAPS_FIELDS}

def filter_missing_fields(raw_missing_str):
    if not raw_missing_str:
        return ""
    fields = [f.strip() for f in str(raw_missing_str).split(",")]
    filtered = []
    for f in fields:
        canonical_name = TRACKED_GAPS_MAP.get(f.lower())
        if canonical_name:
            filtered.append(canonical_name)
    return ", ".join(filtered)

def load_template(filename):
    path = os.path.join("publisher", filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def inject_template_vars(template_content, variable_dict):
    output = template_content
    for key, val in variable_dict.items():
        val_str = str(val)
        pattern_double = re.compile(rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}", re.IGNORECASE)
        pattern_single = re.compile(rf"\{{\s*{re.escape(key)}\s*\}}", re.IGNORECASE)
        output = pattern_double.sub(lambda _: val_str, output)
        output = pattern_single.sub(lambda _: val_str, output)
    return output

def fetch_official_realm_teams_map():
    web_app_url = os.getenv("MAPPING_WEB_APP_URL", "").strip()
    teams_gid = os.getenv("SHEET_REALMS_TEAM_MAPPING_INCIDENTS_GID", "0").strip()
    
    if not web_app_url:
        print("WARNING: MAPPING_WEB_APP_URL not set in environment. Falling back to DB registry.")
        return {}
        
    try:
        if "script.google.com" in web_app_url:
            csv_url = f"{web_app_url}?gid={teams_gid}" if "?" not in web_app_url else web_app_url
        elif "docs.google.com/spreadsheets" in web_app_url:
            base_url = web_app_url.split('/edit')[0].split('#')[0]
            csv_url = f"{base_url}/export?format=csv&gid={teams_gid}"
        else:
            csv_url = web_app_url

        print(f"Fetching official realm-team mapping from: {csv_url}")
        req = urllib.request.Request(csv_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            raw_data = response.read().decode('utf-8')

        official_map = {}

        try:
            json_payload = json.loads(raw_data)
            if isinstance(json_payload, list):
                for item in json_payload:
                    r_name = str(item.get('Realms') or item.get('realm') or item.get('Realm') or '').strip()
                    t_name = str(item.get('Teams') or item.get('team') or item.get('Team') or '').strip()
                    if r_name and t_name:
                        official_map.setdefault(r_name, set()).add(t_name)
            elif isinstance(json_payload, dict):
                for r_name, teams in json_payload.items():
                    if isinstance(teams, list):
                        official_map[r_name] = set(teams)
            if official_map:
                return {r: sorted(list(teams)) for r, teams in official_map.items()}
        except Exception:
            pass

        reader = csv.reader(io.StringIO(raw_data))
        header = None
        for row in reader:
            clean_row = [c.strip().lower() for c in row]
            if any('realm' in c for c in clean_row) and any('team' in c for c in clean_row):
                header = clean_row
                break

        if not header:
            return {}

        realm_idx = next((i for i, c in enumerate(header) if 'realm' in c), 0)
        team_idx = next((i for i, c in enumerate(header) if 'team' in c), 1)

        for row in reader:
            if len(row) > max(realm_idx, team_idx):
                r_name = row[realm_idx].strip()
                t_name = row[team_idx].strip()
                if r_name and t_name and r_name.lower() != 'nan' and t_name.lower() != 'nan':
                    official_map.setdefault(r_name, set()).add(t_name)

        return {r: sorted(list(teams)) for r, teams in official_map.items()}

    except Exception as e:
        print(f"ERROR fetching dynamic realm-team mapping: {e}")
        return {}

def get_realm_metadata(cursor):
    cursor.execute("""
        SELECT DISTINCT realm_name 
        FROM monthly_realm_metrics 
        WHERE LOWER(realm_name) NOT LIKE 'unmapped%' 
          AND LOWER(realm_name) NOT LIKE 'unassigned%'
    """)
    realms = [r[0] for r in cursor.fetchall()]

    metadata = {}
    for r in realms:
        cursor.execute("SELECT COUNT(DISTINCT team_name) FROM service_realm_registry WHERE realm_name = ?", (r,))
        cnt_row = cursor.fetchone()
        team_count = cnt_row[0] if cnt_row and cnt_row[0] > 0 else 1

        slug = r.lower().replace(" ", "-").replace("&", "and")
        icon = REALM_ICONS.get(r, "📦")
        is_cfr_eligible = (r not in NON_CFR_REALMS)

        metadata[r] = {
            "slug": slug,
            "icon": icon,
            "teams": team_count,
            "cfr": is_cfr_eligible
        }
    return metadata

# ==============================================================================
# PART 1: MTTD & MTTR DASHBOARD GENERATION
# ==============================================================================
def generate_mttd_mttr_dashboards(conn, available_years, default_year, year_options_html, realm_meta):
    print("Generating MTTD & MTTR Dashboards...")
    template = load_template("mttd_mttr_template.html")
    cursor = conn.cursor()
    official_realm_teams = fetch_official_realm_teams_map()

    for realm, cfg in realm_meta.items():
        cursor.execute("""
            SELECT DISTINCT affected_team 
            FROM realm_incident_grid 
            WHERE LOWER(realm_name) = LOWER(?) 
                AND affected_team IS NOT NULL 
                AND affected_team != '' 
                AND LOWER(affected_team) != 'unassigned' 
        """, (realm,))
        active_team_rows = cursor.fetchall()

        incident_teams = set()
        for r in active_team_rows:
            raw_teams = str(r[0]).replace("|", ",").split(",")
            for t in raw_teams:
                clean_t = t.strip()
                if clean_t and clean_t.lower() != "unassigned":
                    incident_teams.add(clean_t)

        sheet_teams = []
        if official_realm_teams:
            for sheet_realm, teams in official_realm_teams.items():
                if sheet_realm.strip().lower() == realm.strip().lower():
                    sheet_teams = teams
                    break

        if sheet_teams:
            inc_team_map = {t.lower(): t for t in incident_teams}
            matched_teams = [inc_team_map[st.strip().lower()] for st in sheet_teams if st.strip().lower() in inc_team_map]
            active_realm_teams = sorted(matched_teams) if matched_teams else sorted(list(sheet_teams))
        else:
            active_realm_teams = sorted(list(incident_teams))

        mapped_teams_str = ", ".join(active_realm_teams) if active_realm_teams else f"All {realm} Teams"

        realm_data = {}
        for year in available_years:
            yr_str = str(year)

            cursor.execute("""
                SELECT ym_code, mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours
                FROM monthly_realm_metrics
                WHERE LOWER(realm_name) = LOWER(?) AND calendar_year = ?
                ORDER BY ym_code ASC
            """, (realm, yr_str))
            
            m_rows = cursor.fetchall()
            monthly_list = [{
                "month": r[0].split("-")[1] if "-" in r[0] else r[0],
                "mean_mttd": round(r[1] or 0.0, 1),
                "median_mttd": round(r[2] or 0.0, 1),
                "mean_mttr": round(r[3] or 0.0, 1),
                "median_mttr": round(r[4] or 0.0, 1)
            } for r in m_rows]

            cursor.execute("""
                SELECT incident_count, mean_mttd_hours, median_mttd_hours, mean_mttr_hours, median_mttr_hours
                FROM ytd_realm_metrics
                WHERE LOWER(realm_name) = LOWER(?) AND calendar_year = ?
            """, (realm, yr_str))
            
            y_row = cursor.fetchone()
            ytd_dict = {
                "incident_count": y_row[0] if y_row else 0,
                "mean_mttd": round(y_row[1] or 0.0, 1) if y_row else 0.0,
                "median_mttd": round(y_row[2] or 0.0, 1) if y_row else 0.0,
                "mean_mttr": round(y_row[3] or 0.0, 1) if y_row else 0.0,
                "median_mttr": round(y_row[4] or 0.0, 1) if y_row else 0.0
            } if y_row else {"incident_count": 0, "mean_mttd": 0, "median_mttd": 0, "mean_mttr": 0, "median_mttr": 0}

            cursor.execute("""
                SELECT root_cause_services, COUNT(*) 
                FROM realm_incident_grid 
                WHERE LOWER(realm_name) = LOWER(?) AND calendar_year = ? AND root_cause_services IS NOT NULL AND root_cause_services != ''
                GROUP BY root_cause_services
            """, (realm, yr_str))
            
            rc_rows = cursor.fetchall()
            root_causes = {}
            for r in rc_rows:
                raw_service = r[0]
                count = r[1]
                if "missing" in raw_service.lower() or "unmapped" in raw_service.lower() or "sre" in raw_service.lower():
                    clean_service = "Other / Service Unmapped"
                else:
                    clean_service = raw_service
                root_causes[clean_service] = root_causes.get(clean_service, 0) + count

            cursor.execute("""
                SELECT DISTINCT incident_id, incident_lead, missing_fields, realm_name 
                FROM incident_data_gaps 
                WHERE (
                LOWER(realm_name) = LOWER(?) 
                OR realm_name IS NULL
                OR LOWER(realm_name) LIKE '%unassigned%'
                OR LOWER(realm_name) LIKE '%unmapped%'
                OR realm_name = ''
                )
            """, (realm,))
            
            g_rows = cursor.fetchall()
            gaps_list = []
            for r in g_rows:
                filtered_missing = filter_missing_fields(r[2])
                if filtered_missing:
                    gaps_list.append({
                        "incident_id": r[0],
                        "lead": r[1] if r[1] else "Unassigned",
                        "missing": filtered_missing,
                        "realm": r[3] if r[3] else "Unassigned Realm"
                    })

            realm_data[yr_str] = {
                "monthly": monthly_list,
                "ytd": ytd_dict,
                "root_causes": root_causes,
                "gaps": gaps_list
            }

        replacements = {
            "realm_name": realm,
            "mapped_teams": mapped_teams_str,
            "landing_page_path": LANDING_PAGE_PATH,
            "year_options_html": year_options_html,
            "default_year": default_year,
            "payload_json": json.dumps(realm_data)
        }
        html_content = inject_template_vars(template, replacements)

        out_path = os.path.join(OUTPUT_DIR, f"{cfg['slug']}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Generated MTTD/MTTR Dashboard: {out_path}")

# ==============================================================================
# PART 2: CFR & DEPLOYMENT DASHBOARD GENERATION
# ==============================================================================
def generate_cfr_dashboards(conn, realm_meta):
    cursor = conn.cursor()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "cfr_dr_template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    official_realm_teams = fetch_official_realm_teams_map()

    # Dynamic Years derived directly from DB
    cursor.execute("""
        SELECT DISTINCT substr(time_bucket, 1, 4) AS yr 
        FROM monthly_df_cfr 
        WHERE total_deployments > 0 
        ORDER BY yr DESC;
    """)
    cfr_years = [r[0] for r in cursor.fetchall() if r[0]]
    if not cfr_years:
        cfr_years = DYNAMIC_YEAR_FALLBACKS

    cfr_default_year = cfr_years[0]
    cfr_year_options_html = "".join([f'<option value="{y}">{y}</option>' for y in cfr_years])

    for realm in realm_meta.keys():
        clean_realm = re.split(r'[-–]', realm)[0].strip()
        
        teams_list = []
        if official_realm_teams:
            for r_key, t_list in official_realm_teams.items():
                if clean_realm.lower() in r_key.lower():
                    teams_list = t_list
                    break
        
        # FIXED: Dynamic generic team fallback string per realm
        mapped_teams_str = ", ".join(teams_list) if teams_list else f"All {clean_realm} Teams"
        
        cfr_payload = {}
        for year in cfr_years:
            yr_str = str(year)

            cursor.execute("""
                SELECT 
                    SUM(total_deployments), 
                    SUM(successful_deployments), 
                    SUM(failed_deployments)
                FROM monthly_df_cfr
                WHERE (LOWER(realm_name) = LOWER(?) OR LOWER(realm_name) LIKE LOWER(?)) 
                  AND time_bucket LIKE ?
            """, (realm, f"{clean_realm}%", f"{yr_str}%"))
            
            row = cursor.fetchone()
            if row and row[0] and row[0] > 0:
                total_cnt = row[0]
                succ_cnt = row[1] or 0
                fail_cnt = row[2] or 0
            else:
                cursor.execute("""
                    SELECT LOWER(status), COUNT(*)
                    FROM deployments
                    WHERE (LOWER(realm_name) = LOWER(?) OR LOWER(realm_name) LIKE LOWER(?)) 
                      AND deployed_at LIKE ?
                    GROUP BY LOWER(status)
                """, (realm, f"{clean_realm}%", f"{yr_str}%"))
                tot_rows = dict(cursor.fetchall())
                succ_cnt = tot_rows.get("success", 0)
                fail_cnt = tot_rows.get("failure", 0) + tot_rows.get("failed", 0)
                total_cnt = succ_cnt + fail_cnt

            cfr_pct = f"{((fail_cnt / total_cnt) * 100):.1f}%" if total_cnt > 0 else "0.0%"

            cursor.execute("""
                SELECT group_name, SUM(successful_deployments), SUM(failed_deployments), SUM(total_deployments)
                FROM monthly_df_cfr
                WHERE (LOWER(realm_name) = LOWER(?) OR LOWER(realm_name) LIKE LOWER(?)) 
                  AND time_bucket LIKE ?
                GROUP BY group_name
            """, (realm, f"{clean_realm}%", f"{yr_str}%"))
            team_rows = cursor.fetchall()

            teams_dict = {}
            for g_name, s_cnt, f_cnt, t_cnt in team_rows:
                if not g_name: 
                    continue
                teams_dict[g_name] = {
                    "success": s_cnt or 0,
                    "failure": f_cnt or 0,
                    "total": t_cnt or 0
                }

            cursor.execute("""
                SELECT DISTINCT `Issue key`, Summary, Components, Resolved 
                FROM jira_releases_raw 
                WHERE (Components IS NULL OR Components = '' OR LOWER(Components) LIKE '%unmapped%')
                  AND Resolved LIKE ?
            """, (f"{yr_str}%",))
            
            unmapped_rows = cursor.fetchall()
            unmapped_list = [{
                "key": r[0] or "N/A",
                "summary": r[1] or "No Summary",
                "component": r[2] or "Unmapped",
                "resolved": r[3] or ""
            } for r in unmapped_rows]

            cfr_payload[yr_str] = {
                "total": total_cnt,
                "success": succ_cnt,
                "failure": fail_cnt,
                "cfr_pct": cfr_pct,
                "teams": teams_dict,
                "unmapped": unmapped_list
            }

        replacements = {
            "realm_name": clean_realm,
            "mapped_teams": mapped_teams_str,
            "landing_page_path": LANDING_PAGE_PATH,
            "year_options_html": cfr_year_options_html,
            "default_year": cfr_default_year,
            "payload_json": json.dumps(cfr_payload)
        }
        html_out = inject_template_vars(template, replacements)

        slug = clean_realm.lower().replace(" ", "-").replace("&", "and")
        out_path = os.path.join(OUTPUT_DIR, f"{slug}-cfr-dashboard.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"Generated CFR Dashboard: {out_path}")

# ==============================================================================
# LANDING PAGE GENERATION
# ==============================================================================
def generate_landing_page(realm_meta, latest_month_str):
    print("Generating Master Landing Page (index.html)...")
    landing_template = load_template("landing_template.html")
    # Section 2 Cards: MTTD & MTTR (Part 1)
    incident_cards_html = ""
    cfr_cards_html = ""

    for realm_name, cfg in realm_meta.items():
        incident_cards_html += f'''
        <a href="public/{cfg['slug']}.html" class="block bg-slate-800 p-6 rounded-xl border border-slate-700 hover:border-sky-400 transition-colors">
            <h3 class="text-xl font-bold text-sky-400">{cfg['icon']} {realm_name}</h3>
            <p class="text-sm text-slate-400 mt-2">{cfg['teams']} teams · Latest: {latest_month_str}</p>
            <div class="mt-4 flex items-center text-xs text-sky-300 font-semibold">Open Dashboard &rarr;</div>
        </a>'''
        # Section 3 Cards: CFR & Defect Rate (Part 2)
        if cfg["cfr"]:
            cfr_cards_html += f'''
            <a href="public/{cfg['slug']}-cfr-dashboard.html" class="block bg-slate-800 p-6 rounded-xl border border-slate-700 hover:border-sky-400 transition-colors">
                <h3 class="text-xl font-bold text-sky-400">{cfg['icon']} {realm_name}</h3>
                <p class="text-sm text-slate-400 mt-2">{cfg['teams']} teams · Latest: {latest_month_str}</p>
                <div class="mt-4 flex items-center text-xs text-sky-300 font-semibold">Open Dashboard &rarr;</div>
            </a>'''
    # Replaces ONLY DORA placeholders, leaving Section 1 (Flow Metrics) untouched
    replacements = {
        "INCIDENT_CARDS": incident_cards_html,
        "CFR_CARDS": cfr_cards_html
    }

    landing_html = inject_template_vars(landing_template, replacements)
    # Output directly to root index.html
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(landing_html)
    print("Master Landing Page index.html Generated Successfully!")

# ==============================================================================
# MAIN ORCHESTRATOR
# ==============================================================================
def generate_site():
    print("Starting DORA Site Generator...")
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file missing at {DB_PATH}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT calendar_year 
        FROM monthly_realm_metrics 
        WHERE calendar_year IS NOT NULL AND LOWER(calendar_year) != 'unknown' 
        ORDER BY calendar_year DESC
    """)
    available_years = [str(r[0]) for r in cursor.fetchall()]

    if not available_years:
        available_years = DYNAMIC_YEAR_FALLBACKS

    # FIXED: Picks the latest year available in DB automatically without hardcoding specific year numbers
    cursor.execute("""
        SELECT DISTINCT calendar_year 
        FROM monthly_realm_metrics 
        WHERE incident_count > 0
        ORDER BY calendar_year DESC
    """)
    active_year_row = cursor.fetchone()
    default_year = str(active_year_row[0]) if active_year_row else available_years[0]

    year_options_html = "".join([
        f'<option value="{yr}" {"selected" if yr == default_year else ""}>{yr}</option>\n' 
        for yr in available_years
    ])

    latest_month_str = datetime.now().strftime("%b %y")
    realm_meta = get_realm_metadata(cursor)

    generate_mttd_mttr_dashboards(conn, available_years, default_year, year_options_html, realm_meta)
    generate_cfr_dashboards(conn, realm_meta)
    generate_landing_page(realm_meta, latest_month_str)

    conn.close()
    print("Complete Site Generation Finished!")

if __name__ == "__main__":
    generate_site()