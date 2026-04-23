
import sqlite3
import os

db_path = r"C:\Users\mchur\Pictures\_PhotoSelect\main.sqlite3"
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found")
else:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        print(f"Tables: {[t['name'] for t in tables]}")
        
        for table in [t['name'] for t in tables]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"Table {table}: {count} rows")
            
        if 'photos' in [t['name'] for t in tables]:
            first = conn.execute("SELECT * FROM photos LIMIT 1").fetchone()
            if first:
                print(f"Sample row in photos: {dict(first)}")
    except Exception as e:
        print(f"Error inspecting DB: {e}")
    finally:
        conn.close()
