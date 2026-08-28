import os
import sqlite3
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Credentials and Jira Domain setup from environment variables
raw_url = os.getenv("JIRA_URL") or os.getenv("JIRA_DOMAIN") or "egym.atlassian.net"
JIRA_DOMAIN = raw_url.replace("https://", "").replace("http://", "").strip("/")
JIRA_EMAIL = os.getenv("JIRA_USER_EMAIL") or os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_KEY") or os.getenv("JIRA_API_TOKEN")

DB_PATH = os.path.join("database", "dora_metrics.db")

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

def get_registered_jira_projects(cursor):
    """Dynamically fetches distinct Jira project keys stored in the registry database."""
    cursor.execute("""
        SELECT DISTINCT service_name 
        FROM service_realm_registry 
        WHERE source_type = 'jira' AND service_name IS NOT NULL
    """)
    rows = cursor.fetchall()
    return [row[0].strip() for row in rows if row[0] and row[0].strip()]

def get_mapping_from_registry(cursor, identifier, fallback_identifier=None):
    """Looks up team and realm mapping for a component or project key from SQLite."""
    cursor.execute("""
        SELECT team_name, realm_name 
        FROM service_realm_registry 
        WHERE service_name = ?
    """, (identifier,))
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    if fallback_identifier and fallback_identifier != identifier:
        cursor.execute("""
            SELECT team_name, realm_name 
            FROM service_realm_registry 
            WHERE service_name = ?
        """, (fallback_identifier,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1]

    return "Unassigned", "Unassigned"

def insert_deployment(cursor, deployment_data):
    """Inserts transformed Jira release record into deployments table."""
    cursor.execute("""
        INSERT OR REPLACE INTO deployments (
            deployment_id, service_name, realm_name, team_name, source, status, deployed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, deployment_data)

def fetch_and_store_jira_releases():
    if not JIRA_API_TOKEN or not JIRA_EMAIL:
        print("Error: Missing JIRA_EMAIL or JIRA_API_KEY in environment variables.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Fetch registered Jira projects dynamically from database
        jira_projects = get_registered_jira_projects(cursor)
        if not jira_projects:
            print("Notice: No Jira projects found in service_realm_registry. Skipping fetch.")
            return

        # 2. Construct dynamic JQL clause with quoted project names
        project_clause = ", ".join(f'"{p}"' for p in jira_projects)
        jql = (
            f"project IN ({project_clause}) "
            "AND issuetype = Release "
            "AND status IN (Done, Failed) "
            "AND resolved >= '2024-01-01'"
        )

        url = f"https://{JIRA_DOMAIN}/rest/api/3/search"
        auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        start_at = 0
        max_results = 50
        total_releases = 0

        while True:
            params = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": "summary,status,components,resolutiondate,project"
            }

            res = requests.get(url, auth=auth, headers=headers, params=params, timeout=30)
            res.raise_for_status()
            data = res.json()
            issues = data.get("issues", [])

            if not issues:
                break

            for issue in issues:
                issue_key = issue.get("key")
                fields = issue.get("fields", {})

                # Outcome mapping: Done -> success, Failed -> failure
                raw_status = fields.get("status", {}).get("name", "")
                status = "success" if raw_status.lower() == "done" else "failure"

                # Parse resolution date
                resolved_date = parse_iso_timestamp(fields.get("resolutiondate"))

                # Extract component or project key as lookup target
                components = fields.get("components", [])
                project_key = fields.get("project", {}).get("key", "")
                primary_service = components[0].get("name") if components else project_key

                # Look up Team and Realm dynamically with fallback to project_key
                team_name, realm_name = get_mapping_from_registry(cursor, primary_service, fallback_identifier=project_key)

                deployment_id = f"jira_{issue_key}"
                payload = (
                    deployment_id,
                    primary_service,
                    realm_name,
                    team_name,
                    "jira",
                    status,
                    resolved_date
                )
                insert_deployment(cursor, payload)
                total_releases += 1

            start_at += len(issues)
            if start_at >= data.get("total", 0):
                break

        print(f"Successfully ingested {total_releases} Jira release issues.")

    except Exception as e:
        print(f"Error fetching Jira releases: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_jira_releases()