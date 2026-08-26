import os
import sqlite3
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_ORG = os.getenv("GITHUB_ORG", "my-org")
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
    """Auto-registers discovered repositories into service_realm_registry."""
    cursor.execute("""
        INSERT OR IGNORE INTO service_realm_registry (service_name, source_type)
        VALUES (?, 'github')
    """, (service_name,))

def insert_deployment(cursor, deployment_data):
    """Inserts processed deployment records into the deployments table."""
    cursor.execute("""
        INSERT OR REPLACE INTO deployments (
            deployment_id, service_name, realm_name, team_name, source, status, deployed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, deployment_data)

def fetch_and_store_github_deployments():
    print("Scanning GitHub org repos and deployment events...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if not GITHUB_TOKEN or GITHUB_TOKEN == "your_github_token_here":
            print("Notice: No live GITHUB_TOKEN in .env. Table insertion & auto-discovery logic are ready.")
            return

        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }

        # Auto-discover repositories for the GitHub organization
        repos_url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"
        res = requests.get(repos_url, headers=headers)
        res.raise_for_status()
        repos = res.json()

        total_deployments = 0
        for repo in repos:
            repo_name = repo.get("name")
            register_service(cursor, repo_name)

            # Pull deployment timestamps for each repo
            deploy_url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/deployments"
            d_res = requests.get(deploy_url, headers=headers)
            if d_res.status_code == 200:
                deployments = d_res.json()
                for d in deployments:
                    dep_id = f"gh_{d.get('id')}"
                    created_at = parse_iso_timestamp(d.get("created_at"))
                    
                    # Tag source explicitly as 'github'
                    payload = (dep_id, repo_name, None, None, "github", "success", created_at)
                    insert_deployment(cursor, payload)
                    total_deployments += 1

        print(f"Successfully processed {total_deployments} GitHub deployments.")

    except Exception as e:
        print(f"Error fetching GitHub deployments: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_github_deployments()