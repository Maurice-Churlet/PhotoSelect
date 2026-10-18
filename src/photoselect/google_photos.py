"""Envoi de photos vers Google Photos (API Library, périmètre restreint post-mars 2025).

Ce que l'API permet encore (scopes `photoslibrary.appendonly` + `photoslibrary.readonly.appcreateddata`) :
- téléverser des photos, créer des albums et y ajouter les photos envoyées par PhotoSelect.
Ce qu'elle ne permet plus : partager un album par programme (403). Le partage se fait donc dans
Google Photos : on renvoie l'URL de l'album (`productUrl`) pour l'ouvrir dans le navigateur.

Secrets (règle C:\\Dev) :
- client OAuth « Application de bureau » : coffre `photoselect/google-photos-client` (JSON téléchargé
  depuis Google Cloud Console). Lu via le paquet `coffre` s'il est installé, sinon directement dans le
  Gestionnaire d'identifiants Windows (entrée `coffre:<nom>`), sinon variable
  `COFFRE_PHOTOSELECT__GOOGLE_PHOTOS_CLIENT`.
- jeton de rafraîchissement : produit par le flux OAuth, stocké par PhotoSelect dans le Gestionnaire
  d'identifiants Windows sous `photoselect:google-photos-token` (hors espace coffre, que seul Maurice alimente).
Aucune valeur secrète n'est journalisée ni incluse dans un message d'erreur.

Les originaux ne sont jamais modifiés : l'ajout ou le retrait du GPS se fait sur une copie en mémoire.
"""

from __future__ import annotations

import contextlib
import json
import logging
import mimetypes
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

try:  # Dépendances optionnelles : PhotoSelect doit démarrer même sans elles.
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    _DEPS_ERROR: str | None = None
except Exception as _exc:  # pragma: no cover - dépend de l'environnement
    AuthorizedSession = Request = Credentials = InstalledAppFlow = None  # type: ignore[assignment,misc]

    class RefreshError(Exception):  # type: ignore[no-redef]
        pass

    _DEPS_ERROR = f"{type(_exc).__name__}: {_exc}"

# En DEBUG, ces bibliothèques journalisent les requêtes OAuth complètes (client_secret, jetons) :
# on les bride, quel que soit le niveau de log choisi pour l'application.
for _noisy in ("requests_oauthlib", "oauthlib", "google_auth_oauthlib", "google.auth", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

SCOPES = [
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
]
API = "https://photoslibrary.googleapis.com/v1"
CLIENT_SECRET_NAME = "photoselect/google-photos-client"
TOKEN_TARGET = "photoselect:google-photos-token"
AUTH_TIMEOUT_S = 180  # attente maximale du consentement dans le navigateur
BATCH_CREATE_MAX = 50  # limite API par appel mediaItems:batchCreate
RETRY_CODES = (429, 500, 502, 503, 504)
# batchCreate n'est pas idempotent (jetons d'upload à usage unique) : on ne retente que les refus
# explicites où Google n'a rien créé, pas un 500 qui a pu réussir côté serveur.
BATCH_RETRY_CODES = (429, 503)
MAX_PHOTO_BYTES = 200 * 1024 * 1024  # limite Google Photos pour une image
DESCRIPTION_MAX = 1000
ALBUM_TITLE_MAX = 500
SUPPORTED_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".avif", ".ico", ".dng", ".cr2", ".nef", ".arw", ".orf", ".rw2",
}

GPS_KEEP = "keep"    # GPS de la base injecté si le fichier n'en a pas, sinon conservé tel quel
GPS_STRIP = "strip"  # GPS retiré de la copie envoyée


