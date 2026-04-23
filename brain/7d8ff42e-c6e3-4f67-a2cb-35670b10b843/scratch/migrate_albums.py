
import shutil
import os

src = r"C:\Users\mchur\AppData\Roaming\PhotoSelect\databases\_album_index.sqlite3"
dst = r"C:\Users\mchur\Pictures\_PhotoSelect\album_index.sqlite3"

if os.path.exists(src) and not os.path.exists(dst):
    shutil.copy2(src, dst)
    print(f"Migrated Virtual Albums to {dst}")
else:
    print("Virtual Albums already migrated or source not found.")
