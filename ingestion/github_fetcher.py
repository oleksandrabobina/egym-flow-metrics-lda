import os
import sqlite3
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

# Environment credentials & endpoints
SERVICE_INVENTORY_URL = os.getenv("SERVICE_INVENTORY_URL")
SERVICE_INVENTORY_API_KEY = os.getenv("SERVICE_INVENTORY_API_KEY")
DB_PATH = os.path.join("database", "dora_metrics.db")

def get_mapping_from_registry(cursor, service_name):
    """Queries service_realm_registry for pre-populated team/realm mapping."""
    cursor.execute("""
        SELECT team_name, realm_name 
        FROM service_realm_registry 
        WHERE service_name = ?
    """, (service_name,))
    row = cursor.fetchone()
    if row:
        return row[0] or "Unassigned", row[1] or "Unassigned"
    return "Unassigned", "Unassigned"

def fetch_and_store_sre_deployments(days=28):
    """Fetches production deployment data from SRE Service Inventory API and maps team/realm dynamically."""
    if not SERVICE_INVENTORY_URL or not SERVICE_INVENTORY_API_KEY:
        print("Error: SERVICE_INVENTORY_URL or SERVICE_INVENTORY_API_KEY is missing from environment.")
        return

    # Sanitize base URL to handle trailing slashes safely
    base_url = SERVICE_INVENTORY_URL.rstrip('/')
    endpoint_url = f"{base_url}/api/deployments/recent/{days}"
    print(f"Fetching SRE deployment metrics for the past {days} days from endpoint...")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # HTTP Basic Auth using 'githubactions' as username
        auth = HTTPBasicAuth("natasha.baisiwala", SERVICE_INVENTORY_API_KEY)
        res = requests.get(endpoint_url, auth=auth, timeout=30)
        
        if res.status_code != 200:
            print(f"Error fetching SRE deployments (HTTP {res.status_code}):")
            print(res.text[:300])
            return

        try:
            raw_deployments = res.json()
        except Exception:
            print("Error: SRE endpoint returned non-JSON content:")
            print(res.text[:300])
            return
        total_processed = 0
        for item in raw_deployments:
            # 1. Apply SRE Filters: production only and completed (endTime not null)
            if item.get("environment") != "production" or not item.get("endTime"):
                continue

            service_name = item.get("service")
            if not service_name:
                continue

            # 2. Extract ISO Date (YYYY-MM-DD) from endTime
            deployed_at = item.get("endTime")[:10]

            # 3. Evaluate Rollout Success / Failure
            is_rollback = item.get("rollback", False)
            status = "failure" if is_rollback else "success"

            # 4. Dynamic Lookup for Team & Realm mapping from registry
            team_name, realm_name = get_mapping_from_registry(cursor, service_name)

            # Unique ID key to prevent duplicates
            deployment_id = f"sre_{service_name}_{item.get('endTime')}"

            # Insert into SQLite deployments table with mapped Team & Realm
            cursor.execute("""
                INSERT OR REPLACE INTO deployments (
                    deployment_id, service_name, realm_name, team_name, source, status, deployed_at
                ) VALUES (?, ?, ?, ?, 'github', ?, ?)
            """, (deployment_id, service_name, realm_name, team_name, status, deployed_at))

            total_processed += 1

        print(f"Successfully ingested {total_processed} production deployments.")

    except Exception as e:
        print(f"Error fetching SRE deployments: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_sre_deployments(days=28)