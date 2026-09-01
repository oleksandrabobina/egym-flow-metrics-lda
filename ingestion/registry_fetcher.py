import csv
import json
import os
import re
import sqlite3
import urllib.request
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join("database", "dora_metrics.db")
LOCAL_CSV_PATH = os.path.join("database", "service_realm_registry.csv")

# Loaded exclusively from environment variable (.env)
MAPPING_WEB_APP_URL = os.getenv("MAPPING_WEB_APP_URL")

def canonical_normalize(name_str):
    """
    Strips out conjunctions (&, and, +, /, with), hyphens, and extra spaces.
    Converts 'WP Offer & Acquisition' AND 'WP Offer Acquisition' to 'wp offer acquisition'.
    """
    if not name_str:
        return ""
    clean = str(name_str).lower()
    clean = re.sub(r"\b(and|or|with)\b", " ", clean)
    clean = clean.replace("&", " ").replace("+", " ").replace("/", " ")
    clean = re.sub(r"[^a-z0-9]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def sync_incident_realm_registry():
    """Fetch live incident service-to-realm registry via Apps Script Web App or local CSV fallback."""
    print("Fetching incident service-to-realm registry...")
    records = []

    # Attempt 1: Fetch via Web App Endpoint if configured in .env
    if MAPPING_WEB_APP_URL:
        try:
            req = urllib.request.Request(
                MAPPING_WEB_APP_URL, 
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                records = data.get("data", data) if isinstance(data, dict) else data
            print("Successfully retrieved registry from Apps Script Web App Endpoint!")
        except Exception as e:
            print(f"Notice: Web App Endpoint fetch failed ({e}). Checking local CSV fallback...")

    # Attempt 2: Fallback to local CSV file (database/service_realm_registry.csv)
    if not records and os.path.exists(LOCAL_CSV_PATH):
        try:
            with open(LOCAL_CSV_PATH, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                records = list(reader)
            print(f"Successfully loaded local registry from {LOCAL_CSV_PATH}!")
        except Exception as e:
            print(f"Error reading local CSV file: {e}")

    if not records:
        print("Error: Could not retrieve registry data. Please verify MAPPING_WEB_APP_URL in .env or place 'service_realm_registry.csv' in database/.")
        return

    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("DROP TABLE IF EXISTS service_realm_registry;")
        cursor.execute("""
            CREATE TABLE service_realm_registry (
                team_name VARCHAR(255) PRIMARY KEY,
                normalized_team_name VARCHAR(255) NOT NULL,
                realm_name VARCHAR(255) NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        synced_count = 0
        for entry in records:
            if not isinstance(entry, dict):
                continue

            team = None
            realm = None
            aliases_raw = ""

            for k, v in entry.items():
                k_clean = str(k).strip().lower().replace(" ", "_")
                if "team" in k_clean or "component" in k_clean or "service" in k_clean:
                    team = str(v).strip() if v else None
                elif "realm" in k_clean:
                    realm = str(v).strip() if v else None
                elif "alias" in k_clean:
                    aliases_raw = str(v).strip() if v else ""

            if team and realm and team.lower() not in ["nan", "none"]:
                norm_team = canonical_normalize(team)
                cursor.execute("""
                    INSERT OR REPLACE INTO service_realm_registry (team_name, normalized_team_name, realm_name)
                    VALUES (?, ?, ?)
                """, (team, norm_team, realm))
                synced_count += 1

                # If an 'Aliases' column exists in the Google Sheet tab, insert alias entries
                if aliases_raw:
                    aliases_list = [a.strip() for a in aliases_raw.split(",") if a.strip()]
                    for alias in aliases_list:
                        cursor.execute("""
                            INSERT OR REPLACE INTO service_realm_registry (team_name, normalized_team_name, realm_name)
                            VALUES (?, ?, ?)
                        """, (alias, canonical_normalize(alias), realm))

        conn.commit()
        conn.close()
        print(f"Successfully synced {synced_count} incident team-to-realm mappings (with canonical keys) into database table 'service_realm_registry'!")

    except Exception as e:
        print(f"Error processing registry data into SQLite: {e}")

if __name__ == "__main__":
    sync_incident_realm_registry()