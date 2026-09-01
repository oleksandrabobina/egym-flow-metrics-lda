import csv
import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join("database", "dora_metrics.db")

INCIDENT_IO_URL = os.getenv("INCIDENT_IO_URL")
INCIDENT_IO_API_KEY = os.getenv("INCIDENT_IO_API_KEY")

# Exact IDs extracted directly from SRE shell script
TIMESTAMP_IDS = {
    "impact_started_at": "01GCEY73K6FDY5JBH6BNJ5TR68",
    "identified_at": "01GBT96QC9W2C46VPA5HDAXNW1",
    "fixed_at": "01GBT96QC94Q4P6E8CARFKWD9W"
}

DURATION_IDS = {
    "incident_duration": "01GGZ2RSHJBJKSC0W3ZGTNMQ3W",
    "mttd": "01GW2GSXAQXP2WSF0E6DG88FCN",
    "mttr": "01GZNGZ2DEY6T58RXQ07RJFCRM"
}

CUSTOM_FIELD_IDS = {
    "affected_team": "01G3RGYHGVDXZK9J234TMWT85B",
    "affected_products": "01KC72FT6C0GNRFMCTPSEAFMND",
    "root_cause_services": "01G3S5A121W82BECRS6CF551H6"
}

TEST_INCIDENT_TYPE_ID = "01HHHC3PE57MZBFPJ1A4H6W8RG"
INCIDENT_LEAD_ROLE_ID = "01FZV4K4BRBY9DGSJ0QXV42KVC"

