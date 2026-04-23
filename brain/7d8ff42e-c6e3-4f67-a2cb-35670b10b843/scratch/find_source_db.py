
import sqlite3
import os
from pathlib import Path

app_data_db_dir = r"C:\Users\mchur\AppData\Roaming\PhotoSelect\databases"
files = [f for f in os.listdir(app_data_db_dir) if f.endswith(".sqlite3") and len(f) > 30]

for f in files:
    db_path = os.path.join(app_data_db_dir, f)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        sample = conn.execute("SELECT filepath FROM photos LIMIT 1").fetchone()
        print(f"File {f}: {count} photos. Example path: {sample['filepath'] if sample else 'N/A'}")
    except Exception:
        pass
    finally:
        conn.close()
