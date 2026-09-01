import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join("database", "dora_metrics.db")
BASE_URL = os.getenv("MAPPING_WEB_APP_URL")

def get_sheet_data():
    if not BASE_URL:
        print("Error: MAPPING_WEB_APP_URL is missing from .env")
        return []

    try:
        response = requests.get(BASE_URL, allow_redirects=True, timeout=15)
        response.raise_for_status()
        data = response.json()

        # Unwrap top-level dictionary structures (e.g., {"data": [...]} or multi-tab payloads)
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                data = data["data"]
            else:
                flat_rows = []
                for val in data.values():
                    if isinstance(val, list):
                        flat_rows.extend(val)
                data = flat_rows

        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    except Exception as e:
        print(f"Error fetching sheet data: {e}")
    
    return []

def seed_registry():
    print("Fetching service mappings from Web App endpoint...")
    rows = get_sheet_data()

    if not rows:
        print("No rows retrieved from Google Apps Script.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Re-initialize tables cleanly
    cursor.execute("DROP TABLE IF EXISTS service_realm_registry;")
    cursor.execute("""
        CREATE TABLE service_realm_registry (
            service_name VARCHAR(100) PRIMARY KEY,
            realm_name VARCHAR(100),
            team_name VARCHAR(100),
            source VARCHAR(50),
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS realms_teams (
            realm_name VARCHAR(100),
            team_name VARCHAR(100),
            PRIMARY KEY (realm_name, team_name)
        );
    """)

    seeded_count = 0
    for row in rows:
        # Normalize keys by stripping spaces, underscores, and lowercasing
        norm_row = {str(k).strip().lower().replace(" ", "").replace("_", ""): str(v).strip() for k, v in row.items() if v is not None}
        
        # Matches 'Service Inventory' across all tabs, with fallbacks for alternative headers
        service = norm_row.get("serviceinventory") or norm_row.get("servicename") or norm_row.get("service")
        team = norm_row.get("team") or norm_row.get("teams") or norm_row.get("teamname")
        realm = norm_row.get("realm") or norm_row.get("realms") or norm_row.get("realmname")
        
        if not service and team:
            service = team

        if service and realm:
            cursor.execute("""
                INSERT INTO service_realm_registry (service_name, realm_name, team_name, source, updated_at)
                VALUES (?, ?, ?, 'apps_script', CURRENT_TIMESTAMP)
                ON CONFLICT(service_name) DO UPDATE SET
                    realm_name = excluded.realm_name,
                    team_name = excluded.team_name,
                    updated_at = CURRENT_TIMESTAMP
            """, (str(service).strip(), str(realm).strip(), str(team or 'Unassigned').strip()))
            seeded_count += 1

        # Seed distinct realm-to-team relationships for dashboard headers
        if realm and team and team.lower() != 'unassigned':
            cursor.execute("""
                INSERT OR IGNORE INTO realms_teams (realm_name, team_name)
                VALUES (?, ?)
            """, (str(realm).strip(), str(team).strip()))

    conn.commit()
    conn.close()
    print(f"Successfully seeded {seeded_count} service mappings into SQLite database!")

if __name__ == "__main__":
    seed_registry()