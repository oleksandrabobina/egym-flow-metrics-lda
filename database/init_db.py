import os
import sqlite3

def init_database():
    db_path = os.path.join("database", "dora_metrics.db")
    schema_path = os.path.join("database", "schema.sql")

    print(f"Initializing database at {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    with open(schema_path, "r") as f:
        sql_script = f.read()

    cursor.executescript(sql_script)
    conn.commit()
    conn.close()

    print("Database initialized successfully!")

if __name__ == "__main__":
    init_database()