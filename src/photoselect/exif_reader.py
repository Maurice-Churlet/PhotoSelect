# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

"""Lecture et extraction des métadonnées EXIF des fichiers image (y compris GPS)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import urllib.parse
import urllib.request
import time
import csv
import os
from dataclasses import dataclass

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS, IFD

logger = logging.getLogger(__name__)

# --- MOTEUR GÉO LOCAL LIGHT (Zéro-Dépendance) ---
@dataclass
class City:
    name: str
    admin1: str
    cc: str
    lat: float
    lon: float

_CITIES: list[City] = []
_LOADED = False
_GEO_CACHE: dict[tuple[float, float], dict[str, str]] = {}

def _init_cities_db():
    """Charge les villes depuis le CSV sans importer le module problématique."""
    global _CITIES, _LOADED
    if _LOADED: return
    try:
        import sys
        # On cherche le dossier du package dans sys.path sans l'importer
        csv_path = None
        for p in sys.path:
            potential = os.path.join(p, 'reverse_geocoder', 'rg_cities1000.csv')
            if os.path.exists(potential):
                csv_path = potential
                break
        
        if csv_path:
            logger.info(f"Moteur Local Light : Chargement de {csv_path}")
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    _CITIES.append(City(
                        name=row['name'],
                        admin1=row['admin1'],
                        cc=row['cc'],
                        lat=float(row['lat']),
                        lon=float(row['lon'])
                    ))
            _LOADED = True
            logger.info("Moteur Local Light prêt (%d villes).", len(_CITIES))
        else:
            logger.warning("Fichier rg_cities1000.csv introuvable dans sys.path.")
    except Exception as e:
        logger.warning("Impossible de charger le CSV local : %s", e)

# On tente l'init sans bloquer l'import
try:
    _init_cities_db()
except Exception: pass


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return "".join(c for c in value.lower().strip() if c.isalnum())


def _parse_exif_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _oldest_available_datetime(filepath: Path, exif: dict[str, Any]) -> str:
    exif_dates: list[datetime] = []
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        parsed = _parse_exif_datetime(exif.get(key))
        if parsed is not None:
            exif_dates.append(parsed)

    if exif_dates:
        return min(exif_dates).strftime("%Y:%m:%d %H:%M:%S")

    try:
        stat = filepath.stat()
        fs_candidates = [
            datetime.fromtimestamp(stat.st_mtime),
            datetime.fromtimestamp(stat.st_ctime),
        ]
        birth_ts = getattr(stat, "st_birthtime", None)
        if birth_ts is not None:
            fs_candidates.append(datetime.fromtimestamp(birth_ts))
        return min(fs_candidates).strftime("%Y:%m:%d %H:%M:%S")
    except Exception:
        return ""


def read_exif(filepath: Path) -> dict[str, Any]:
    """Lit les données EXIF standards d'un fichier image."""
    exif_data: dict[str, Any] = {}
    try:
        with Image.open(filepath) as img:
            raw_exif = img.getexif()
            if not raw_exif:
                return exif_data
            
            for tag_id, value in raw_exif.items():
                tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="replace")
                    except Exception:
                        value = repr(value)
                exif_data[tag_name] = value

            for ifd_id in (IFD.Exif, IFD.GPS):
                try:
                    ifd = raw_exif.get_ifd(ifd_id)
                    for tag, value in ifd.items():
                        decoded = TAGS.get(tag, tag) if ifd_id == IFD.Exif else GPSTAGS.get(tag, tag)
                        exif_data[decoded] = value
                except Exception:
                    continue
    except Exception as exc:
        logger.debug("Erreur lecture EXIF %s: %s", filepath.name, exc)
    return exif_data


def _convert_to_decimal(value: tuple) -> float | None:
    try:
        d, m, s = value
        return float(d) + (float(m) / 60.0) + (float(s) / 3600.0)
    except Exception: return None


def read_gps_exif(filepath: Path) -> dict[str, float]:
    gps_data = {}
    try:
        with Image.open(filepath) as img:
            raw_exif = img.getexif()
            gps_ifd = raw_exif.get_ifd(IFD.GPSInfo)
            if gps_ifd:
                gps_info = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                lat = _convert_to_decimal(gps_info.get("GPSLatitude"))
                lon = _convert_to_decimal(gps_info.get("GPSLongitude"))
                if lat is not None and lon is not None:
                    if gps_info.get("GPSLatitudeRef") == "S": lat = -lat
                    if gps_info.get("GPSLongitudeRef") == "W": lon = -lon
                    gps_data["latitude"] = lat
                    gps_data["longitude"] = lon
    except Exception: pass
    return gps_data


def get_location_details(lat: float, lon: float) -> dict[str, str]:
    """Recherche la ville la plus proche (Moteur Local Light + Cache)."""
    grid_key = (round(lat, 3), round(lon, 3))
    if grid_key in _GEO_CACHE:
        return _GEO_CACHE[grid_key]

    if not _LOADED:
        return {"country": "", "region": "", "city": ""}

    best_dist = float('inf')
    best_city = None
    for city in _CITIES:
        d = (city.lat - lat)**2 + (city.lon - lon)**2
        if d < best_dist:
            best_dist = d
            best_city = city

    if best_city:
        res = {"country": best_city.cc, "region": best_city.admin1, "city": best_city.name}
        _GEO_CACHE[grid_key] = res
        return res
    return {"country": "", "region": "", "city": ""}


