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

    response = requests.get(BASE_URL, allow_redirects=True)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []

def seed_registry():
    print("Fetching service mappings from Web App endpoint...")
    rows = get_sheet_data()

    if not rows:
        print("No rows retrieved from Google Apps Script.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Drop legacy schema to ensure new columns ('source', 'updated_at') exist
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

    seeded_count = 0
    for row in rows:
        service = row.get("Service Name") or row.get("service_name") or row.get("Service") or row.get("Services")
        realm = row.get("Realm Name") or row.get("realm_name") or row.get("Realm") or row.get("Realms")
        team = row.get("Team Name") or row.get("team_name") or row.get("Team") or row.get("Teams")

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

    conn.commit()
    conn.close()
    print(f"Successfully seeded {seeded_count} service mappings into SQLite database!")

if __name__ == "__main__":
    seed_registry()