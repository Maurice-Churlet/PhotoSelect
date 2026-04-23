
import sqlite3
import os

db_path = r"C:\Users\mchur\Pictures\_PhotoSelect\main.sqlite3"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT thumb_relpath FROM photo_faces LIMIT 5").fetchone()
    if row:
        print(f"Sample: {row[0]}")
    conn.close()
else:
    print("DB not found")