def find_city_candidates(city_name: str, limit: int = 30) -> list[dict[str, Any]]:
    """Retourne des candidats de localisation (ville/région/pays/GPS) depuis la base locale offline."""
    _init_cities_db()
    norm_query = _normalize_text(city_name)
    if not norm_query or not _LOADED:
        return []

    exact: list[dict[str, Any]] = []
    fuzzy: list[dict[str, Any]] = []

    for city in _CITIES:
        norm_name = _normalize_text(city.name)
        if not norm_name:
            continue
        item = {
            "city": city.name,
            "region": city.admin1,
            "country": city.cc,
            "latitude": city.lat,
            "longitude": city.lon,
            "source": "Base locale"
        }
        if norm_name == norm_query:
            exact.append(item)
        elif not exact and norm_name.startswith(norm_query):
            fuzzy.append(item)

    results = exact if exact else fuzzy
    dedup: dict[tuple[str, str, str, float, float], dict[str, Any]] = {}
    for item in results:
        key = (
            item["city"],
            item["region"],
            item["country"],
            round(float(item["latitude"]), 5),
            round(float(item["longitude"]), 5),
        )
        dedup[key] = item

    return list(dedup.values())[:max(1, limit)]


def find_city_candidates_online(city_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recherche en ligne des candidats ville/région/pays/GPS (Nominatim)."""
    city_name = city_name.strip()
    if not city_name:
        return []

    try:
        query = urllib.parse.quote(city_name)
        url = (
            "https://nominatim.openstreetmap.org/search"
            f"?q={query}&format=jsonv2&addressdetails=1&limit={max(1, min(limit, 25))}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "PhotoSelect/1.1"})
        time.sleep(0.5)
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode())

        results: list[dict[str, Any]] = []
        for item in data:
            addr = item.get("address", {}) if isinstance(item, dict) else {}
            city = addr.get("city") or addr.get("town") or addr.get("village") or city_name
            region = addr.get("state") or addr.get("county") or ""
            country = (addr.get("country_code") or "").upper() or (addr.get("country") or "")
            lat = float(item.get("lat")) if item.get("lat") is not None else None
            lon = float(item.get("lon")) if item.get("lon") is not None else None
            results.append(
                {
                    "city": city,
                    "region": region,
                    "country": country,
                    "latitude": lat,
                    "longitude": lon,
                    "source": "En ligne",
                }
            )

        dedup: dict[tuple[str, str, str, float | None, float | None], dict[str, Any]] = {}
        for r in results:
            key = (
                str(r.get("city") or "").strip(),
                str(r.get("region") or "").strip(),
                str(r.get("country") or "").strip(),
                round(float(r["latitude"]), 5) if r.get("latitude") is not None else None,
                round(float(r["longitude"]), 5) if r.get("longitude") is not None else None,
            )
            dedup[key] = r
        return list(dedup.values())
    except Exception as exc:
        logger.debug("Recherche online ville impossible (%s): %s", city_name, exc)
        return []


def _to_rational(value: float) -> tuple[int, int]:
    scaled = int(round(value * 1000000))
    return (scaled, 1000000)


def _to_dms(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    abs_val = abs(value)
    degrees = int(abs_val)
    minutes_float = (abs_val - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return ((degrees, 1), (minutes, 1), _to_rational(seconds))


def update_image_exif_location(
    filepath: Path,
    latitude: float | None,
    longitude: float | None,
    country: str = "",
    region: str = "",
    city: str = ""
) -> bool:
    """Met à jour les métadonnées EXIF GPS d'un JPEG et ajoute une description de localisation."""
    if filepath.suffix.lower() not in (".jpg", ".jpeg"):
        return False

    try:
        import piexif
        from piexif.helper import UserComment
    except Exception as exc:
        logger.warning("piexif indisponible: %s", exc)
        return False

    try:
        exif_dict = piexif.load(str(filepath))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    # Assure la structure attendue par piexif même si le fichier source est atypique.
    exif_dict.setdefault("0th", {})
    exif_dict.setdefault("Exif", {})
    exif_dict.setdefault("GPS", {})
    exif_dict.setdefault("1st", {})
    exif_dict.setdefault("thumbnail", None)

    gps_ifd = exif_dict.get("GPS", {})
    if latitude is not None and longitude is not None:
        gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = b"N" if latitude >= 0 else b"S"
        gps_ifd[piexif.GPSIFD.GPSLatitude] = _to_dms(latitude)
        gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = b"E" if longitude >= 0 else b"W"
        gps_ifd[piexif.GPSIFD.GPSLongitude] = _to_dms(longitude)
    else:
        gps_ifd.pop(piexif.GPSIFD.GPSLatitudeRef, None)
        gps_ifd.pop(piexif.GPSIFD.GPSLatitude, None)
        gps_ifd.pop(piexif.GPSIFD.GPSLongitudeRef, None)
        gps_ifd.pop(piexif.GPSIFD.GPSLongitude, None)
    exif_dict["GPS"] = gps_ifd

    location_text = " | ".join([p for p in [country.strip(), region.strip(), city.strip()] if p])
    if location_text:
        exif_dict.setdefault("0th", {})[piexif.ImageIFD.ImageDescription] = location_text.encode("utf-8", errors="ignore")
        exif_dict.setdefault("Exif", {})[piexif.ExifIFD.UserComment] = UserComment.dump(location_text, encoding="unicode")
    else:
        exif_dict.setdefault("0th", {}).pop(piexif.ImageIFD.ImageDescription, None)
        exif_dict.setdefault("Exif", {}).pop(piexif.ExifIFD.UserComment, None)

    try:
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(filepath))
        logger.info("EXIF ecrit (mode normal): %s", filepath)
        return True
    except Exception as exc:
        # Certains fichiers contiennent des tags EXIF non conformes
        # (ex: type inattendu) que piexif refuse au dump.
        logger.warning("Dump EXIF initial impossible pour %s: %s", filepath, exc)

    # Fallback robuste: repartir d'une structure minimale saine et n'écrire
    # que les blocs utiles à PhotoSelect (GPS + description localisation).
    try:
        clean_exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        if "GPS" in exif_dict and isinstance(exif_dict.get("GPS"), dict):
            clean_exif["GPS"] = dict(exif_dict["GPS"])
        if "0th" in exif_dict and isinstance(exif_dict.get("0th"), dict):
            # Conserve Orientation si présente pour éviter une régression d'affichage.
            orient_tag = piexif.ImageIFD.Orientation
            if orient_tag in exif_dict["0th"]:
                clean_exif["0th"][orient_tag] = exif_dict["0th"][orient_tag]

        gps_ifd = clean_exif.get("GPS", {})
        if latitude is not None and longitude is not None:
            gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = b"N" if latitude >= 0 else b"S"
            gps_ifd[piexif.GPSIFD.GPSLatitude] = _to_dms(latitude)
            gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = b"E" if longitude >= 0 else b"W"
            gps_ifd[piexif.GPSIFD.GPSLongitude] = _to_dms(longitude)
        else:
            gps_ifd.pop(piexif.GPSIFD.GPSLatitudeRef, None)
            gps_ifd.pop(piexif.GPSIFD.GPSLatitude, None)
            gps_ifd.pop(piexif.GPSIFD.GPSLongitudeRef, None)
            gps_ifd.pop(piexif.GPSIFD.GPSLongitude, None)
        clean_exif["GPS"] = gps_ifd

        if location_text:
            clean_exif["0th"][piexif.ImageIFD.ImageDescription] = location_text.encode("utf-8", errors="ignore")
            clean_exif["Exif"][piexif.ExifIFD.UserComment] = UserComment.dump(location_text, encoding="unicode")
        else:
            clean_exif["0th"].pop(piexif.ImageIFD.ImageDescription, None)
            clean_exif["Exif"].pop(piexif.ExifIFD.UserComment, None)

        exif_bytes = piexif.dump(clean_exif)
        piexif.insert(exif_bytes, str(filepath))
        logger.info("EXIF ecrit (mode fallback): %s", filepath)
        return True
    except Exception as exc:
        logger.warning("Impossible d'écrire EXIF pour %s: %s", filepath, exc)
        return False


