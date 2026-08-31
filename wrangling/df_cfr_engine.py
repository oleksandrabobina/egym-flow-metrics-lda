import csv
import json
import os
import sqlite3
import urllib.request
from datetime import datetime

try:
  from dotenv import load_dotenv

  load_dotenv()
except ImportError:
  pass

# Environment & Storage Config (100% Dynamic & Scalable)
DB_PATH = os.getenv("DB_PATH", os.path.join("database", "dora_metrics.db"))
MAPPING_WEB_APP_URL = os.getenv("MAPPING_WEB_APP_URL")

INPUT_GH_CSV = os.getenv("INPUT_GH_CSV", "inspected_service_inventory.csv")
INPUT_JIRA_CSV = os.getenv("INPUT_JIRA_CSV", "inspected_jira_releases.csv")

OUTPUT_WRANGLED_GH = "wrangled_github_deployments.csv"
OUTPUT_WRANGLED_JIRA = "wrangled_jira_releases.csv"
OUTPUT_CONSOLIDATED = "wrangled_consolidated_workings.csv"


def fetch_live_mappings(web_app_url):
  """Dynamically fetches live service and component mappings directly from Google Web App Endpoint.

  Parses any top-level JSON structure (Lists/Dicts/Multi-tab payloads) and
  matches column headers flexibly.
  """
  if not web_app_url:
    print(
        "CRITICAL WARNING: MAPPING_WEB_APP_URL is not set in .env! All items"
        " will be tagged 'Unassigned'."
    )
    return {}, {}

  try:
    print(f"Fetching live mappings from Google Web App: {web_app_url}...")
    req = urllib.request.Request(
        web_app_url, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
      payload = json.loads(response.read().decode("utf-8"))

    service_map = {}
    jira_component_map = {}

    def extract_item(item_dict):
      """Extracts service/component, team, and realm regardless of exact key casing/formatting."""
      if not isinstance(item_dict, dict):
        return None, None, None, None

      # Normalize dictionary keys (remove spaces, underscores, lower-case)
      norm = {
          k.lower().replace(" ", "").replace("_", ""): str(v).strip()
          for k, v in item_dict.items()
          if v is not None
      }

      svc = (
          norm.get("serviceinventory")
          or norm.get("service")
          or norm.get("servicename")
          or norm.get("repository")
          or norm.get("repo")
      )
      comp = (
          norm.get("jiracomponent")
          or norm.get("component")
          or norm.get("components")
          or norm.get("jira")
      )

      # Handle both singular and plural forms (Team vs Teams, Realm vs Realms)
      team = (
          norm.get("team")
          or norm.get("teams")
          or norm.get("group")
          or norm.get("groups")
          or norm.get("teamname")
          or "Unassigned"
      )
      realm = (
          norm.get("realm")
          or norm.get("realms")
          or norm.get("realmname")
          or "Unassigned"
      )

      return svc, comp, team, realm

    def process_entry(entry):
      svc, comp, team, realm = extract_item(entry)
      if svc:
        service_map[svc] = (team, realm)
      if comp:
        jira_component_map[comp] = (team, realm)

    # Case A: Top-level JSON list of dictionaries
    if isinstance(payload, list):
      for entry in payload:
        process_entry(entry)

    # Case B: Top-level JSON dictionary (multi-tab array or key-based maps)
    elif isinstance(payload, dict):
      for key, val in payload.items():
        if isinstance(val, list):
          for entry in val:
            process_entry(entry)
        elif isinstance(val, dict):
          process_entry(val)

    print(
        f"Successfully loaded {len(service_map)} GitHub Service Mappings and"
        f" {len(jira_component_map)} Jira Component Mappings live!"
    )
    return service_map, jira_component_map

  except Exception as e:
    print(
        f"ERROR: Failed to fetch live mappings from Web App ({e}). Falling"
        " back to empty dynamic maps."
    )
    return {}, {}


def setup_database_tables(cursor):
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT,
            team_name TEXT,
            realm_name TEXT,
            deployed_at TEXT,
            status TEXT,
            source TEXT
        );
    """)

  cursor.execute("DROP VIEW IF EXISTS v_change_failure_consolidated;")
  cursor.execute("""
        CREATE VIEW v_change_failure_consolidated AS
        SELECT 
            team_name AS group_name,
            realm_name,
            deployed_at,
            status,
            source
        FROM deployments;
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS monthly_df_cfr (
            id VARCHAR(100) PRIMARY KEY,
            realm_name VARCHAR(100),
            group_name VARCHAR(100),
            time_frame VARCHAR(20),
            time_bucket VARCHAR(20),
            total_deployments INTEGER DEFAULT 0,
            successful_deployments INTEGER DEFAULT 0,
            failed_deployments INTEGER DEFAULT 0,
            cfr_percent REAL DEFAULT 0.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)


