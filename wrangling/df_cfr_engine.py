import os
import sqlite3

DB_PATH = os.path.join("database", "dora_metrics.db")

def create_consolidated_view(cursor):
    """Replicates the exact SQL view from Prompt 6."""
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS v_change_failure_consolidated AS
        SELECT service_name AS group_name, deployed_at, status, 'github' AS source
        FROM deployments WHERE source = 'github'
        UNION ALL
        SELECT service_name AS group_name, deployed_at, status, 'jira' AS source
        FROM deployments WHERE source = 'jira';
    """)

def compute_df_and_cfr():
    print("Executing DF & CFR Engine (Daily, Weekly, Monthly aggregations)...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Create SQL View strictly matching Prompt 6
        create_consolidated_view(cursor)

        # 2. Define Day, Week, and Month time buckets for DF aggregation
        time_frames = [
            ("day", "%Y-%m-%d"),
            ("week", "%Y-%W"),
            ("month", "%Y-%m")
        ]

        total_processed = 0

        for frame_label, date_fmt in time_frames:
            # Deployment Frequency (DF) = sum of all deployments per Day/Week/Month
            cursor.execute(f"""
                SELECT group_name, strftime('{date_fmt}', deployed_at) as time_bucket, COUNT(*) as total_deployments
                FROM v_change_failure_consolidated
                WHERE deployed_at IS NOT NULL
                GROUP BY group_name, time_bucket
            """)
            aggregates = cursor.fetchall()

            for group_name, time_bucket, total_deps in aggregates:
                # Count deployment-triggered incidents matching service or team in that time window
                cursor.execute(f"""
                    SELECT COUNT(*) FROM raw_incidents
                    WHERE (root_cause_service = ? OR affected_team = ?)
                      AND strftime('{date_fmt}', impact_started_at) = ?
                """, (group_name, group_name, time_bucket))
                incidents_count = cursor.fetchone()[0]

                # CFR = (Total Deployment-Triggered Incidents / Total Consolidated Deployments) * 100
                cfr_pct = round((incidents_count / total_deps) * 100.0, 2) if total_deps > 0 else 0.0

                summary_id = f"df_cfr_{group_name}_{frame_label}_{time_bucket}"
                
                # 3. Save both DF and CFR outputs to database
                cursor.execute("""
                    INSERT INTO calculated_dora_metrics (
                        incident_id, month_year, deployment_frequency, change_failure_rate_pct
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(incident_id) DO UPDATE SET
                        month_year = excluded.month_year,
                        deployment_frequency = excluded.deployment_frequency,
                        change_failure_rate_pct = excluded.change_failure_rate_pct
                """, (summary_id, time_bucket, total_deps, cfr_pct))

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