import os
import sqlite3
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

JIRA_DOMAIN = os.getenv("JIRA_DOMAIN", "your-domain.atlassian.net")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
DB_PATH = os.path.join("database", "dora_metrics.db")

def parse_iso_timestamp(ts_str):
    """Parses ISO-8601 UTC timestamps safely."""
    if not ts_str:
        return None
    try:
        cleaned_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def register_service(cursor, service_name):
    """Auto-registers project keys into service_realm_registry."""
    cursor.execute("""
        INSERT OR IGNORE INTO service_realm_registry (service_name, source_type)
        VALUES (?, 'jira')
    """, (service_name,))

def insert_deployment(cursor, deployment_data):
    """Inserts processed deployment/release records into the deployments table."""
    cursor.execute("""
        INSERT OR REPLACE INTO deployments (
            deployment_id, service_name, realm_name, team_name, source, status, deployed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, deployment_data)

def fetch_and_store_jira_releases():
    print("Auto-discovering Jira project keys and release metadata...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if not JIRA_API_TOKEN or JIRA_API_TOKEN == "your_jira_token_here":
            print("Notice: No live JIRA_API_TOKEN in .env. Jira release ingestion logic is ready.")
            return

        auth = (JIRA_EMAIL, JIRA_API_TOKEN)
        headers = {"Accept": "application/json"}

        # Auto-discover Jira projects
        projects_url = f"https://{JIRA_DOMAIN}/rest/api/3/project"
        res = requests.get(projects_url, auth=auth, headers=headers)
        res.raise_for_status()
        projects = res.json()

        total_releases = 0
        for proj in projects:
            project_key = proj.get("key")
            register_service(cursor, project_key)

            # Pull version/release metadata for each project key
            versions_url = f"https://{JIRA_DOMAIN}/rest/api/3/project/{project_key}/versions"
            v_res = requests.get(versions_url, auth=auth, headers=headers)
            if v_res.status_code == 200:
                versions = v_res.json()
                for v in versions:
                    if v.get("released"):
                        dep_id = f"jira_{v.get('id')}"
                        release_date = parse_iso_timestamp(v.get("releaseDate"))
                        
                        # Tag source explicitly as 'jira'
                        payload = (dep_id, project_key, None, None, "jira", "released", release_date)
                        insert_deployment(cursor, payload)
                        total_releases += 1

        print(f"Successfully processed {total_releases} Jira releases.")

    except Exception as e:
        print(f"Error fetching Jira releases: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_jira_releases()