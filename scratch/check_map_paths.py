import sys
from pathlib import Path

def get_resource_path(relative_path: str) -> Path:
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        # Assuming app.py is in src/photoselect/app.py
        base_path = Path(__file__).resolve().parent.parent.parent
    return base_path / relative_path

print(f"__file__: {__file__}")
print(f"CWD: {Path.cwd()}")

candidates = [
    get_resource_path("src/photoselect/assets/world_countries.geojson"),
    Path(__file__).resolve().parent.joinpath("src", "photoselect", "assets", "world_countries.geojson"), # Incorrect if we are in scratch
    Path("src/photoselect/assets/world_countries.geojson"),
]

for p in candidates:
    print(f"Candidate: {p} | Exists: {p.exists()}")

# Manual check of the known location
real_path = Path("c:/Dev/PhotoSelect/src/photoselect/assets/world_countries.geojson")
print(f"Real Path: {real_path} | Exists: {real_path.exists()}")
