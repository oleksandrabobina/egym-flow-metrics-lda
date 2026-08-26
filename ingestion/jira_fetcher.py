import os
import sqlite3
import yaml
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

# Accept either JIRA_URL or JIRA_DOMAIN from environment
raw_url = os.getenv("JIRA_URL") or os.getenv("JIRA_DOMAIN") or "egym.atlassian.net"
JIRA_DOMAIN = raw_url.replace("https://", "").replace("http://", "").strip("/")

JIRA_EMAIL = os.getenv("JIRA_USER_EMAIL") or os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_KEY") or os.getenv("JIRA_API_TOKEN")
DB_PATH = os.path.join("database", "dora_metrics.db")
CONFIG_PATH = "config.yaml"

def parse_iso_timestamp(ts_str):
    """Parses ISO-8601 UTC or YYYY-MM-DD timestamps safely."""
    if not ts_str:
        return None
    try:
        cleaned_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d 00:00:00")
        except ValueError:
            return None

def build_jira_project_map(config_path):
    """Builds a lookup map: { 'PROJECT_KEY': ('Team Name', 'Realm Name') } from config.yaml."""
    project_map = {}
    if not os.path.exists(config_path):
        print(f"Warning: {config_path} not found. Skipping targeted Jira mapping.")
        return project_map

    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}

    realms = config.get("realms", {})
    for realm_name, realm_info in realms.items():
        teams = realm_info.get("teams", [])
        for team in teams:
            if isinstance(team, dict):
                team_name = team.get("name")
                jira_projects = team.get("jira_projects", [])
            else:
                team_name = team
                jira_projects = []
            
            for project_key in jira_projects:
                project_map[project_key.upper()] = (team_name, realm_name)
                
    return project_map

def register_service(cursor, service_name, realm_name):
    """Auto-registers project keys into service_realm_registry."""
    cursor.execute("""
        INSERT INTO service_realm_registry (service_name, realm_name, source_type)
        VALUES (?, ?, 'jira')
        ON CONFLICT(service_name) DO UPDATE SET realm_name=excluded.realm_name
    """, (service_name, realm_name))

def insert_deployment(cursor, deployment_data):
    """Inserts processed deployment/release records into the deployments table."""
    cursor.execute("""
        INSERT OR REPLACE INTO deployments (
            deployment_id, service_name, realm_name, team_name, source, status, deployed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, deployment_data)

def fetch_and_store_jira_releases():
    jira_map = build_jira_project_map(CONFIG_PATH)
    
    if not jira_map:
        print("Notice: No Jira projects configured in config.yaml. Skipping Jira fetch.")
        return

    print(f"Targeting Jira projects configured in config.yaml: {list(jira_map.keys())}")

    if not JIRA_API_TOKEN or JIRA_API_TOKEN in ["your_jira_token_here", "your_jira_api_key"]:
        print("Notice: No valid JIRA_API_KEY in .env. Skipping execution.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        auth = (JIRA_EMAIL, JIRA_API_TOKEN)
        headers = {"Accept": "application/json"}
        total_releases = 0

        # Query only the project keys present in config.yaml
        for project_key, (team_name, realm_name) in jira_map.items():
            register_service(cursor, project_key, realm_name)

            versions_url = f"https://{JIRA_DOMAIN}/rest/api/3/project/{project_key}/versions"
            v_res = requests.get(versions_url, auth=auth, headers=headers)
            
            if v_res.status_code == 200:
                versions = v_res.json()
                for v in versions:
                    if v.get("released"):
                        dep_id = f"jira_{v.get('id')}"
                        release_date = parse_iso_timestamp(v.get("releaseDate"))
                        
                        payload = (
                            dep_id, 
                            project_key, 
                            realm_name, 
                            team_name, 
                            "jira", 
                            "released", 
                            release_date
                        )
                        insert_deployment(cursor, payload)
                        total_releases += 1
            else:
                print(f"Warning: Failed to fetch versions for {project_key} (Status Code: {v_res.status_code})")

        print(f"Successfully processed {total_releases} Jira releases across targeted realms.")

    except Exception as e:
        print(f"Error fetching Jira releases: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_jira_releases()