def fetch_all_incidents():
    """Fetch all raw incident objects directly via Incident.io API."""
    if not INCIDENT_IO_URL or not INCIDENT_IO_API_KEY:
        print("Error: INCIDENT_IO_URL or INCIDENT_IO_API_KEY missing from .env")
        return []

    headers = {
        "Authorization": f"Bearer {INCIDENT_IO_API_KEY}",
        "Accept": "application/json"
    }

    all_incidents = []
    after_cursor = None

    print("Fetching historical incidents directly from secured endpoint...")

    while True:
        params = {"page_size": 250}
        if after_cursor:
            params["after"] = after_cursor

        try:
            response = requests.get(INCIDENT_IO_URL, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Error fetching data from API endpoint: {e}")
            break

        incidents = data.get("incidents", [])
        all_incidents.extend(incidents)

        pagination = data.get("pagination_meta", {})
        after_cursor = pagination.get("after")
        if not after_cursor or not incidents:
            break

    return all_incidents

def extract_timestamp_by_id(inc, target_id):
    """Matches SRE logic: (.incident_timestamp_values[]? | select(.incident_timestamp.id == ID) | .value.value)"""
    for entry in inc.get("incident_timestamp_values", []):
        ts_id = str(entry.get("incident_timestamp", {}).get("id") or "")
        if ts_id == target_id:
            val_obj = entry.get("value")
            if isinstance(val_obj, dict):
                return val_obj.get("value")
    return None

def extract_duration_by_id(inc, target_id):
    """Matches SRE logic: (.duration_metrics[]? | select(.duration_metric.id == ID).value_seconds)"""
    for entry in inc.get("duration_metrics", []):
        dur_id = str(entry.get("duration_metric", {}).get("id") or "")
        if dur_id == target_id:
            val = entry.get("value_seconds")
            if val is not None:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    pass
    return None

def extract_custom_field_by_id(inc, target_id):
    """Matches SRE logic: (.custom_field_entries[]? | select(.custom_field.id == ID).values | join(" | "))"""
    found_values = []
    for entry in inc.get("custom_field_entries", []):
        cf_id = str(entry.get("custom_field", {}).get("id") or "")
        if cf_id == target_id:
            values = entry.get("values", [])
            for val in values:
                if isinstance(val, dict):
                    name = val.get("value_catalog_entry", {}).get("name") or val.get("value_text")
                    if name:
                        found_values.append(str(name))
                elif val:
                    found_values.append(str(val))
    return " | ".join(found_values) if found_values else None

def extract_incident_lead(inc):
    """Matches SRE logic: (.incident_role_assignments[]? | select(.role.id == "01FZV4K4BRBY9DGSJ0QXV42KVC").assignee.name)"""
    for assignment in inc.get("incident_role_assignments", []):
        role_id = str(assignment.get("role", {}).get("id") or "")
        if role_id == INCIDENT_LEAD_ROLE_ID:
            assignee = assignment.get("assignee", {})
            if isinstance(assignee, dict):
                name = assignee.get("name") or assignee.get("email")
                if name:
                    return str(name)
    return "Unassigned"

def seed_incidents():
    incidents = fetch_all_incidents()
    if not incidents:
        print("No incidents retrieved. Check your .env configuration.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS incidents;")
    cursor.execute("""
        CREATE TABLE incidents (
            id VARCHAR(50) PRIMARY KEY,
            status VARCHAR(50),
            severity VARCHAR(50),
            impact_started_at DATETIME,
            identified_at DATETIME,
            fixed_at DATETIME,
            mttd INTEGER,
            mttr INTEGER,
            incident_duration INTEGER,
            affected_team VARCHAR(255),
            root_cause_services VARCHAR(255),
            affected_products VARCHAR(255),
            incident_lead VARCHAR(255)
        );
    """)

    seeded_count = 0
    skipped_count = 0

    for inc in incidents:
        # Numeric Reference ID (e.g. 269)
        raw_ref = str(inc.get("reference") or inc.get("name") or inc.get("id") or "")
        inc_id = raw_ref.upper().replace("INC-", "").strip()
        
        name_lower = str(inc.get("name") or "").lower()
        summary_lower = str(inc.get("summary") or "").lower()

        # Rule 1: Exclude noise (test, dependabot)
        if any(term in name_lower or term in summary_lower for term in ["test", "dependabot"]):
            skipped_count += 1
            continue

        # Rule 2: Exclude Incident Type = 'test' (Matches SRE script: incident_type.id == "01HHHC3PE57MZBFPJ1A4H6W8RG")
        incident_type_obj = inc.get("incident_type") or {}
        type_id = incident_type_obj.get("id") if isinstance(incident_type_obj, dict) else ""
        type_name = str(incident_type_obj.get("name") or "").lower() if isinstance(incident_type_obj, dict) else str(incident_type_obj).lower()

        if type_id == TEST_INCIDENT_TYPE_ID or "test" in type_name:
            skipped_count += 1
            continue

        # Extract Severity & Status directly
        severity_obj = inc.get("severity", {})
        severity = severity_obj.get("name") if isinstance(severity_obj, dict) else str(severity_obj or "")

        status_obj = inc.get("incident_status") or inc.get("status")
        status = status_obj.get("name") if isinstance(status_obj, dict) else str(status_obj or "")

        # Extract Native Timestamps by Exact ID (matching SRE script)
        impact_started_at = extract_timestamp_by_id(inc, TIMESTAMP_IDS["impact_started_at"]) or inc.get("created_at")
        identified_at = extract_timestamp_by_id(inc, TIMESTAMP_IDS["identified_at"])
        fixed_at = extract_timestamp_by_id(inc, TIMESTAMP_IDS["fixed_at"])

        # Extract Native Durations by Exact ID (matching SRE script)
        mttd = extract_duration_by_id(inc, DURATION_IDS["mttd"])
        mttr = extract_duration_by_id(inc, DURATION_IDS["mttr"])
        incident_duration = extract_duration_by_id(inc, DURATION_IDS["incident_duration"])

        # Extract Custom Fields by Exact ID (matching SRE script)
        affected_team = extract_custom_field_by_id(inc, CUSTOM_FIELD_IDS["affected_team"]) or "Unassigned"
        root_cause_services = extract_custom_field_by_id(inc, CUSTOM_FIELD_IDS["root_cause_services"]) or ""
        affected_products = extract_custom_field_by_id(inc, CUSTOM_FIELD_IDS["affected_products"]) or ""
        
        # Extract Incident Lead by Role ID
        incident_lead = extract_incident_lead(inc)

        if inc_id:
            cursor.execute("""
                INSERT OR REPLACE INTO incidents (
                    id, status, severity, impact_started_at, identified_at, fixed_at,
                    mttd, mttr, incident_duration, affected_team, root_cause_services, affected_products, incident_lead
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inc_id, str(status), str(severity), impact_started_at,
                identified_at, fixed_at, mttd, mttr, incident_duration,
                affected_team, root_cause_services, affected_products, incident_lead
            ))
            seeded_count += 1

    conn.commit()

    # Write clean inspected_incidents.csv
    cursor.execute("SELECT * FROM incidents")
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    with open('inspected_incidents.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)

    conn.close()
    print(f"Successfully loaded {seeded_count} incidents using SRE field mapping into database and updated inspected_incidents.csv!")

if __name__ == "__main__":
    seed_incidents()