def _get_location_online(lat: float, lon: float) -> dict[str, str]:
    """Fallback en ligne si besoin."""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&addressdetails=1"
        headers = {"User-Agent": "PhotoSelect/1.1"}
        req = urllib.request.Request(url, headers=headers)
        time.sleep(1.0)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            addr = data.get("address", {})
            return {
                "country": addr.get("country_code", "").upper(),
                "region": addr.get("state", addr.get("county", "")),
                "city": addr.get("city", addr.get("town", addr.get("village", "")))
            }
    except Exception: pass
    return {"country": "", "region": "", "city": ""}


def get_location_details_online(lat: float, lon: float) -> dict[str, str]:
    """Recherche de lieu en ligne (Nominatim) pour un couple latitude/longitude."""
    return _get_location_online(lat, lon)


def extract_all_metadata(filepath: Path, mode: str = "local") -> dict[str, Any]:
    exif = read_exif(filepath)
    gps = read_gps_exif(filepath)
    date_taken = _oldest_available_datetime(filepath, exif)
    
    location = {"country": "", "region": "", "city": ""}
    if "latitude" in gps and "longitude" in gps:
        if mode == "online":
            location = _get_location_online(gps["latitude"], gps["longitude"])
        else:
            location = get_location_details(gps["latitude"], gps["longitude"])

    return {
        "filepath": str(filepath),
        "filename": filepath.name,
        "date_taken": date_taken,
        "camera_model": str(exif.get("Model", "")),
        "latitude": gps.get("latitude"),
        "longitude": gps.get("longitude"),
        "country": location["country"],
        "region": location["region"],
        "city": location["city"],
    }
