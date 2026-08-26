import os
import sqlite3
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

INCIDENT_IO_API_KEY = os.getenv("INCIDENT_IO_API_KEY")
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

def auto_register_team(cursor, team_name):
    """Auto-registers new teams in realms_teams upon detection."""
    if not team_name:
        return
    cursor.execute(
        "INSERT OR IGNORE INTO realms_teams (team_name) VALUES (?)",
        (team_name,)
    )

def insert_incident(cursor, incident_data):
    """Inserts parsed incident data directly into raw_incidents."""
    cursor.execute("""
        INSERT OR REPLACE INTO raw_incidents (
            incident_id, realm, status, severity, affected_team,
            affected_products, root_cause_service, impact_started_at,
            identified_at, fixed_at, resolved_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, incident_data)

def fetch_and_store_incidents():
    print("Fetching closed incidents from Incident.io...")
    
    url = "https://api.incident.io/v2/incidents"
    headers = {
        "Authorization": f"Bearer {INCIDENT_IO_API_KEY}",
        "Content-Type": "application/json"
    }

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if not INCIDENT_IO_API_KEY or INCIDENT_IO_API_KEY == "your_incident_io_api_key_here":
            print("Notice: No live API key in .env. Parsing & DB insertion logic are fully prepared.")
            return

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        incidents = response.json().get("incidents", [])

        for item in incidents:
            incident_id = item.get("id")
            status = item.get("status")
            severity = item.get("severity", {}).get("name")
            
            affected_team = item.get("custom_fields", {}).get("affected_team")
            affected_products = str(item.get("custom_fields", {}).get("affected_products", ""))
            root_cause_service = item.get("custom_fields", {}).get("root_cause_service")

            impact_started = parse_iso_timestamp(item.get("timestamps", {}).get("impact_started_at"))
            identified = parse_iso_timestamp(item.get("timestamps", {}).get("identified_at"))
            fixed = parse_iso_timestamp(item.get("timestamps", {}).get("fixed_at"))
            resolved = parse_iso_timestamp(item.get("timestamps", {}).get("resolved_at"))
            updated = parse_iso_timestamp(item.get("updated_at"))

            # Auto-register team in realms_teams table
            auto_register_team(cursor, affected_team)

            # Realm is set to None initially for the LLM agent to classify later
            payload = (
                incident_id, None, status, severity, affected_team,
                affected_products, root_cause_service, impact_started,
                identified, fixed, resolved, updated
            )

            insert_incident(cursor, payload)

        print(f"Successfully processed {len(incidents)} incidents.")

    except Exception as e:
        print(f"Error fetching incidents: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_incidents()