class GooglePhotosError(RuntimeError):
    """Erreur présentable à l'utilisateur (ne contient jamais de valeur secrète).

    ``status`` : code HTTP de la réponse API quand l'erreur en vient, sinon None.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# Erreurs qui touchent tout l'envoi (autorisation révoquée, scope, quota du projet) : inutile de
# les répéter pour chaque photo.
FATAL_HTTP = (401, 403)


def dependencies_error() -> str | None:
    """Message si les paquets Google ne sont pas installés, sinon None."""
    if _DEPS_ERROR is None:
        return None
    return (
        "Paquets Google absents. Installer dans l'environnement PhotoSelect :\n"
        "  pip install google-auth-oauthlib requests\n"
        f"(détail : {_DEPS_ERROR})"
    )


# --------------------------------------------------------------------------------------------
# Stockage des secrets : Gestionnaire d'identifiants Windows (ctypes, sans dépendance)
# --------------------------------------------------------------------------------------------
class SecretStore(Protocol):
    def read(self, target: str) -> str | None: ...
    def write(self, target: str, value: str) -> None: ...
    def delete(self, target: str) -> None: ...


class MemorySecretStore:
    """Magasin en mémoire (tests, plateformes non Windows)."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._data = dict(initial or {})

    def read(self, target: str) -> str | None:
        return self._data.get(target)

    def write(self, target: str, value: str) -> None:
        self._data[target] = value

    def delete(self, target: str) -> None:
        self._data.pop(target, None)


