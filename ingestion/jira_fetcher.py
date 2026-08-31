import os
import csv
import sqlite3
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Environment credentials & endpoints
raw_url = os.getenv("JIRA_URL")
JIRA_DOMAIN = raw_url.replace("https://", "").replace("http://", "").strip("/") if raw_url else None
JIRA_EMAIL = os.getenv("JIRA_USER_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_KEY")
MAPPING_WEB_APP_URL = os.getenv("MAPPING_WEB_APP_URL")
MAPPING_TAB_GID = os.getenv("MAPPING_TAB_GID")

DB_PATH = os.path.join("database", "dora_metrics.db")
EXPORT_CSV_PATH = "inspected_jira_releases.csv"

def parse_iso_timestamp(ts_str):
    """Parses Jira resolution date string into ISO format (YYYY-MM-DD)."""
    if not ts_str:
        return None
    try:
        cleaned_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned_str)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ts_str[:10] if ts_str else None

def init_raw_jira_table(cursor):
    """Ensures raw Jira storage table exists with exact original column names."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jira_releases_raw (
            "Issue Type" TEXT,
            "Issue key" TEXT PRIMARY KEY,
            "Issue id" TEXT,
            "Summary" TEXT,
            "Status" TEXT,
            "Components" TEXT,
            "Resolved" TEXT
        )
    """)

def fetch_jira_project_keys_from_mapping():
    """Fetches active Jira project keys directly from MAPPING_WEB_APP_URL using configured GID."""
    if not MAPPING_WEB_APP_URL or not MAPPING_TAB_GID:
        print("Error: MAPPING_WEB_APP_URL or MAPPING_TAB_GID is missing in environment variables.")
        return []

    try:
        separator = "&" if "?" in MAPPING_WEB_APP_URL else "?"
        target_url = f"{MAPPING_WEB_APP_URL}{separator}gid={MAPPING_TAB_GID}"
        
        res = requests.get(target_url, timeout=30)
        res.raise_for_status()
        mapping_data = res.json()
        
        project_keys = set()
        
        if isinstance(mapping_data, list):
            for row in mapping_data:
                if isinstance(row, dict):
                    key = row.get("Jira Key") or row.get("Jira key") or row.get("jira_key") or row.get("JiraKey") or row.get("Project Key")
                    if key and str(key).strip():
                        project_keys.add(str(key).strip())

        return list(project_keys)

    except Exception as e:
        print(f"Error fetching Jira project keys from MAPPING_WEB_APP_URL: {e}")
        return []

def insert_raw_jira_release(cursor, raw_data):
    """Inserts or replaces raw Jira release record in jira_releases_raw table."""
    cursor.execute("""
        INSERT OR REPLACE INTO jira_releases_raw (
            "Issue Type", "Issue key", "Issue id", "Summary", "Status", "Components", "Resolved"
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, raw_data)

def export_jira_releases_to_csv(cursor):
    """Exports all records from jira_releases_raw to a root CSV file for reconciliation."""
    cursor.execute('SELECT "Issue Type", "Issue key", "Issue id", "Summary", "Status", "Components", "Resolved" FROM jira_releases_raw')
    rows = cursor.fetchall()
    
    headers = ["Issue Type", "Issue key", "Issue id", "Summary", "Status", "Components", "Resolved"]
    
    with open(EXPORT_CSV_PATH, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)
        
    print(f"Exported {len(rows)} records to {EXPORT_CSV_PATH} for reconciliation!")

def fetch_and_store_jira_releases():
    if not JIRA_API_TOKEN or not JIRA_EMAIL or not JIRA_DOMAIN:
        print("Error: Missing JIRA_URL, JIRA_USER_EMAIL, or JIRA_API_KEY in environment variables.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        init_raw_jira_table(cursor)

        jira_projects = fetch_jira_project_keys_from_mapping()
        if not jira_projects:
            print("Notice: No Jira project keys retrieved from MAPPING_WEB_APP_URL. Skipping fetch.")
            return

        print(f"Found Jira project keys to query: {jira_projects}")

        project_clause = ", ".join(f'"{p}"' for p in jira_projects)
        jql = (
            f"project IN ({project_clause}) "
            "AND issuetype = Release "
            "AND status IN (Done, Failed) "
            "AND resolved >= '2024-01-01'"
        )

        search_url = f"https://{JIRA_DOMAIN}/rest/api/3/search/jql"
        auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        next_page_token = None
        total_releases = 0

        while True:
            payload = {
                "jql": jql, 
                "maxResults": 50,
                "fields": ["summary", "issuetype", "status", "components", "resolutiondate"]
            }
            if next_page_token:
                payload["nextPageToken"] = next_page_token

            res = requests.post(search_url, auth=auth, headers=headers, json=payload, timeout=30)
            
            if res.status_code == 400:
                payload.pop("fields", None)
                res = requests.post(search_url, auth=auth, headers=headers, json=payload, timeout=30)
                
            res.raise_for_status()
            data = res.json()
            
            issues = data.get("issues", [])
            if not issues:
                break

            for issue_item in issues:
                issue_id = str(issue_item.get("id"))
                issue_key = issue_item.get("key")
                fields = issue_item.get("fields", {})

                if not fields:
                    issue_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_id}"
                    issue_res = requests.get(issue_url, auth=auth, headers={"Accept": "application/json"}, timeout=15)
                    if issue_res.status_code == 200:
                        detail_data = issue_res.json()
                        issue_key = detail_data.get("key", issue_key)
                        fields = detail_data.get("fields", {})

                issue_type = fields.get("issuetype", {}).get("name", "Release")
                summary = fields.get("summary", "")
                status = fields.get("status", {}).get("name", "")
                
                components_list = fields.get("components", [])
                components_str = ", ".join([c.get("name") for c in components_list if c.get("name")])

                resolved_date = parse_iso_timestamp(fields.get("resolutiondate"))

                raw_payload = (
                    issue_type,
                    issue_key,
                    issue_id,
                    summary,
                    status,
                    components_str,
                    resolved_date
                )
                
                insert_raw_jira_release(cursor, raw_payload)
                total_releases += 1

            if data.get("isLast", True):
                break
                
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        print(f"Successfully ingested {total_releases} raw Jira release issues into jira_releases_raw.")
        
        # Export CSV file for manual reconciliation
        export_jira_releases_to_csv(cursor)

    except Exception as e:
        print(f"Error fetching Jira releases: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_jira_releases()