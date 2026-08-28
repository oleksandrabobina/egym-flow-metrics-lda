import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join("database", "dora_metrics.db")

def create_consolidated_view(cursor):
    """Recreates the SQL view with updated columns."""
    cursor.execute("DROP VIEW IF EXISTS v_change_failure_consolidated;")
    cursor.execute("""
        CREATE VIEW v_change_failure_consolidated AS
        SELECT service_name AS group_name, realm_name, team_name, deployed_at, status, 'github' AS source
        FROM deployments WHERE source = 'github'
        UNION ALL
        SELECT service_name AS group_name, realm_name, team_name, deployed_at, status, 'jira' AS source
        FROM deployments WHERE source = 'jira';
    """)

def init_summary_table(cursor):
    """Ensures the dedicated monthly_df_cfr storage table exists in SQLite."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monthly_df_cfr (
            id VARCHAR(100) PRIMARY KEY,
            realm_name VARCHAR(100),
            time_frame VARCHAR(20),  -- 'day', 'week', 'month'
            time_bucket VARCHAR(20),  -- '2026-01-15', '2026-03', etc.
            total_deployments INTEGER DEFAULT 0,
            successful_deployments INTEGER DEFAULT 0,
            failed_deployments INTEGER DEFAULT 0,
            cfr_percent REAL DEFAULT 0.0,
            incident_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

def get_incidents_realm_column(cursor):
    """Safely determines whether raw_incidents uses 'realm' or 'realm_name'."""
    cursor.execute("PRAGMA table_info(raw_incidents);")
    cols = [row[1] for row in cursor.fetchall()]
    if "realm_name" in cols:
        return "realm_name"
    elif "realm" in cols:
        return "realm"
    return None

def compute_df_and_cfr():
    print("Executing DF & CFR Engine (Daily, Weekly, Monthly aggregations)...")
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Recreate view to bind freshly added database columns
        create_consolidated_view(cursor)
        init_summary_table(cursor)

        incidents_col = get_incidents_realm_column(cursor)

        # 2. Define Day, Week, and Month time buckets for DF aggregation
        time_frames = [
            ("day", "%Y-%m-%d"),
            ("week", "%Y-%W"),
            ("month", "%Y-%m")
        ]

        total_processed = 0

        for frame_label, date_fmt in time_frames:
            # Aggregate total, success, and failure counts per Realm and Time Bucket
            cursor.execute(f"""
                SELECT 
                    realm_name,
                    strftime('{date_fmt}', deployed_at) as time_bucket,
                    COUNT(*) as total_deployments,
                    SUM(CASE WHEN LOWER(status) = 'success' THEN 1 ELSE 0 END) as success_deploys,
                    SUM(CASE WHEN LOWER(status) = 'failure' THEN 1 ELSE 0 END) as fail_deploys
                FROM v_change_failure_consolidated
                WHERE deployed_at IS NOT NULL 
                  AND realm_name IS NOT NULL 
                  AND realm_name != 'Unassigned'
                GROUP BY realm_name, time_bucket
            """)
            aggregates = cursor.fetchall()

            for realm_name, time_bucket, total_deps, success_deps, fail_deps in aggregates:
                if not time_bucket:
                    continue

                # Count incident occurrences matching this realm in the same time window
                incidents_count = 0
                if incidents_col:
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM raw_incidents
                        WHERE {incidents_col} = ?
                          AND strftime('{date_fmt}', impact_started_at) = ?
                    """, (realm_name, time_bucket))
                    res = cursor.fetchone()
                    incidents_count = res[0] if res else 0

                # CFR calculation: Percentage of failed rollouts
                cfr_pct = round((fail_deps / total_deps) * 100.0, 2) if total_deps > 0 else 0.0

                summary_id = f"df_cfr_{realm_name}_{frame_label}_{time_bucket}"

                # 3. Save aggregated DF and CFR outputs to monthly_df_cfr table
                cursor.execute("""
                    INSERT INTO monthly_df_cfr (
                        id, realm_name, time_frame, time_bucket,
                        total_deployments, successful_deployments, failed_deployments,
                        cfr_percent, incident_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        total_deployments = excluded.total_deployments,
                        successful_deployments = excluded.successful_deployments,
                        failed_deployments = excluded.failed_deployments,
                        cfr_percent = excluded.cfr_percent,
                        incident_count = excluded.incident_count,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    summary_id, realm_name, frame_label, time_bucket,
                    total_deps, success_deps, fail_deps,
                    cfr_pct, incidents_count
                ))

                total_processed += 1

        if total_processed == 0:
            print("View v_change_failure_consolidated created. Engine ready for incoming data.")
        else:
            print(f"Successfully processed DF & CFR across Day/Week/Month windows ({total_processed} aggregations saved).")

    except Exception as e:
        print(f"Error executing df_cfr_engine: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    compute_df_and_cfr()