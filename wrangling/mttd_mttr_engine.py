import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join("database", "dora_metrics.db")

def parse_datetime(dt_str):
    """Parses standard SQLite and ISO-8601 timestamp strings safely."""
    if not dt_str:
        return None
    try:
        cleaned_str = dt_str.replace("Z", "+00:00").replace("T", " ")
        return datetime.fromisoformat(cleaned_str)
    except (ValueError, TypeError):
        return None

def calc_hours(start_str, end_str):
    """Calculates difference in seconds divided strictly by 3600."""
    start_dt = parse_datetime(start_str)
    end_dt = parse_datetime(end_str)
    if not start_dt or not end_dt:
        return None
    
    diff_seconds = (end_dt - start_dt).total_seconds()
    return round(diff_seconds / 3600.0, 2)

def compute_mttd_mttr():
    print("Computing MTTD, MTTR, and Incident Duration strictly per Prompt 5 specs...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT incident_id, impact_started_at, identified_at, fixed_at, resolved_at
            FROM raw_incidents
        """)
        rows = cursor.fetchall()

        if not rows:
            print("No raw incidents found in database. Engine ready for incoming data.")
            return

        for row in rows:
            incident_id, impact_started, identified, fixed, resolved = row

            # MTTD (hours) = (Identified At - Impact Started At) / 3600
            mttd_hours = calc_hours(impact_started, identified)

            # MTTR (hours) = (Fixed At - Impact Started At) / 3600
            mttr_hours = calc_hours(impact_started, fixed)

            # Incident Duration (hours) = (Resolved At - Impact Started At) / 3600
            duration_hours = calc_hours(impact_started, resolved)

            month_year = impact_started[:7] if impact_started else "Unknown"

            # Safely UPSERT metrics without wiping existing row data
            cursor.execute("""
                INSERT INTO calculated_dora_metrics (
                    incident_id, month_year, mttd_hours, mttr_hours, duration_hours
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    month_year = excluded.month_year,
                    mttd_hours = excluded.mttd_hours,
                    mttr_hours = excluded.mttr_hours,
                    duration_hours = excluded.duration_hours
            """, (incident_id, month_year, mttd_hours, mttr_hours, duration_hours))

        print(f"Successfully processed isolated metrics for {len(rows)} incidents.")

    except Exception as e:
        print(f"Error executing mttd_mttr_engine: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    compute_mttd_mttr()