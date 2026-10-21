import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def run_migration():
    db_url = os.getenv("DATABASE_POSTGRESQL_URL")
    if not db_url:
        print("[ERROR] DATABASE_POSTGRESQL_URL not found in environment variables.")
        return False

    sql_file = os.path.join(os.path.dirname(__file__), "migrations", "create_notifications_table.sql")
    if not os.path.exists(sql_file):
        print(f"[ERROR] Migration file not found: {sql_file}")
        return False

    with open(sql_file, "r", encoding="utf-8") as f:
        sql = f.read()

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        print("[INFO] Applying migration: migrations/create_notifications_table.sql ...")
        cur.execute(sql)
        for notice in conn.notices:
            print(f"  {notice.strip()}")
        cur.close()
        conn.close()
        print("[SUCCESS] Notifications migration applied successfully.")
        return True
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        return False

if __name__ == "__main__":
    run_migration()