class WindowsCredentialStore:
    """Entrées génériques du Gestionnaire d'identifiants Windows, valeur en UTF-16 (format coffre)."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168
    MAX_BLOB = 5 * 512

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise GooglePhotosError("Gestionnaire d'identifiants Windows indisponible sur cette plateforme.")
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._ctypes = ctypes
        self._CREDENTIALW = CREDENTIALW
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self._CredReadW = adv.CredReadW
        self._CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        self._CredReadW.restype = wintypes.BOOL
        self._CredWriteW = adv.CredWriteW
        self._CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        self._CredWriteW.restype = wintypes.BOOL
        self._CredDeleteW = adv.CredDeleteW
        self._CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._CredDeleteW.restype = wintypes.BOOL
        self._CredFree = adv.CredFree
        self._CredFree.argtypes = [ctypes.c_void_p]
        self._CredFree.restype = None

    def read(self, target: str) -> str | None:
        ct = self._ctypes
        pcred = ct.POINTER(self._CREDENTIALW)()
        if not self._CredReadW(target, self.CRED_TYPE_GENERIC, 0, ct.byref(pcred)):
            err = ct.get_last_error()
            if err == self.ERROR_NOT_FOUND:
                return None
            raise GooglePhotosError(f"Lecture de l'entrée « {target} » impossible (code Windows {err}).")
        try:
            cred = pcred.contents
            raw = ct.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            self._CredFree(pcred)
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="strict")

    def write(self, target: str, value: str) -> None:
        ct = self._ctypes
        blob = value.encode("utf-16-le")
        if len(blob) > self.MAX_BLOB:
            raise GooglePhotosError(f"Valeur trop longue pour l'entrée « {target} ».")
        buf = (ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self._CREDENTIALW()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ct.cast(buf, ct.POINTER(ct.c_ubyte))
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "photoselect"
        if not self._CredWriteW(ct.byref(cred), 0):
            err = ct.get_last_error()
            raise GooglePhotosError(f"Écriture de l'entrée « {target} » impossible (code Windows {err}).")

    def delete(self, target: str) -> None:
        ct = self._ctypes
        if not self._CredDeleteW(target, self.CRED_TYPE_GENERIC, 0):
            err = ct.get_last_error()
            if err != self.ERROR_NOT_FOUND:
                raise GooglePhotosError(f"Suppression de l'entrée « {target} » impossible (code Windows {err}).")


def default_secret_store() -> SecretStore:
    if sys.platform == "win32":
        return WindowsCredentialStore()
    return MemorySecretStore()


def _env_name(secret_name: str) -> str:
    out = secret_name.replace("/", "__").replace("-", "_").replace(".", "_")
    return "COFFRE_" + out.upper()


def load_client_config(store: SecretStore | None = None) -> dict[str, Any]:
    """Charge le JSON du client OAuth depuis coffre. Lève GooglePhotosError (sans valeur) si absent/invalide."""
    raw: str | None = None
    try:
        import coffre  # type: ignore[import-not-found]

        try:
            raw = coffre.get(CLIENT_SECRET_NAME)
        except Exception:
            raw = None
    except Exception:
        pass
    if raw is None:
        st = store if store is not None else (WindowsCredentialStore() if sys.platform == "win32" else None)
        if st is not None:
            raw = st.read(f"coffre:{CLIENT_SECRET_NAME}")
    if raw is None:
        raw = os.environ.get(_env_name(CLIENT_SECRET_NAME))
    if not raw:
        raise GooglePhotosError(
            "Client OAuth Google introuvable.\n"
            f"Dans un terminal : coffre definir {CLIENT_SECRET_NAME}\n"
            "et coller le contenu du fichier JSON « Application de bureau » téléchargé depuis Google Cloud Console."
        )
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GooglePhotosError(
            f"Le secret « {CLIENT_SECRET_NAME} » n'est pas un JSON valide (ligne {exc.lineno}, colonne {exc.colno})."
        ) from None
    section = cfg.get("installed") or cfg.get("web")
    if not isinstance(section, dict) or not section.get("client_id") or not section.get("client_secret"):
        raise GooglePhotosError(
            f"Le secret « {CLIENT_SECRET_NAME} » doit être le JSON d'un client « Application de bureau » "
            "(clé « installed » avec client_id et client_secret)."
        )
    if "installed" not in cfg:
        cfg = {"installed": section}
    return cfg


# --------------------------------------------------------------------------------------------
# Préparation des octets envoyés (GPS)
# --------------------------------------------------------------------------------------------
def _to_rational(value: float) -> tuple[int, int]:
    return (round(value * 1_000_000), 1_000_000)


def _to_dms(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    a = abs(value)
    d = int(a)
    m_f = (a - d) * 60
    m = int(m_f)
    s = (m_f - m) * 60
    return ((d, 1), (m, 1), _to_rational(s))


def _valid_coords(lat: Any, lon: Any) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0) or (la == 0.0 and lo == 0.0):
        return None
    return la, lo


def prepare_upload_bytes(path: Path, gps_mode: str = GPS_KEEP, latitude: Any = None, longitude: Any = None) -> bytes:
    """Octets à envoyer. JPEG : GPS injecté (keep, si absent du fichier) ou retiré (strip), en mémoire.

    Les autres formats sont envoyés tels quels ; en mode strip, un non-JPEG lève une erreur plutôt que
    d'envoyer silencieusement une position que l'utilisateur voulait retirer.
    """
    data = Path(path).read_bytes()
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    if not is_jpeg:
        if gps_mode == GPS_STRIP:
            raise GooglePhotosError("retrait GPS supporté uniquement pour les JPEG ; fichier non envoyé")
        return data

    import piexif

    try:
        exif = piexif.load(data)
    except Exception:
        if gps_mode == GPS_STRIP:  # EXIF illisible : impossible de garantir que le GPS est retiré
            raise GooglePhotosError("EXIF illisible, retrait du GPS impossible à garantir ; fichier non envoyé") from None
        # keep : réécrire l'EXIF effacerait la date de prise de vue ; on envoie le fichier tel quel.
        return data

    gps = exif.get("GPS") or {}
    has_gps = piexif.GPSIFD.GPSLatitude in gps and piexif.GPSIFD.GPSLongitude in gps

    if gps_mode == GPS_STRIP:
        # Toujours reconstruire : le GPS peut aussi être dans un second segment Exif, dans le XMP
        # (Lightroom, darktable, Snapseed...) ou dans une image secondaire / vidéo de « motion photo ».
        exif["GPS"] = {}
    elif gps_mode == GPS_KEEP:
        coords = _valid_coords(latitude, longitude)
        if has_gps or coords is None:
            return data
        la, lo = coords
        exif["GPS"] = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: b"N" if la >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _to_dms(la),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lo >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _to_dms(lo),
        }
    else:
        raise ValueError(f"gps_mode inconnu : {gps_mode}")

    strip = gps_mode == GPS_STRIP
    try:
        exif_bytes = _dump_exif(exif)
        out = _rebuild_jpeg(data, exif_bytes, strip_private=strip)
        if strip:
            _assert_no_gps(out)
    except GooglePhotosError:
        if strip:
            raise
        return data  # keep : on envoie le fichier tel quel, sans le GPS de la base
    except Exception:
        if strip:
            raise GooglePhotosError("réécriture EXIF impossible, GPS non retiré ; fichier non envoyé") from None
        return data
    return out


def _dump_exif(exif: dict[str, Any]) -> bytes:
    """piexif.dump avec replis : tag SceneType mal typé (bug piexif 1.1.3), puis miniature corrompue."""
    import piexif

    try:
        return piexif.dump(exif)
    except Exception:
        (exif.get("Exif") or {}).pop(piexif.ExifIFD.SceneType, None)
    try:
        return piexif.dump(exif)
    except Exception:
        exif["thumbnail"] = None
        exif["1st"] = {}
    return piexif.dump(exif)


_EXIF_HEADER = b"Exif\x00\x00"
_XMP_HEADERS = (b"http://ns.adobe.com/xap/1.0/\x00", b"http://ns.adobe.com/xmp/extension/\x00")
_MPF_HEADER = b"MPF\x00"
_BAD_JPEG = "structure JPEG inattendue, GPS non vérifiable ; fichier non envoyé"


def _jpeg_segments(data: bytes) -> tuple[list[tuple[int, bytes]], int]:
    """Segments d'en-tête (marqueur, octets complets) jusqu'au premier SOS exclu, et position du SOS."""
    if data[:2] != b"\xff\xd8":
        raise GooglePhotosError(_BAD_JPEG)
    segments: list[tuple[int, bytes]] = []
    i, n = 2, len(data)
    while True:
        if i + 4 > n or data[i] != 0xFF:
            raise GooglePhotosError(_BAD_JPEG)
        marker = data[i + 1]
        if marker == 0xFF:  # octet de remplissage
            i += 1
            continue
        if marker == 0xDA:
            return segments, i
        if 0xD0 <= marker <= 0xD9:
            raise GooglePhotosError(_BAD_JPEG)
        end = i + 2 + int.from_bytes(data[i + 2:i + 4], "big")
        if end > n or end < i + 4:
            raise GooglePhotosError(_BAD_JPEG)
        segments.append((marker, data[i:end]))
        i = end


def _image_end(data: bytes, sos: int) -> int:
    """Position juste après l'EOI de l'image principale (scans progressifs compris)."""
    i, n = sos, len(data)
    while i + 2 <= n:
        if data[i] != 0xFF:
            raise GooglePhotosError(_BAD_JPEG)
        marker = data[i + 1]
        if marker == 0xD9:
            return i + 2
        if marker == 0xFF:
            i += 1
            continue
        if i + 4 > n:
            break
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
        if marker != 0xDA:
            continue
        # Données compressées : FF00 (octet échappé) et FFD0-FFD7 (RSTn) n'y sont pas des marqueurs.
        while True:
            i = data.find(b"\xff", i)
            if i < 0 or i + 1 >= n:
                raise GooglePhotosError(_BAD_JPEG)
            nxt = data[i + 1]
            if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                i += 2
            elif nxt == 0xFF:
                i += 1
            else:
                break
    raise GooglePhotosError(_BAD_JPEG)


def _rebuild_jpeg(data: bytes, exif_bytes: bytes, *, strip_private: bool) -> bytes:
    """Remplace TOUS les segments Exif par `exif_bytes` (piexif.insert n'en remplace qu'un, et seulement
    en tête). Avec `strip_private` : retire aussi XMP et MPF, et coupe tout ce qui suit l'image
    principale (images secondaires, vidéo des « motion photos »)."""
    if len(exif_bytes) + 2 > 0xFFFF:
        raise GooglePhotosError("EXIF trop volumineux pour un JPEG")
    segments, sos = _jpeg_segments(data)

    def keep(marker: int, seg: bytes) -> bool:
        body = seg[4:]
        if marker == 0xE1 and body.startswith(_EXIF_HEADER):
            return False
        if strip_private and marker == 0xE1 and body.startswith(_XMP_HEADERS):
            return False
        return not (strip_private and marker == 0xE2 and body.startswith(_MPF_HEADER))

    kept = [seg for marker, seg in segments if keep(marker, seg)]
    exif_seg = b"\xff\xe1" + (len(exif_bytes) + 2).to_bytes(2, "big") + exif_bytes
    pos = 1 if segments and segments[0][0] == 0xE0 and kept and kept[0] == segments[0][1] else 0
    kept.insert(pos, exif_seg)
    tail = data[sos:_image_end(data, sos)] if strip_private else data[sos:]
    return b"\xff\xd8" + b"".join(kept) + tail


def _assert_no_gps(data: bytes) -> None:
    """Garde-fou après retrait : un seul Exif, sans GPS ; ni XMP ni MPF ; rien après l'image."""
    import piexif

    segments, sos = _jpeg_segments(data)
    exif_count = sum(1 for m, s in segments if m == 0xE1 and s[4:].startswith(_EXIF_HEADER))
    private = any(
        (m == 0xE1 and s[4:].startswith(_XMP_HEADERS)) or (m == 0xE2 and s[4:].startswith(_MPF_HEADER))
        for m, s in segments
    )
    if exif_count > 1 or private or _image_end(data, sos) != len(data) or piexif.load(data).get("GPS"):
        raise GooglePhotosError("retrait du GPS non vérifié ; fichier non envoyé")


