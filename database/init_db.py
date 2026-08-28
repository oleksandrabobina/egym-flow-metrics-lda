import os
import sqlite3

DB_PATH = os.path.join("database", "dora_metrics.db")
SCHEMA_PATH = os.path.join("database", "schema.sql")

def init_db():
    print(f"Initializing database schema at {DB_PATH}...")
    if not os.path.exists(SCHEMA_PATH):
        print(f"Error: Schema file not found at {SCHEMA_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        with open(SCHEMA_PATH, "r") as f:
            schema_sql = f.read()
        conn.executescript(schema_sql)
        conn.commit()
        print("Database schema initialized successfully.")
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()