def compute_df_and_cfr():
  print("Executing Fully Dynamic DF & CFR Engine...")

  # Fetch mappings dynamically from MAPPING_WEB_APP_URL
  service_inventory_map, jira_component_map = fetch_live_mappings(
      MAPPING_WEB_APP_URL
  )

  jira_csv = INPUT_JIRA_CSV
  github_csv = INPUT_GH_CSV

  if not os.path.exists(github_csv):
    print(f"Error: Required raw CSV logbook file ({github_csv}) not found.")
    return

  # 1. Map Jira Release Ticket Data Dynamically
  wrangled_jira_rows = []
  jira_stack_rows = []

  if os.path.exists(jira_csv):
    with open(jira_csv, mode="r", encoding="utf-8") as f:
      reader = csv.DictReader(f)
      for row in reader:
        comp = row.get("Components", "").strip()
        team, realm = jira_component_map.get(
            comp, ("Unassigned", "Unassigned")
        )

        raw_status = row.get("Status", "").strip().lower()
        success = (
            "Success"
            if raw_status in ["done", "released", "closed", "resolved"]
            else "Failure"
        )

        resolved_date = row.get("Resolved", "").strip()
        formatted_date = ""
        parsed_dt = None
        if resolved_date:
          try:
            parsed_dt = datetime.strptime(resolved_date, "%Y-%m-%d")
            formatted_date = parsed_dt.strftime("%d-%m-%Y")
          except ValueError:
            pass

        wrangled_jira_rows.append({
            "Issue Type": row.get("Issue Type", ""),
            "Issue key": row.get("Issue key", ""),
            "Summary": row.get("Summary", ""),
            "Components": comp,
            "Team": team,
            "Realm": realm,
            "Date": formatted_date,
            "Status": row.get("Status", ""),
            "Success": success,
        })

        if parsed_dt:
          jira_stack_rows.append({
              "Group": team,
              "Realm": realm,
              "Date": formatted_date,
              "Success": success,
              "Year": parsed_dt.year,
              "Month": parsed_dt.month,
              "IsoDate": parsed_dt.strftime("%Y-%m-%d"),
          })

    with open(
        OUTPUT_WRANGLED_JIRA, mode="w", newline="", encoding="utf-8"
    ) as f:
      fieldnames = [
          "Issue Type",
          "Issue key",
          "Summary",
          "Components",
          "Team",
          "Realm",
          "Date",
          "Status",
          "Success",
      ]
      writer = csv.DictWriter(f, fieldnames=fieldnames)
      writer.writeheader()
      writer.writerows(wrangled_jira_rows)

  # 2. Map GitHub Deployment Data Dynamically
  wrangled_gh_rows = []
  gh_stack_rows = []

  with open(github_csv, mode="r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
      service = row.get("Service", "").strip()
      team, realm = service_inventory_map.get(
          service, ("Unassigned", "Unassigned")
      )

      raw_success = str(row.get("Rollout Success", "")).strip().lower()
      success = (
          "Success" if raw_success in ["true", "1", "success"] else "Failure"
      )

      deploy_date = row.get("Deployment Date", "").strip()
      formatted_date = ""
      parsed_dt = None
      if deploy_date:
        try:
          parsed_dt = datetime.strptime(deploy_date, "%d.%m.%Y")
          formatted_date = parsed_dt.strftime("%d-%m-%Y")
        except ValueError:
          pass

      wrangled_gh_rows.append({
          "Service": service,
          "Team": team,
          "Realm": realm,
          "Date": formatted_date,
          "Rollout Success": row.get("Rollout Success", ""),
          "Success": success,
      })

      if parsed_dt:
        gh_stack_rows.append({
            "Group": team,
            "Realm": realm,
            "Date": formatted_date,
            "Success": success,
            "Year": parsed_dt.year,
            "Month": parsed_dt.month,
            "IsoDate": parsed_dt.strftime("%Y-%m-%d"),
        })

  with open(OUTPUT_WRANGLED_GH, mode="w", newline="", encoding="utf-8") as f:
    fieldnames = [
        "Service",
        "Team",
        "Realm",
        "Date",
        "Rollout Success",
        "Success",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(wrangled_gh_rows)

  # 3. Consolidate Streams
  consolidated_rows = gh_stack_rows + jira_stack_rows

  with open(OUTPUT_CONSOLIDATED, mode="w", newline="", encoding="utf-8") as f:
    fieldnames = ["Group", "Realm", "Date", "Success", "Year", "Month"]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for r in consolidated_rows:
      writer.writerow({
          "Group": r["Group"],
          "Realm": r["Realm"],
          "Date": r["Date"],
          "Success": r["Success"],
          "Year": r["Year"],
          "Month": r["Month"],
      })

  # 4. Database Storage and Aggregations
  os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()

  try:
    setup_database_tables(cursor)
    cursor.execute("DELETE FROM deployments;")

    db_records = [
        (
            r["Group"],
            r["Group"],
            r["Realm"],
            r["IsoDate"],
            r["Success"],
            "consolidated",
        )
        for r in consolidated_rows
    ]

    cursor.executemany(
        """
            INSERT INTO deployments (service_name, team_name, realm_name, deployed_at, status, source)
            VALUES (?, ?, ?, ?, ?, ?);
        """,
        db_records,
    )

    monthly_stats = {}
    for r in consolidated_rows:
      key = (r["Realm"], r["Group"], r["Year"], r["Month"])
      if key not in monthly_stats:
        monthly_stats[key] = {"total": 0, "success": 0, "failure": 0}
      monthly_stats[key]["total"] += 1
      if r["Success"] == "Success":
        monthly_stats[key]["success"] += 1
      else:
        monthly_stats[key]["failure"] += 1

    for (realm, group, year, month), stats in monthly_stats.items():
      time_bucket = f"{year}-{month:02d}"
      total = stats["total"]
      fails = stats["failure"]
      succs = stats["success"]
      cfr_pct = round((fails / total) * 100.0, 2) if total > 0 else 0.0
      summary_id = f"df_cfr_{realm}_{group}_{time_bucket}"

      cursor.execute(
          """
                INSERT INTO monthly_df_cfr (
                    id, realm_name, group_name, time_frame, time_bucket,
                    total_deployments, successful_deployments, failed_deployments,
                    cfr_percent, updated_at
                ) VALUES (?, ?, ?, 'month', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    total_deployments = excluded.total_deployments,
                    successful_deployments = excluded.successful_deployments,
                    failed_deployments = excluded.failed_deployments,
                    cfr_percent = excluded.cfr_percent,
                    updated_at = CURRENT_TIMESTAMP;
            """,
          (summary_id, realm, group, time_bucket, total, succs, fails, cfr_pct),
      )

    conn.commit()
    print(
        f"Successfully processed {len(consolidated_rows)} wrangled records into"
        f" SQLite ({DB_PATH})."
    )

  except Exception as e:
    print(f"Error executing df_cfr_engine: {e}")
    conn.rollback()
  finally:
    conn.close()


if __name__ == "__main__":
  compute_df_and_cfr()