def build_description(country: Any = "", region: Any = "", city: Any = "") -> str:
    parts = [str(p).strip() for p in (city, region, country) if p and str(p).strip()]
    seen: list[str] = []
    for p in parts:
        if p.lower() not in (s.lower() for s in seen):
            seen.append(p)
    return ", ".join(seen)[:DESCRIPTION_MAX]


# --------------------------------------------------------------------------------------------
# Journal local des envois (évite les doublons dans un même album)
# --------------------------------------------------------------------------------------------
def default_ledger_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "PhotoSelect" / "google_photos.db"


class UploadLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS uploads (path_key TEXT, size INTEGER, mtime_ns INTEGER, gps_mode TEXT,"
                " album_id TEXT, media_item_id TEXT, uploaded_at TEXT,"
                " PRIMARY KEY (path_key, size, mtime_ns, gps_mode, album_id))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS albums (title TEXT PRIMARY KEY, album_id TEXT NOT NULL,"
                " product_url TEXT, created_at TEXT)"
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Connexion courte : transaction validée en sortie, puis fermée (le `with` de sqlite3 ne ferme pas)."""
        with contextlib.closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            yield conn

    @staticmethod
    def file_key(path: Path) -> tuple[str, int, int]:
        st = Path(path).stat()
        return os.path.normcase(str(Path(path).resolve())), int(st.st_size), int(st.st_mtime_ns)

    def is_uploaded(self, path: Path, gps_mode: str, album_id: str) -> bool:
        k, s, m = self.file_key(path)
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT 1 FROM uploads WHERE path_key=? AND size=? AND mtime_ns=? AND gps_mode=? AND album_id=?",
                (k, s, m, gps_mode, album_id),
            ).fetchone()
        return row is not None

    def record_upload(self, path: Path, gps_mode: str, album_id: str, media_item_id: str) -> None:
        k, s, m = self.file_key(path)
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO uploads VALUES (?,?,?,?,?,?,?)",
                (k, s, m, gps_mode, album_id, media_item_id, datetime.now(timezone.utc).isoformat()),
            )

    def get_album(self, title: str) -> dict[str, str] | None:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT album_id, product_url FROM albums WHERE title=?", (title,)).fetchone()
        return {"id": row[0], "productUrl": row[1] or ""} if row else None

    def save_album(self, title: str, album_id: str, product_url: str) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO albums VALUES (?,?,?,?)",
                (title, album_id, product_url, datetime.now(timezone.utc).isoformat()),
            )

    def forget_album(self, title: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("DELETE FROM albums WHERE title=?", (title,))


# --------------------------------------------------------------------------------------------
# Client API
# --------------------------------------------------------------------------------------------
class _HttpSession(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class GooglePhotosClient:
    def __init__(
        self,
        session: _HttpSession,
        *,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 120.0,
    ) -> None:
        self.session = session
        self.max_retries = max_retries
        self._sleep = sleep
        self.timeout = timeout
        self.cancel: threading.Event | None = None  # interrompt l'attente entre deux tentatives

    def _request(
        self, method: str, url: str, *, retry_codes: tuple[int, ...] = RETRY_CODES, **kwargs: Any
    ) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        delay = 1.0
        last_exc: Exception | None = None
        last_status: int | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except RefreshError:  # autorisation révoquée ou expirée : retenter ne sert à rien
                raise GooglePhotosError(
                    "Autorisation Google Photos expirée ou révoquée : relancer l'envoi pour se reconnecter.",
                    status=401,
                ) from None
            except Exception as exc:  # réseau : on retente
                last_exc = exc
                resp = None
            if resp is not None:
                code = int(resp.status_code)
                if code < 400:
                    return resp
                if code not in retry_codes:
                    raise GooglePhotosError(_api_error_message(resp), status=code)
                last_exc = GooglePhotosError(_api_error_message(resp), status=code)
                last_status = code
            if attempt < self.max_retries:
                if self.cancel is not None and self.cancel.is_set():
                    break
                self._sleep(delay)
                delay = min(delay * 2, 32.0)
        raise GooglePhotosError(f"Échec après {attempt + 1} tentative(s) : {last_exc}", status=last_status)

    def upload_bytes(self, data: bytes, filename: str, mime: str) -> str:
        resp = self._request(
            "POST",
            f"{API}/uploads",
            data=data,
            headers={
                "Content-type": "application/octet-stream",
                "X-Goog-Upload-Content-Type": mime,
                "X-Goog-Upload-Protocol": "raw",
                "X-Goog-Upload-File-Name": filename.encode("ascii", "replace").decode("ascii"),
            },
        )
        token = (resp.text or "").strip()
        if not token:
            raise GooglePhotosError("Réponse d'envoi vide (pas de jeton d'upload).")
        return token

    def batch_create(self, album_id: str | None, items: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not items:
            return []
        if len(items) > BATCH_CREATE_MAX:
            raise ValueError("batch_create : 50 éléments maximum")
        body: dict[str, Any] = {
            "newMediaItems": [
                {
                    "description": it.get("description", ""),
                    "simpleMediaItem": {"uploadToken": it["uploadToken"], "fileName": it["fileName"]},
                }
                for it in items
            ]
        }
        if album_id:
            body["albumId"] = album_id
        resp = self._request("POST", f"{API}/mediaItems:batchCreate", retry_codes=BATCH_RETRY_CODES, json=body)
        return list((resp.json() or {}).get("newMediaItemResults") or [])

    def create_album(self, title: str) -> dict[str, str]:
        resp = self._request("POST", f"{API}/albums", json={"album": {"title": title[:ALBUM_TITLE_MAX]}})
        data = resp.json() or {}
        if not data.get("id"):
            raise GooglePhotosError("Création d'album : réponse sans identifiant.")
        return {"id": data["id"], "productUrl": data.get("productUrl", "")}

    def get_album(self, album_id: str) -> dict[str, str] | None:
        try:
            resp = self._request("GET", f"{API}/albums/{album_id}")
        except GooglePhotosError as exc:
            # 404 : album supprimé côté Google ; 403 sur un album précis : plus accessible à l'app.
            if exc.status in (403, 404):
                return None
            raise
        data = resp.json() or {}
        return {"id": data.get("id", album_id), "productUrl": data.get("productUrl", "")}


def _api_error_message(resp: Any) -> str:
    code = getattr(resp, "status_code", "?")
    detail = ""
    try:
        err = (resp.json() or {}).get("error") or {}
        detail = f"{err.get('status', '')} {err.get('message', '')}".strip()
    except Exception:
        detail = (getattr(resp, "text", "") or "")[:200]
    return f"HTTP {code} {detail}".strip()


# --------------------------------------------------------------------------------------------
# Authentification
# --------------------------------------------------------------------------------------------
def _credentials_from_store(client_cfg: dict[str, Any], store: SecretStore) -> Any | None:
    raw = store.read(TOKEN_TARGET)
    if not raw:
        return None
    try:
        tok = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not tok.get("refresh_token") or not set(SCOPES).issubset(set(tok.get("scopes") or [])):
        return None
    inst = client_cfg["installed"]
    return Credentials(
        token=None,
        refresh_token=tok["refresh_token"],
        token_uri=inst.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=inst["client_id"],
        client_secret=inst["client_secret"],
        scopes=tok["scopes"],
    )


def authenticate(store: SecretStore | None = None, *, interactive: bool = True) -> Any:
    """Retourne des Credentials valides. Ouvre le navigateur pour le consentement si nécessaire."""
    err = dependencies_error()
    if err:
        raise GooglePhotosError(err)
    store = store if store is not None else default_secret_store()
    client_cfg = load_client_config(store)
    creds = _credentials_from_store(client_cfg, store)
    if creds is not None:
        try:
            creds.refresh(Request())
            return creds
        except RefreshError as exc:
            if getattr(exc, "retryable", False):  # 5xx du serveur d'authentification : jeton conservé
                raise GooglePhotosError("Connexion à Google impossible pour le moment. Réessayer plus tard.") from None
            # invalid_grant & co : jeton révoqué ou expiré (7 jours si l'app est restée en « Test »).
            logger.warning("Jeton Google Photos refusé (%s) : nouveau consentement requis.", type(exc).__name__)
            store.delete(TOKEN_TARGET)
        except Exception as exc:  # réseau : on garde le jeton, il resservira
            raise GooglePhotosError(
                f"Connexion à Google impossible ({type(exc).__name__}). Vérifier l'accès Internet."
            ) from None
    if not interactive:
        raise GooglePhotosError("Compte Google Photos non connecté.")
    flow = InstalledAppFlow.from_client_config(client_cfg, SCOPES)
    try:
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            authorization_prompt_message="",
            success_message="PhotoSelect est autorisé à envoyer vers Google Photos. Vous pouvez fermer cet onglet.",
            timeout_seconds=AUTH_TIMEOUT_S,
            # Sans « consent », Google ne renvoie pas de jeton de rafraîchissement lors d'une
            # nouvelle autorisation (après « déconnecter le compte » ou un jeton révoqué).
            prompt="consent",
        )
    except Exception as exc:  # délai dépassé, refus de l'utilisateur, scope décoché, réseau
        name = type(exc).__name__
        if "Timeout" in name:
            msg = f"Pas de réponse du navigateur en {AUTH_TIMEOUT_S // 60} min : autorisation abandonnée."
        elif "AccessDenied" in name:
            msg = "Autorisation refusée dans le navigateur."
        else:
            msg = f"Autorisation Google impossible ({name}). Accepter toutes les permissions demandées."
        raise GooglePhotosError(msg) from None
    if not set(SCOPES).issubset(set(getattr(creds, "scopes", None) or SCOPES)):
        raise GooglePhotosError("Toutes les permissions demandées doivent être cochées dans le navigateur.")
    if not getattr(creds, "refresh_token", None):
        raise GooglePhotosError("Google n'a pas fourni de jeton de rafraîchissement.")
    token_doc = {"refresh_token": creds.refresh_token, "scopes": list(creds.scopes or SCOPES)}
    store.write(TOKEN_TARGET, json.dumps(token_doc))
    return creds


def disconnect(store: SecretStore | None = None) -> None:
    """Oublie le jeton local (révocation côté Google : myaccount.google.com/permissions)."""
    (store if store is not None else default_secret_store()).delete(TOKEN_TARGET)


def make_client(creds: Any) -> GooglePhotosClient:
    return GooglePhotosClient(AuthorizedSession(creds))


# --------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------
@dataclass
class UploadItem:
    path: Path
    latitude: Any = None
    longitude: Any = None
    description: str = ""


@dataclass
class UploadReport:
    album_title: str = ""
    album_id: str = ""
    album_url: str = ""
    uploaded: int = 0
    skipped_already: int = 0
    skipped_unsupported: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    aborted: str = ""  # erreur qui a interrompu tout l'envoi (autorisation, quota du projet)

    @property
    def total_failed(self) -> int:
        return len(self.failed)


ProgressCb = Callable[[int, int, str], None]


def resolve_album(client: GooglePhotosClient, ledger: UploadLedger, title: str) -> dict[str, str]:
    """Album créé par PhotoSelect portant ce titre (réutilisé s'il existe toujours), sinon nouveau."""
    cached = ledger.get_album(title)
    if cached:
        live = client.get_album(cached["id"])
        if live is not None:
            url = live.get("productUrl") or cached.get("productUrl", "")
            if url != cached.get("productUrl"):
                ledger.save_album(title, cached["id"], url)
            return {"id": cached["id"], "productUrl": url}
        ledger.forget_album(title)
    album = client.create_album(title)
    ledger.save_album(title, album["id"], album["productUrl"])
    return album


def upload_items(
    client: GooglePhotosClient,
    ledger: UploadLedger,
    items: list[UploadItem],
    album_title: str,
    *,
    gps_mode: str = GPS_KEEP,
    progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> UploadReport:
    title = (album_title or "").strip()[:ALBUM_TITLE_MAX]
    if not title:
        raise GooglePhotosError("Titre d'album vide.")
    report = UploadReport(album_title=title)
    client.cancel = cancel
    album = resolve_album(client, ledger, title)
    report.album_id, report.album_url = album["id"], album["productUrl"]

    total = len(items)
    pending: list[tuple[UploadItem, dict[str, str]]] = []

    def flush() -> None:
        if not pending:
            return
        batch = list(pending)
        pending.clear()
        try:
            results = client.batch_create(album["id"], [meta for _it, meta in batch])
        except GooglePhotosError as exc:
            report.failed.extend((str(it.path), str(exc)) for it, _m in batch)
            if exc.status in FATAL_HTTP:
                report.aborted = str(exc)
            return
        by_token = {str(r.get("uploadToken", "")): r for r in results}
        for idx, (it, meta) in enumerate(batch):
            res = by_token.get(meta["uploadToken"]) or (results[idx] if idx < len(results) else {})
            status = res.get("status") or {}
            media = res.get("mediaItem") or {}
            code = int(status.get("code", 0) or 0)
            if code == 0 and media.get("id"):
                report.uploaded += 1
                try:
                    ledger.record_upload(it.path, gps_mode, album["id"], media["id"])
                except (OSError, sqlite3.Error) as exc:  # l'envoi a réussi : seul l'anti-doublon est perdu
                    logger.warning("[gphotos] journal non mis à jour pour %s (%s)", it.path, exc)
            else:
                report.failed.append((str(it.path), status.get("message") or f"code {code}"))

    for idx, it in enumerate(items, start=1):
        if cancel is not None and cancel.is_set():
            report.cancelled = True
            break
        p = Path(it.path)
        if progress:
            progress(idx - 1, total, p.name)
        try:
            if p.suffix.lower() not in SUPPORTED_EXT:
                report.skipped_unsupported += 1
                continue
            if not p.is_file():
                report.failed.append((str(p), "fichier introuvable"))
                continue
            if ledger.is_uploaded(p, gps_mode, album["id"]):
                report.skipped_already += 1
                continue
            data = prepare_upload_bytes(p, gps_mode, it.latitude, it.longitude)
            if len(data) > MAX_PHOTO_BYTES:
                report.failed.append((str(p), "fichier > 200 Mo (limite Google Photos)"))
                continue
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            token = client.upload_bytes(data, p.name, mime)
            meta = {"uploadToken": token, "fileName": p.name, "description": it.description[:DESCRIPTION_MAX]}
            pending.append((it, meta))
        except GooglePhotosError as exc:
            report.failed.append((str(p), str(exc)))
            if exc.status in FATAL_HTTP:
                report.aborted = str(exc)
        except Exception as exc:
            report.failed.append((str(p), f"{type(exc).__name__}: {exc}"))
        if len(pending) >= BATCH_CREATE_MAX:
            flush()
        if report.aborted:
            break
    if not report.aborted:
        flush()  # sinon les jetons en attente échoueraient pour la même raison
    else:
        report.failed.extend((str(it.path), report.aborted) for it, _m in pending)
        pending.clear()
    if progress:
        progress(total if not report.cancelled else idx - 1, total, "")
    return report
