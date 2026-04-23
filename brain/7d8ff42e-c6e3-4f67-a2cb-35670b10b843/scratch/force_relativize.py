
import sqlite3
import os

db_path = r"C:\Users\mchur\Pictures\_PhotoSelect\main.sqlite3"
source_prefix = r"C:\Users\mchur\Pictures" + os.sep

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE photos SET filepath = SUBSTR(filepath, LENGTH(?) + 1) WHERE filepath LIKE (? || '%')",
            (source_prefix, source_prefix)
        )
        print(f"Relativized {cur.rowcount} paths in main.sqlite3")
        conn.commit()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
