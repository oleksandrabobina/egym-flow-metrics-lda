import os
import json
import sqlite3
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_ORG = os.getenv("GITHUB_ORG")
SERVICE_INVENTORY_URL = os.getenv("SERVICE_INVENTORY_URL")
SERVICE_INVENTORY_TOKEN = os.getenv("SERVICE_INVENTORY_TOKEN")
DB_PATH = os.path.join("database", "dora_metrics.db")
MOCK_FILE_PATH = os.path.join("database", "mock_inventory.json")

def parse_iso_timestamp(ts_str):
    if not ts_str:
        return None
    try:
        cleaned_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def fetch_service_inventory():
    """Fetches valid services from live API, local mock file, or skips validation."""
    # Priority 1: Live Internal API (if configured in local .env)
    if SERVICE_INVENTORY_URL and SERVICE_INVENTORY_TOKEN:
        print("Checking Live Service Inventory API...")
        try:
            headers = {"Authorization": f"Bearer {SERVICE_INVENTORY_TOKEN}", "Accept": "application/json"}
            res = requests.get(SERVICE_INVENTORY_URL, headers=headers, timeout=5)
            res.raise_for_status()
            data = res.json()
            return {item["name"]: {"team_name": item.get("team"), "realm_name": item.get("realm")} for item in data if item.get("name")}
        except Exception as e:
            print(f"Notice: Service Inventory API unreachable ({e}).")

    # Priority 2: Local Git-Ignored Mock File (for local dev testing)
    if os.path.exists(MOCK_FILE_PATH):
        print("Loading service validation from local database/mock_inventory.json...")
        with open(MOCK_FILE_PATH, "r") as f:
            data = json.load(f)
            return {item["name"]: {"team_name": item.get("team"), "realm_name": item.get("realm")} for item in data if item.get("name")}

    # Priority 3: Cloud CI/CD Default (Skip catalog filtering safely)
    print("Notice: No Service Inventory endpoint or mock file configured. Proceeding without catalog filtering.")
    return None

def register_service(cursor, service_name, team_name=None, realm_name=None):
    cursor.execute("""
        INSERT OR REPLACE INTO service_realm_registry (service_name, team_name, realm_name, source_type)
        VALUES (?, ?, ?, 'github')
    """, (service_name, team_name, realm_name))

def insert_deployment(cursor, deployment_data):
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
        if not GITHUB_TOKEN or not GITHUB_ORG:
            print("Notice: GITHUB_TOKEN or GITHUB_ORG missing in environment. Table insertion & auto-discovery logic are ready.")
            return

        inventory = fetch_service_inventory()

        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }

        repos_url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"
        res = requests.get(repos_url, headers=headers)
        res.raise_for_status()
        repos = res.json()

        total_deployments = 0
        for repo in repos:
            repo_name = repo.get("name")
            team_name = None
            realm_name = None

            if inventory is not None:
                if repo_name not in inventory:
                    print(f"Skipping repository '{repo_name}': Not listed in Service Inventory.")
                    continue
                team_name = inventory[repo_name].get("team_name")
                realm_name = inventory[repo_name].get("realm_name")

            register_service(cursor, repo_name, team_name, realm_name)

            deploy_url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/deployments"
            d_res = requests.get(deploy_url, headers=headers)
            if d_res.status_code == 200:
                deployments = d_res.json()
                for d in deployments:
                    dep_id = f"gh_{d.get('id')}"
                    created_at = parse_iso_timestamp(d.get("created_at"))
                    
                    payload = (dep_id, repo_name, realm_name, team_name, "github", "success", created_at)
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