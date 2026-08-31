import os
import csv
import sqlite3
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

# Environment credentials & endpoints
SERVICE_INVENTORY_URL = os.getenv("SERVICE_INVENTORY_URL")
SERVICE_INVENTORY_API_KEY = os.getenv("SERVICE_INVENTORY_API_KEY")

DEFAULT_USER = "natasha.baisiwala"
SERVICE_INVENTORY_USER = os.getenv("SERVICE_INVENTORY_USER", DEFAULT_USER)
if "@" in SERVICE_INVENTORY_USER:
    SERVICE_INVENTORY_USER = SERVICE_INVENTORY_USER.split("@")[0]

DB_PATH = os.path.join("database", "dora_metrics.db")
EXPORT_CSV_PATH = "inspected_service_inventory.csv"

# Cutoff start date for DORA metrics baseline
START_DATE_CUTOFF = "2024-01-01"

def init_raw_service_inventory_table(cursor):
    """Ensures raw Service Inventory storage table exists with exact column structure."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS service_inventory_raw (
            deployment_id TEXT PRIMARY KEY,
            "Deployment Date" TEXT,
            "Service" TEXT,
            "Rollout Success" TEXT
        )
    """)

def format_date_to_european(iso_date_str):
    """Converts YYYY-MM-DD into European DD.MM.YYYY format matching Google Sheet."""
    if not iso_date_str or len(iso_date_str) < 10:
        return iso_date_str
    parts = iso_date_str[:10].split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return iso_date_str

def export_service_inventory_to_csv(cursor):
    """Exports records matching exact 3-column spreadsheet format [Deployment Date, Service, Rollout Success]."""
    cursor.execute('SELECT "Deployment Date", "Service", "Rollout Success" FROM service_inventory_raw ORDER BY deployment_id ASC')
    rows = cursor.fetchall()
    
    headers = ["Deployment Date", "Service", "Rollout Success"]
    
    with open(EXPORT_CSV_PATH, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)
        
    print(f"Exported {len(rows)} records (2024-01-01 onwards in DD.MM.YYYY format) to {EXPORT_CSV_PATH}!")

def fetch_and_store_sre_deployments():
    """Fetches full SRE deployments logbook, filtering >= 2024-01-01 and formatting dates as DD.MM.YYYY."""
    if not SERVICE_INVENTORY_URL or not SERVICE_INVENTORY_API_KEY:
        print("Error: SERVICE_INVENTORY_URL or SERVICE_INVENTORY_API_KEY is missing from environment.")
        return

    base_url = SERVICE_INVENTORY_URL.rstrip('/')
    endpoint_url = f"{base_url}/api/deployments"
    print(f"Fetching SRE deployment logbook from {endpoint_url} (Filtering >= {START_DATE_CUTOFF})...")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        init_raw_service_inventory_table(cursor)
        
        # Clear existing table to remove legacy pre-2024 rows
        cursor.execute("DELETE FROM service_inventory_raw")

        # Primary authentication attempt using natasha.baisiwala
        auth = HTTPBasicAuth(SERVICE_INVENTORY_USER, SERVICE_INVENTORY_API_KEY)
        res = requests.get(endpoint_url, auth=auth, timeout=60)
        
        # Fallback to githubactions if primary user returns 401
        if res.status_code == 401 and SERVICE_INVENTORY_USER != "githubactions":
            print("Notice: Primary user auth returned 401, trying 'githubactions'...")
            auth = HTTPBasicAuth("githubactions", SERVICE_INVENTORY_API_KEY)
            res = requests.get(endpoint_url, auth=auth, timeout=60)

        if res.status_code != 200:
            print(f"Error fetching SRE deployments (HTTP {res.status_code}): {res.text[:300]}")
            return

        raw_deployments = res.json()

        # Filter criteria: environment == production, non-null endTime, date >= 2024-01-01
        filtered_deployments = []
        for item in raw_deployments:
            if item.get("environment") != "production" or not item.get("endTime"):
                continue
            
            end_time_str = str(item.get("endTime"))
            iso_date = end_time_str[:10]  # YYYY-MM-DD
            
            # Enforce Jan 1, 2024 cutoff
            if iso_date >= START_DATE_CUTOFF:
                filtered_deployments.append((item, iso_date))

        # Sort chronologically by endTime
        filtered_deployments.sort(key=lambda x: str(x[0].get("endTime")))

        total_processed = 0
        for item, iso_date in filtered_deployments:
            service_name = item.get("service")
            if not service_name:
                continue

            # Format date as DD.MM.YYYY matching reference Google Sheet
            deployment_date_euro = format_date_to_european(iso_date)

            # Evaluate Rollout Success: TRUE if not rollback, FALSE if rollback
            is_rollback = bool(item.get("rollback", False))
            rollout_success = "FALSE" if is_rollback else "TRUE"

            # Primary key for database deduplication
            deployment_id = f"sre_{service_name}_{item.get('endTime')}"

            cursor.execute("""
                INSERT OR REPLACE INTO service_inventory_raw (
                    deployment_id, "Deployment Date", "Service", "Rollout Success"
                ) VALUES (?, ?, ?, ?)
            """, (deployment_id, deployment_date_euro, service_name, rollout_success))

            total_processed += 1

        print(f"Successfully ingested {total_processed} production deployments (2024-01-01 onwards) into service_inventory_raw.")
        
        # Export CSV matching Google Sheet format
        export_service_inventory_to_csv(cursor)

    except Exception as e:
        print(f"Error fetching SRE deployments: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    fetch_and_store_sre_deployments()