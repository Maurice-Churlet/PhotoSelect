# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

"""Gestion de la base de données SQLite locale."""

from __future__ import annotations

import logging
import math
import json
import sqlite3
from pathlib import Path
from typing import Any
from collections import defaultdict
import re
import time
import unicodedata

logger = logging.getLogger(__name__)


def _extract_yymmdd(date_str: str | None) -> str:
    """Extrait l'année (2 digits), le mois et le jour depuis une date EXIF."""
    if not date_str:
        return "000000"
    
    # Formats classiques : YYYY:MM:DD HH:MM:SS ou YYYY-MM-DD
    match = re.search(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", date_str)
    if match:
        year, month, day = match.groups()
        return f"{year[-2:]}{month}{day}"
    return "000000"


def _clean_string(val: str | None) -> str:
    """Nettoie une chaîne pour le nom de fichier (enlève espaces superflus etc.)."""
    if not val:
        return ""
    # Remplacer les caractères problématiques si besoin
    cleaned = re.sub(r'[\\/*?:"<>|]', "", val).strip()
    return cleaned


def _normalize_person_name(val: str | None) -> str:
    """Normalise un nom de personne (sans accents, minuscules, espaces réduits)."""
    raw = str(val or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    no_accents = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents).strip()


def _normalize_virtual_album_name(val: str | None) -> str:
    """Normalise un nom d'album virtuel pour les comparaisons métier."""
    return _normalize_person_name(val)


class Database:
    """Gestionnaire de la base de données SQLite."""

    def __init__(self, db_path: Path):
        """Initialise la connexion à la base de données."""
        self.db_path = db_path
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        
        # Optimisation pour la concurrence (WAL Mode)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
            
        self._create_table()

    def _create_table(self) -> None:
        """Crée la table `photos` si elle n'existe pas avec collection_name."""
        query = """
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            filesize INTEGER NOT NULL,
            date_taken TEXT,
            camera_model TEXT,
            latitude REAL,
            longitude REAL,
            country TEXT,
            region TEXT,
            city TEXT,
            collection_name TEXT,
            is_copied INTEGER DEFAULT 0
        )
        """
        try:
            with self._conn:
                self._conn.execute(query)
            logger.info("Table `photos` prête.")
        except sqlite3.Error as e:
            logger.error("Erreur de création de table : %s", e)

        # Migration si la DB a été créée avant la mise à jour
        try:
            with self._conn:
                self._conn.execute("ALTER TABLE photos ADD COLUMN is_copied INTEGER DEFAULT 0")
            logger.info("Migration SQLite effectuée : colonne is_copied ajoutée.")
        except sqlite3.OperationalError:
            pass  # La colonne existe déjà

        # Index des doublons de signature (taille + titre + date)
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS photo_duplicate_flags (
                        photo_id INTEGER PRIMARY KEY,
                        dup_key TEXT NOT NULL,
                        dup_count INTEGER NOT NULL DEFAULT 1,
                        indexed_at INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS duplicate_index_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        last_built_at INTEGER NOT NULL DEFAULT 0,
                        photo_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
        except sqlite3.Error as e:
            logger.error("Erreur création tables doublons : %s", e)

        # Schéma relationnel visages
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS face_persons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        display_name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL UNIQUE,
                        folder_name TEXT NOT NULL UNIQUE,
                        prototype_embedding TEXT,
                        created_at INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS photo_faces (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        photo_id INTEGER NOT NULL,
                        person_id INTEGER NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0.0,
                        bbox_left REAL NOT NULL DEFAULT 0.0,
                        bbox_top REAL NOT NULL DEFAULT 0.0,
                        bbox_width REAL NOT NULL DEFAULT 0.0,
                        bbox_height REAL NOT NULL DEFAULT 0.0,
                        thumb_relpath TEXT,
                        embedding TEXT,
                        validated INTEGER NOT NULL DEFAULT 0, -- 0=unknown, 1=good, -1=bad
                        created_at INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE,
                        FOREIGN KEY(person_id) REFERENCES face_persons(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS face_photo_state (
                        photo_id INTEGER PRIMARY KEY,
                        processed_at INTEGER NOT NULL DEFAULT 0,
                        error_text TEXT,
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS theme_photo_state (
                        photo_id INTEGER PRIMARY KEY,
                        processed_at INTEGER NOT NULL DEFAULT 0,
                        error_text TEXT,
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS face_index_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        enabled INTEGER NOT NULL DEFAULT 0,
                        model_name TEXT NOT NULL DEFAULT 'mediapipe',
                        last_run_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                self._conn.execute(
                    """
                    INSERT INTO face_index_state(id, enabled, model_name, last_run_at)
                    VALUES (1, 0, 'mediapipe', 0)
                    ON CONFLICT(id) DO NOTHING
                    """
                )
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_faces_photo ON photo_faces(photo_id)")
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_faces_person ON photo_faces(person_id)")
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_face_persons_norm ON face_persons(normalized_name)")
        except sqlite3.Error as e:
            logger.error("Erreur création tables visages : %s", e)

        # Migration validation visages
        try:
            with self._conn:
                self._conn.execute("ALTER TABLE photo_faces ADD COLUMN validated INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Déjà là

        # Schéma relationnel albums virtuels (par base source)
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS virtual_albums (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL UNIQUE,
                        created_at INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS virtual_album_members (
                        album_id INTEGER NOT NULL,
                        photo_id INTEGER NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        added_at INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(album_id, photo_id),
                        FOREIGN KEY(album_id) REFERENCES virtual_albums(id) ON DELETE CASCADE,
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_virtual_albums_name ON virtual_albums(name COLLATE NOCASE)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_virtual_album_members_album_order ON virtual_album_members(album_id, sort_order, added_at, photo_id)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_virtual_album_members_photo ON virtual_album_members(photo_id, album_id)"
                )
        except sqlite3.Error as e:
            logger.error("Erreur création tables albums virtuels : %s", e)

        # Schéma tags thématiques (classification IA / heuristique)
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS photo_theme_tags (
                        photo_id INTEGER NOT NULL,
                        theme_key TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0.0,
                        source TEXT NOT NULL DEFAULT 'thematic_v1',
                        created_at INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(photo_id, theme_key, source),
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photo_theme_tags_theme ON photo_theme_tags(theme_key, source, confidence DESC, photo_id)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photo_theme_tags_photo ON photo_theme_tags(photo_id, source)"
                )
        except sqlite3.Error as e:
            logger.error("Erreur création table tags thématiques : %s", e)

        # Table des exclusions manuelles d'albums virtuels (auto-gérés par IA/thème)
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS virtual_album_excluded_photos (
                        album_id INTEGER NOT NULL,
                        photo_id INTEGER NOT NULL,
                        excluded_at INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(album_id, photo_id),
                        FOREIGN KEY(album_id) REFERENCES virtual_albums(id) ON DELETE CASCADE,
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_valbum_excluded_album ON virtual_album_excluded_photos(album_id, photo_id)"
                )
        except sqlite3.Error as e:
            logger.error("Erreur création table exclusions albums virtuels : %s", e)

        # Table des exclusions manuelles de reconnaissance faciale (évite la re-attribution après reset)
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS face_excluded_assignments (
                        photo_id INTEGER NOT NULL,
                        person_id INTEGER NOT NULL,
                        excluded_at INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(photo_id, person_id),
                        FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE,
                        FOREIGN KEY(person_id) REFERENCES face_persons(id) ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_face_excluded_photo ON face_excluded_assignments(photo_id, person_id)"
                )
        except sqlite3.Error as e:
            logger.error("Erreur création table exclusions visages : %s", e)

    @staticmethod
    def _duplicate_signature(filesize: int | None, filename: str | None, date_taken: str | None) -> str:
        """Construit une signature métier de doublon: taille + titre + date."""
        size = int(filesize or 0)
        title = Path(str(filename or "")).stem.strip().lower()
        date = str(date_taken or "").strip()
        return f"{size}|{title}|{date}"

    def duplicate_index_needs_refresh(self) -> bool:
        """Retourne True si l'index de doublons est absent ou obsolète."""
        try:
            photo_count = int(self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
            flag_count = int(self._conn.execute("SELECT COUNT(*) FROM photo_duplicate_flags").fetchone()[0])
            if photo_count != flag_count:
                return True

            row = self._conn.execute(
                "SELECT photo_count FROM duplicate_index_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return True
            indexed_photo_count = int(row[0] or 0)
            return indexed_photo_count != photo_count
        except sqlite3.Error:
            return True

    def build_duplicate_index(
        self,
        progress_callback: Any = None,
        cancel_check: Any = None,
    ) -> dict[str, int]:
        """Reconstruit l'index des doublons (signature: taille+titre+date)."""
        try:
            rows = self._conn.execute(
                "SELECT id, filesize, filename, date_taken FROM photos ORDER BY id"
            ).fetchall()
        except sqlite3.Error as e:
            logger.error("Erreur lecture photos pour index doublons : %s", e)
            return {"photos": 0, "duplicates": 0, "groups": 0, "cancelled": 1}

        total = len(rows)
        counts: dict[str, int] = defaultdict(int)
        for idx, row in enumerate(rows, start=1):
            if cancel_check and cancel_check():
                return {"photos": total, "duplicates": 0, "groups": 0, "cancelled": 1}
            key = self._duplicate_signature(row["filesize"], row["filename"], row["date_taken"])
            counts[key] += 1
            if progress_callback and (idx % 500 == 0 or idx == total):
                progress_callback(idx, total)

        now_ts = int(time.time())
        batch: list[tuple[int, str, int, int]] = []
        duplicate_photos = 0
        duplicate_groups = 0
        seen_dup_groups: set[str] = set()

        for idx, row in enumerate(rows, start=1):
            if cancel_check and cancel_check():
                return {"photos": total, "duplicates": duplicate_photos, "groups": duplicate_groups, "cancelled": 1}
            key = self._duplicate_signature(row["filesize"], row["filename"], row["date_taken"])
            dup_count = int(counts.get(key, 1))
            if dup_count > 1:
                duplicate_photos += 1
                if key not in seen_dup_groups:
                    seen_dup_groups.add(key)
                    duplicate_groups += 1
            batch.append((int(row["id"]), key, dup_count, now_ts))

        try:
            with self._conn:
                self._conn.execute("DELETE FROM photo_duplicate_flags")
                self._conn.executemany(
                    "INSERT INTO photo_duplicate_flags(photo_id, dup_key, dup_count, indexed_at) VALUES (?, ?, ?, ?)",
                    batch,
                )
                self._conn.execute(
                    """
                    INSERT INTO duplicate_index_state(id, last_built_at, photo_count)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_built_at = excluded.last_built_at,
                        photo_count = excluded.photo_count
                    """,
                    (now_ts, total),
                )
        except sqlite3.Error as e:
            logger.error("Erreur écriture index doublons : %s", e)
            return {"photos": total, "duplicates": duplicate_photos, "groups": duplicate_groups, "cancelled": 1}

        return {"photos": total, "duplicates": duplicate_photos, "groups": duplicate_groups, "cancelled": 0}

    def get_duplicate_photo_ids(self) -> set[int]:
        """Retourne les IDs des photos considérées doublons de signature."""
        try:
            rows = self._conn.execute(
                "SELECT photo_id FROM photo_duplicate_flags WHERE dup_count > 1"
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error:
            return set()

    def get_duplicate_stats(self) -> tuple[int, int]:
        """Retourne (nombre_groupes, nombre_photos_doublons)."""
        try:
            photos = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM photo_duplicate_flags WHERE dup_count > 1"
                ).fetchone()[0]
            )
            groups = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM (SELECT dup_key FROM photo_duplicate_flags WHERE dup_count > 1 GROUP BY dup_key)"
                ).fetchone()[0]
            )
            return (groups, photos)
        except sqlite3.Error:
            return (0, 0)

    def clear_photos(self) -> None:
        """Vide la table photos."""
        try:
            with self._conn:
                self._conn.execute("DELETE FROM photos")
                self._conn.execute("DELETE FROM sqlite_sequence WHERE name='photos'")
                self._conn.execute("DELETE FROM photo_duplicate_flags")
                self._conn.execute("DELETE FROM photo_faces")
                self._conn.execute("DELETE FROM face_photo_state")
                self._conn.execute(
                    """
                    INSERT INTO duplicate_index_state(id, last_built_at, photo_count)
                    VALUES (1, 0, 0)
                    ON CONFLICT(id) DO UPDATE SET
                        last_built_at = 0,
                        photo_count = 0
                    """
                )
        except sqlite3.Error as e:
            logger.error("Erreur de nettoyage de table : %s", e)

    @staticmethod
    def _embedding_distance(a: list[float], b: list[float]) -> float:
        """Distance cosine simple entre deux vecteurs."""
        if not a or not b or len(a) != len(b):
            return 1.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b):
            dot += float(x) * float(y)
            norm_a += float(x) * float(x)
            norm_b += float(y) * float(y)
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 1.0
        return 1.0 - (dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))

    @staticmethod
    def _safe_json_vector(value: str | None) -> list[float]:
        try:
            data = json.loads(str(value or "[]"))
            if isinstance(data, list):
                return [float(v) for v in data]
        except Exception:
            return []
        return []

    def set_face_index_enabled(self, enabled: bool, model_name: str = "") -> None:
        model = str(model_name or "").strip() or "unknown"
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO face_index_state(id, enabled, model_name, last_run_at)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        enabled = excluded.enabled,
                        model_name = excluded.model_name,
                        last_run_at = excluded.last_run_at
                    """,
                    (1 if enabled else 0, model, int(time.time())),
                )
        except sqlite3.Error as e:
            logger.error("Erreur set_face_index_enabled : %s", e)

    def get_unprocessed_face_photos(self, limit: int = 2) -> list[dict[str, Any]]:
        """Retourne les photos à traiter par le worker visage."""
        max_rows = max(1, int(limit or 1))
        try:
            rows = self._conn.execute(
                """
                SELECT p.id, p.filepath
                FROM photos p
                LEFT JOIN face_photo_state s ON s.photo_id = p.id
                WHERE s.photo_id IS NULL
                ORDER BY p.id ASC
                LIMIT ?
                """,
                (max_rows,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def mark_face_photo_processed(self, photo_id: int, error_text: str = "") -> None:
        """Marque une photo comme traitée par l'indexation visage."""
        now_ts = int(time.time())
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO face_photo_state(photo_id, processed_at, error_text)
                    VALUES (?, ?, ?)
                    ON CONFLICT(photo_id) DO UPDATE SET
                        processed_at = excluded.processed_at,
                        error_text = excluded.error_text
                    """,
                    (int(photo_id), now_ts, str(error_text or "")),
                )
                self._conn.execute(
                    "UPDATE face_index_state SET last_run_at = ? WHERE id = 1",
                    (now_ts,),
                )
        except sqlite3.Error as e:
            logger.error("Erreur mark_face_photo_processed : %s", e)

    def clear_errored_face_photos(self) -> int:
        """Supprime les états d'erreur pour permettre une réindexation faciale."""
        try:
            with self._conn:
                cur = self._conn.execute("DELETE FROM face_photo_state WHERE TRIM(COALESCE(error_text, '')) <> ''")
                return cur.rowcount
        except sqlite3.Error:
            return 0

    def get_unprocessed_theme_photos(self, limit: int = 5) -> list[dict[str, Any]]:
        """Retourne les photos à traiter par le worker thématique IA."""
        max_rows = max(1, int(limit or 1))
        try:
            rows = self._conn.execute(
                """
                SELECT p.id, p.filepath
                FROM photos p
                LEFT JOIN theme_photo_state s ON s.photo_id = p.id
                WHERE s.photo_id IS NULL
                ORDER BY p.id ASC
                LIMIT ?
                """,
                (max_rows,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def mark_theme_photo_processed(self, photo_id: int, error_text: str = "") -> None:
        """Marque une photo comme traitée par la classification thématique."""
        now_ts = int(time.time())
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO theme_photo_state(photo_id, processed_at, error_text)
                    VALUES (?, ?, ?)
                    ON CONFLICT(photo_id) DO UPDATE SET
                        processed_at = excluded.processed_at,
                        error_text = excluded.error_text
                    """,
                    (int(photo_id), now_ts, str(error_text or "")),
                )
        except sqlite3.Error as e:
            logger.error("Erreur mark_theme_photo_processed : %s", e)

    def clear_errored_theme_photos(self) -> int:
        """Supprime les états d'erreur pour permettre une re-classification thématique."""
        try:
            with self._conn:
                cur = self._conn.execute("DELETE FROM theme_photo_state WHERE TRIM(COALESCE(error_text, '')) <> ''")
                return cur.rowcount
        except sqlite3.Error:
            return 0

    def _next_auto_person_name(self) -> str:
        try:
            rows = self._conn.execute("SELECT display_name FROM face_persons").fetchall()
            used = {str(r[0] or "") for r in rows}
        except sqlite3.Error:
            used = set()

        idx = 1
        while True:
            candidate = f"Personne {idx:04d}"
            if candidate not in used:
                return candidate
            idx += 1

    def _create_face_person(self, display_name: str, embedding: list[float]) -> int:
        now_ts = int(time.time())
        clean_name = str(display_name or "").strip() or self._next_auto_person_name()
        norm = _normalize_person_name(clean_name)
        folder_name = re.sub(r"[^a-zA-Z0-9 _.-]", "", clean_name).strip() or clean_name
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO face_persons(display_name, normalized_name, folder_name, prototype_embedding, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (clean_name, norm, folder_name, json.dumps(embedding), now_ts, now_ts),
                )
                row = self._conn.execute("SELECT last_insert_rowid() AS rid").fetchone()
            return int(row["rid"]) if row else 0
        except sqlite3.IntegrityError:
            row = self._conn.execute(
                "SELECT id FROM face_persons WHERE normalized_name = ?",
                (norm,),
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error as e:
            logger.error("Erreur _create_face_person : %s", e)
            return 0

    def assign_face_person(self, embedding: list[float], threshold: float = 0.22) -> int:
        """Associe un visage à une personne existante ou crée une nouvelle personne."""
        try:
            rows = self._conn.execute(
                "SELECT id, prototype_embedding FROM face_persons"
            ).fetchall()
        except sqlite3.Error:
            rows = []

        best_person_id = 0
        best_distance = 999.0
        for row in rows:
            vec = self._safe_json_vector(row[1])
            if not vec:
                continue
            dist = self._embedding_distance(vec, embedding)
            if dist < best_distance:
                best_distance = dist
                best_person_id = int(row[0])

        if best_person_id > 0 and best_distance <= float(threshold):
            # Mise à jour douce du prototype pour stabiliser le cluster.
            try:
                current = self._conn.execute(
                    "SELECT prototype_embedding FROM face_persons WHERE id = ?",
                    (best_person_id,),
                ).fetchone()
                current_vec = self._safe_json_vector(current[0] if current else "")
                if current_vec and len(current_vec) == len(embedding):
                    merged = [((0.8 * a) + (0.2 * b)) for a, b in zip(current_vec, embedding)]
                    with self._conn:
                        self._conn.execute(
                            "UPDATE face_persons SET prototype_embedding = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(merged), int(time.time()), best_person_id),
                        )
            except sqlite3.Error:
                pass
            return best_person_id

        return self._create_face_person(self._next_auto_person_name(), embedding)

    def replace_photo_faces(self, photo_id: int, faces: list[dict[str, Any]]) -> None:
        """Remplace les visages détectés pour une photo donnée."""
        now_ts = int(time.time())
        try:
            with self._conn:
                self._conn.execute("DELETE FROM photo_faces WHERE photo_id = ?", (int(photo_id),))
                payload: list[tuple[Any, ...]] = []
                for face in faces:
                    payload.append(
                        (
                            int(photo_id),
                            int(face.get("person_id") or 0),
                            float(face.get("confidence") or 0.0),
                            float(face.get("bbox_left") or 0.0),
                            float(face.get("bbox_top") or 0.0),
                            float(face.get("bbox_width") or 0.0),
                            float(face.get("bbox_height") or 0.0),
                            str(face.get("thumb_relpath") or ""),
                            json.dumps(face.get("embedding") or []),
                            now_ts,
                        )
                    )
                if payload:
                    self._conn.executemany(
                        """
                        INSERT INTO photo_faces(
                            photo_id, person_id, confidence, bbox_left, bbox_top, bbox_width, bbox_height,
                            thumb_relpath, embedding, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        payload,
                    )
        except sqlite3.Error as e:
            logger.error("Erreur replace_photo_faces : %s", e)

    def get_photo_ids_with_faces(self) -> set[int]:
        try:
            rows = self._conn.execute("SELECT DISTINCT photo_id FROM photo_faces").fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error:
            return set()

    def get_photo_ids_for_person_name(self, search_term_norm: str) -> set[int]:
        term = str(search_term_norm or "").strip()
        if not term:
            return set()
        like_term = f"%{term}%"
        try:
            rows = self._conn.execute(
                """
                SELECT DISTINCT pf.photo_id
                FROM photo_faces pf
                JOIN face_persons fp ON fp.id = pf.person_id
                WHERE fp.normalized_name LIKE ?
                """,
                (like_term,),
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error:
            return set()

    def get_face_people(self, show_only_marked: bool = False) -> list[dict[str, Any]]:
        try:
            where_clause = ""
            if show_only_marked:
                where_clause = "WHERE fp.id IN (SELECT DISTINCT person_id FROM photo_faces WHERE validated IN (1, -1))"
            
            rows = self._conn.execute(
                f"""
                SELECT
                    fp.id,
                    fp.display_name,
                    fp.normalized_name,
                    fp.folder_name,
                    COUNT(pf.id) AS face_count,
                    COUNT(DISTINCT pf.photo_id) AS photo_count
                FROM face_persons fp
                LEFT JOIN photo_faces pf ON pf.person_id = fp.id
                {where_clause}
                GROUP BY fp.id, fp.display_name, fp.normalized_name, fp.folder_name
                ORDER BY fp.display_name COLLATE NOCASE ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_all_people(self) -> list[dict[str, Any]]:
        """Retourne la liste simplifiée de toutes les personnes (utilisé pour la sidebar)."""
        return self.get_face_people(show_only_marked=False)

    def get_photos_by_person(self, person_id: int) -> list[dict[str, Any]]:
        """Retourne toutes les photos d'origine d'une personne donnée."""
        try:
            query = """
            SELECT p.*
            FROM photos p
            JOIN photo_faces f ON f.photo_id = p.id
            WHERE f.person_id = ?
            GROUP BY p.id
            ORDER BY p.date_taken DESC
            """
            rows = self._conn.execute(query, (int(person_id),)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_people_for_photo(self, photo_id: int) -> list[dict[str, Any]]:
        """Retourne la liste des personnes détectées sur une photo spécifique."""
        try:
            query = """
                SELECT DISTINCT fp.* 
                FROM face_persons fp
                JOIN photo_faces pf ON fp.id = pf.person_id
                WHERE pf.photo_id = ?
            """
            rows = self._conn.execute(query, (int(photo_id),)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_face_person_sample_thumbs(self, person_id: int, limit: int = 3, show_only_marked: bool = False) -> list[str]:
        max_rows = max(1, int(limit or 1))
        try:
            where_clause = "WHERE person_id = ? AND COALESCE(thumb_relpath, '') <> ''"
            if show_only_marked:
                where_clause += " AND validated IN (1, -1)"
            
            rows = self._conn.execute(
                f"""
                SELECT thumb_relpath
                FROM photo_faces
                {where_clause}
                ORDER BY confidence DESC, id ASC
                LIMIT ?
                """,
                (int(person_id), max_rows),
            ).fetchall()
            return [str(r[0]) for r in rows if str(r[0] or "").strip()]
        except sqlite3.Error:
            return []

    def _norm_face_rel(self, path: str) -> str:
        return str(path or "").strip().replace("\\", "/")

    def get_face_thumb_origin_photo(self, person_id: int, thumb_relpath: str) -> dict[str, Any] | None:
        rel = self._norm_face_rel(thumb_relpath)
        if not rel:
            return None
        try:
            row = self._conn.execute(
                """
                SELECT p.*, pf.validated
                FROM photos p
                JOIN photo_faces pf ON p.id = pf.photo_id
                WHERE pf.person_id = ? AND pf.thumb_relpath = ?
                """,
                (int(person_id), rel),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None

    def set_face_validated(self, person_id: int, thumb_relpath: str, validated: int) -> bool:
        """Marque un visage comme validé (1), invalide (-1) ou inconnu (0)."""
        rel = self._norm_face_rel(thumb_relpath)
        if not rel: return False
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE photo_faces SET validated = ? WHERE person_id = ? AND thumb_relpath = ?",
                    (int(validated), int(person_id), rel)
                )
            return True
        except sqlite3.Error:
            return False

    def get_face_validation_status(self, person_id: int, thumb_relpath: str) -> int:
        """Retourne le statut de validation d'un visage."""
        rel = self._norm_face_rel(thumb_relpath)
        if not rel: return 0
        try:
            row = self._conn.execute(
                "SELECT validated FROM photo_faces WHERE person_id = ? AND thumb_relpath = ?",
                (int(person_id), rel)
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def reset_all_face_validations(self) -> int:
        """Remet à 0 tous les statuts de validation (validated != 0). Retourne le nombre de lignes modifiées."""
        try:
            with self._conn:
                cur = self._conn.execute("UPDATE photo_faces SET validated = 0 WHERE validated != 0")
            return int(cur.rowcount or 0)
        except sqlite3.Error:
            return 0

    def delete_face_by_thumb(self, person_id: int, thumb_relpath: str) -> tuple[int, bool]:
        rel = str(thumb_relpath or "").strip()
        if not rel:
            return (0, False)

        removed_faces = 0
        removed_person = False
        try:
            with self._conn:
                # Récupérer le photo_id avant suppression pour enregistrer l'exclusion
                row_photo = self._conn.execute(
                    "SELECT photo_id FROM photo_faces WHERE person_id = ? AND thumb_relpath = ?",
                    (int(person_id), rel),
                ).fetchone()
                affected_photo_id = int(row_photo[0]) if row_photo else 0

                cur = self._conn.execute(
                    "DELETE FROM photo_faces WHERE person_id = ? AND thumb_relpath = ?",
                    (int(person_id), rel),
                )
                removed_faces = int(cur.rowcount or 0)

                if removed_faces > 0 and affected_photo_id > 0:
                    # Mémoriser que cette photo ne doit plus être auto-attribuée à cette personne
                    now_ts = int(time.time())
                    self._conn.execute(
                        "INSERT OR IGNORE INTO face_excluded_assignments(photo_id, person_id, excluded_at) VALUES (?, ?, ?)",
                        (affected_photo_id, int(person_id), now_ts),
                    )

                remaining = self._conn.execute(
                    "SELECT COUNT(*) FROM photo_faces WHERE person_id = ?",
                    (int(person_id),),
                ).fetchone()
                remaining_count = int(remaining[0] or 0) if remaining else 0
                if remaining_count <= 0:
                    cur_person = self._conn.execute(
                        "DELETE FROM face_persons WHERE id = ?",
                        (int(person_id),),
                    )
                    removed_person = int(cur_person.rowcount or 0) > 0
        except sqlite3.Error as e:
            logger.error("Erreur delete_face_by_thumb : %s", e)
            return (0, False)

        return (removed_faces, removed_person)

    def get_face_person_folder_name(self, person_id: int) -> str:
        try:
            row = self._conn.execute(
                "SELECT folder_name FROM face_persons WHERE id = ?",
                (int(person_id),),
            ).fetchone()
            return str(row[0] or "") if row else ""
        except sqlite3.Error:
            return ""

    def get_face_person_display_name(self, person_id: int) -> str:
        try:
            row = self._conn.execute(
                "SELECT display_name FROM face_persons WHERE id = ?",
                (int(person_id),),
            ).fetchone()
            return str(row[0] or "") if row else ""
        except sqlite3.Error:
            return ""

    def get_face_person_by_name(self, display_name: str) -> dict[str, Any] | None:
        """Recherche une personne par son nom d'affichage (exact, insensible à la casse)."""
        clean = str(display_name or "").strip()
        if not clean:
            return None
        try:
            row = self._conn.execute(
                "SELECT id, display_name, folder_name FROM face_persons WHERE display_name = ? COLLATE NOCASE",
                (clean,),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None

    def resolve_face_person_target(self, display_name: str) -> tuple[bool, str, int, str, str]:
        """Résout une cible de personne visage à partir d'un nom.

        Retourne (ok, message, person_id_ou_0, clean_display_name, folder_name).
        """
        clean_name = str(display_name or "").strip()
        if not clean_name:
            return (False, "Nom vide.", 0, "", "")

        norm = _normalize_person_name(clean_name)
        if not norm:
            return (False, "Nom invalide.", 0, "", "")

        try:
            existing = self._conn.execute(
                "SELECT id, display_name, folder_name FROM face_persons WHERE normalized_name = ?",
                (norm,),
            ).fetchone()
            if existing:
                return (
                    True,
                    "OK",
                    int(existing[0] or 0),
                    str(existing[1] or clean_name),
                    str(existing[2] or ""),
                )

            safe_folder = re.sub(r"[^a-zA-Z0-9 _.-]", "", clean_name).strip() or clean_name
            dup_folder = self._conn.execute(
                "SELECT id FROM face_persons WHERE folder_name = ?",
                (safe_folder,),
            ).fetchone()
            if dup_folder:
                return (False, "Nom de dossier déjà utilisé.", 0, clean_name, safe_folder)

            return (True, "OK", 0, clean_name, safe_folder)
        except sqlite3.Error as e:
            logger.error("Erreur resolve_face_person_target : %s", e)
            return (False, "Erreur SQL.", 0, "", "")

    def reassign_face_by_thumb(
        self,
        source_person_id: int,
        source_thumb_relpath: str,
        target_display_name: str,
        target_thumb_relpath: str,
    ) -> tuple[bool, str, int, bool]:
        """Réaffecte un visage (miniature) vers une autre personne.

        Retourne (ok, message, target_person_id, source_person_removed).
        """
        src_rel = str(source_thumb_relpath or "").strip()
        dst_rel = str(target_thumb_relpath or "").strip()
        if int(source_person_id) <= 0 or not src_rel or not dst_rel:
            return (False, "Paramètres invalides.", 0, False)

        ok, msg, resolved_id, clean_name, folder_name = self.resolve_face_person_target(target_display_name)
        if not ok:
            return (False, msg, 0, False)

        now_ts = int(time.time())
        created_target_person = False
        target_person_id = int(resolved_id or 0)

        try:
            with self._conn:
                if target_person_id <= 0:
                    self._conn.execute(
                        """
                        INSERT INTO face_persons(display_name, normalized_name, folder_name, prototype_embedding, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_name,
                            _normalize_person_name(clean_name),
                            folder_name,
                            "[]",
                            now_ts,
                            now_ts,
                        ),
                    )
                    row = self._conn.execute("SELECT last_insert_rowid() AS rid").fetchone()
                    target_person_id = int(row["rid"]) if row else 0
                    created_target_person = target_person_id > 0

                if target_person_id <= 0:
                    raise sqlite3.IntegrityError("target_person_id invalide")

                cur = self._conn.execute(
                    """
                    UPDATE photo_faces
                    SET person_id = ?, thumb_relpath = ?
                    WHERE person_id = ? AND thumb_relpath = ?
                    """,
                    (target_person_id, dst_rel, int(source_person_id), src_rel),
                )
                moved = int(cur.rowcount or 0)
                if moved <= 0:
                    raise sqlite3.IntegrityError("visage introuvable")

                # Récupérer le photo_id pour enregistrer l'exclusion de la source
                row_pid = self._conn.execute(
                    "SELECT photo_id FROM photo_faces WHERE person_id = ? AND thumb_relpath = ?",
                    (target_person_id, dst_rel),
                ).fetchone()
                if row_pid:
                    affected_photo_id = int(row_pid[0])
                    # Cette photo ne doit plus être auto-attribuée à l'ancienne personne
                    self._conn.execute(
                        "INSERT OR IGNORE INTO face_excluded_assignments(photo_id, person_id, excluded_at) VALUES (?, ?, ?)",
                        (affected_photo_id, int(source_person_id), now_ts),
                    )
                    # S'assurer que la nouvelle attribution n'est pas bloquée par une ancienne exclusion
                    self._conn.execute(
                        "DELETE FROM face_excluded_assignments WHERE photo_id = ? AND person_id = ?",
                        (affected_photo_id, target_person_id),
                    )

                source_removed = False
                remaining = self._conn.execute(
                    "SELECT COUNT(*) FROM photo_faces WHERE person_id = ?",
                    (int(source_person_id),),
                ).fetchone()
                remaining_count = int(remaining[0] or 0) if remaining else 0
                if remaining_count <= 0:
                    cur_src = self._conn.execute(
                        "DELETE FROM face_persons WHERE id = ?",
                        (int(source_person_id),),
                    )
                    source_removed = int(cur_src.rowcount or 0) > 0

            return (True, "OK", target_person_id, source_removed)
        except sqlite3.IntegrityError:
            if created_target_person:
                try:
                    with self._conn:
                        self._conn.execute(
                            "DELETE FROM face_persons WHERE id = ?",
                            (target_person_id,),
                        )
                except sqlite3.Error:
                    pass
            return (False, "Visage introuvable.", 0, False)
        except sqlite3.Error as e:
            logger.error("Erreur reassign_face_by_thumb : %s", e)
            if created_target_person:
                try:
                    with self._conn:
                        self._conn.execute(
                            "DELETE FROM face_persons WHERE id = ?",
                            (target_person_id,),
                        )
                except sqlite3.Error:
                    pass
            return (False, "Erreur SQL.", 0, False)

    def rename_face_person(self, person_id: int, new_display_name: str) -> tuple[bool, str, str, str]:
        """Renomme une personne visage.

        Retourne (ok, message, old_folder_name, new_folder_name).
        """
        clean_name = str(new_display_name or "").strip()
        if not clean_name:
            return (False, "Nom vide.", "", "")
        norm = _normalize_person_name(clean_name)
        if not norm:
            return (False, "Nom invalide.", "", "")

        try:
            row = self._conn.execute(
                "SELECT folder_name FROM face_persons WHERE id = ?",
                (int(person_id),),
            ).fetchone()
            if not row:
                return (False, "Personne introuvable.", "", "")
            old_folder = str(row[0] or "")

            dup = self._conn.execute(
                "SELECT id FROM face_persons WHERE normalized_name = ? AND id <> ?",
                (norm, int(person_id)),
            ).fetchone()
            if dup:
                return (False, "Ce nom existe déjà.", old_folder, old_folder)

            safe_folder = re.sub(r"[^a-zA-Z0-9 _.-]", "", clean_name).strip() or clean_name
            dup_folder = self._conn.execute(
                "SELECT id FROM face_persons WHERE folder_name = ? AND id <> ?",
                (safe_folder, int(person_id)),
            ).fetchone()
            if dup_folder:
                return (False, "Nom de dossier déjà utilisé.", old_folder, old_folder)

            with self._conn:
                self._conn.execute(
                    """
                    UPDATE face_persons
                    SET display_name = ?, normalized_name = ?, folder_name = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_name, norm, safe_folder, int(time.time()), int(person_id)),
                )
                if old_folder and safe_folder and old_folder != safe_folder:
                    # Les miniatures sont stockées en chemin relatif "<folder_name>/<file>".
                    # On met à jour le préfixe pour conserver l'affichage après renommage du dossier.
                    self._conn.execute(
                        """
                        UPDATE photo_faces
                        SET thumb_relpath = ? || SUBSTR(thumb_relpath, LENGTH(?) + 1)
                        WHERE person_id = ?
                          AND COALESCE(thumb_relpath, '') <> ''
                          AND thumb_relpath LIKE (? || '/%')
                        """,
                        (safe_folder, old_folder, int(person_id), old_folder),
                    )
            return (True, "OK", old_folder, safe_folder)
        except sqlite3.Error as e:
            logger.error("Erreur rename_face_person : %s", e)
            return (False, "Erreur SQL.", "", "")

    def normalize_face_thumb_folder(self, person_id: int, folder_name: str) -> None:
        """Force le préfixe du dossier sur tous les thumb_relpath d'une personne."""
        folder = str(folder_name or "").strip()
        if int(person_id) <= 0 or not folder:
            return
        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE photo_faces
                    SET thumb_relpath = ? || '/' ||
                        CASE
                            WHEN INSTR(COALESCE(thumb_relpath, ''), '/') > 0
                                THEN SUBSTR(thumb_relpath, INSTR(thumb_relpath, '/') + 1)
                            ELSE COALESCE(thumb_relpath, '')
                        END
                    WHERE person_id = ?
                      AND COALESCE(thumb_relpath, '') <> ''
                      AND thumb_relpath NOT LIKE (? || '/%')
                    """,
                    (folder, int(person_id), folder),
                )
        except sqlite3.Error as e:
            logger.error("Erreur normalize_face_thumb_folder : %s", e)

    def get_face_stats(self) -> dict[str, int]:
        """Retourne des statistiques globales visages."""
        try:
            total_photos = int(self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] or 0)
            processed = int(self._conn.execute("SELECT COUNT(*) FROM face_photo_state").fetchone()[0] or 0)
            processed_errors = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM face_photo_state WHERE TRIM(COALESCE(error_text, '')) <> ''"
                ).fetchone()[0] or 0
            )
            photos_with_faces = int(self._conn.execute("SELECT COUNT(DISTINCT photo_id) FROM photo_faces").fetchone()[0] or 0)
            faces_total = int(self._conn.execute("SELECT COUNT(*) FROM photo_faces").fetchone()[0] or 0)
            people_total = int(self._conn.execute("SELECT COUNT(*) FROM face_persons").fetchone()[0] or 0)
            return {
                "total_photos": total_photos,
                "processed": processed,
                "processed_errors": processed_errors,
                "photos_with_faces": photos_with_faces,
                "faces_total": faces_total,
                "people_total": people_total,
            }
        except sqlite3.Error:
            return {
                "total_photos": 0,
                "processed": 0,
                "processed_errors": 0,
                "photos_with_faces": 0,
                "faces_total": 0,
                "people_total": 0,
            }

    def reset_unsupported_face_states(self) -> int:
        """Réinitialise les états 'unsupported' pour permettre une nouvelle tentative."""
        try:
            with self._conn:
                cur = self._conn.execute("DELETE FROM face_photo_state WHERE error_text = 'unsupported'")
                return int(cur.rowcount or 0)
        except sqlite3.Error as e:
            logger.error("Erreur reset_unsupported_face_states : %s", e)
            return 0

    def reset_face_index_data(self) -> None:
        """Réinitialise les tables visages pour relancer une indexation complète."""
        try:
            with self._conn:
                self._conn.execute("DELETE FROM photo_faces")
                self._conn.execute("DELETE FROM face_persons")
                self._conn.execute("DELETE FROM face_photo_state")
            logger.warning("Réinitialisation complète de l'index visages effectuée.")
        except sqlite3.Error as e:
            logger.error("Erreur reset_face_index_data : %s", e)

    def insert_photos(self, photos_data: list[dict[str, Any]]) -> None:
        """Insère une liste de dictionnaire de photos."""
        if not photos_data:
            return

        query = """
        INSERT OR IGNORE INTO photos (
            filepath, filename, filesize, date_taken, camera_model,
            latitude, longitude, country, region, city
        ) VALUES (
            :filepath, :filename, :filesize, :date_taken, :camera_model,
            :latitude, :longitude, :country, :region, :city
        )
        """
        try:
            with self._conn:
                self._conn.executemany(query, photos_data)
        except sqlite3.Error as e:
            logger.error("Erreur d'insertion par lots : %s", e)

    def update_collection_names(self) -> None:
        """Calcule et met à jour les noms de collection pour éviter les doublons.
        Format : 
        - Sans GPS : AAMMJJ_NomOriginal
        - Avec GPS : AAMMJJ_Pays_Region_Ville
        - Doublons : -XXX (ex: -001, -002...)
        """
        try:
            # On récupère toutes les photos ordonnées par date par défaut pour rester consistant
            cursor = self._conn.execute("SELECT id, filename, date_taken, country, region, city FROM photos ORDER BY date_taken ASC, filename ASC")
            rows = cursor.fetchall()

            counters = defaultdict(int)
            updates = []

            for row in rows:
                photo_id = row["id"]
                yymmdd = _extract_yymmdd(row["date_taken"])
                
                # Nettoyage des composants GPS
                country = _clean_string(row["country"])
                region = _clean_string(row["region"])
                city = _clean_string(row["city"])
                
                # Détermination du nom de base
                if any([country, region, city]):
                    # Format avec GPS
                    parts = [yymmdd]
                    if country: parts.append(country)
                    if region: parts.append(region)
                    if city: parts.append(city)
                    base_name = "_".join(parts)
                else:
                    # Format sans GPS : AAMMJJ_NomFichierOriginal (sans extension)
                    orig_name = Path(row["filename"]).stem
                    base_name = f"{yymmdd}_{_clean_string(orig_name)}"

                # Gestion des doublons par -XXX
                idx = counters[base_name]
                counters[base_name] += 1
                
                # Le premier élément d'un groupe n'a pas forcément de suffixe ?
                # L'utilisateur demande -XXX, je vais l'ajouter systématiquement pour la consistance numérique
                collection_name = f"{base_name}-{idx:03d}"
                updates.append((collection_name, photo_id))

            # Application de la mise à jour par lots
            if updates:
                with self._conn:
                    self._conn.executemany("UPDATE photos SET collection_name = ? WHERE id = ?", updates)
                logger.info("Noms de collection mis à jour pour %d fichiers.", len(updates))
                
        except sqlite3.Error as e:
            logger.error("Erreur logic collection_name : %s", e)

    def get_all_photos(self, order_by: str = "date_taken", asc: bool = True) -> list[dict[str, Any]]:
        """Récupère toutes les photos."""
        direction = "ASC" if asc else "DESC"
        allowed_columns = {
            "id", "filepath", "filename", "filesize", "date_taken",
            "camera_model", "country", "region", "city", "collection_name", "is_copied",
            "latitude", "longitude"
        }
        if order_by not in allowed_columns:
            order_by = "date_taken"

        # Pour collection_name vide / null on s'assure qu'il est récupéré
        query = f"SELECT * FROM photos ORDER BY {order_by} {direction}, id {direction}"
        try:
            cursor = self._conn.execute(query)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error("Erreur de sélection des photos : %s", e)
            return []

    def get_photo_count(self) -> int:
        """Retourne le nombre total de photos en base."""
        try:
            cursor = self._conn.execute("SELECT COUNT(*) FROM photos")
            row = cursor.fetchone()
            return int(row[0] or 0) if row else 0
        except sqlite3.Error:
            return 0

    def update_filepaths_batch(self, updates: list[tuple[str, int]]) -> None:
        """Met à jour les chemins de fichiers par lots (Utilisé pour migration absolu -> relatif)."""
        if not updates:
            return
        try:
            with self._conn:
                self._conn.executemany(
                    "UPDATE photos SET filepath = ? WHERE id = ?",
                    updates
                )
        except sqlite3.Error as e:
            logger.error("Erreur update_filepaths_batch : %s", e)

    def has_missing_collection_names(self) -> bool:
        """Indique s'il existe au moins une photo sans collection_name renseigné."""
        try:
            row = self._conn.execute(
                "SELECT 1 FROM photos WHERE collection_name IS NULL OR TRIM(collection_name) = '' LIMIT 1"
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def set_copied_state(self, photo_id: int, state: bool) -> None:
        """Met à jour le statut copié d'une photo."""
        try:
            with self._conn:
                self._conn.execute("UPDATE photos SET is_copied = ? WHERE id = ?", (1 if state else 0, photo_id))
        except sqlite3.Error as e:
            logger.error("Erreur set_copied_state : %s", e)

    def get_copied_count(self) -> int:
        """Retourne le nombre de photos marquées comme copiées."""
        try:
            cursor = self._conn.execute("SELECT COUNT(*) FROM photos WHERE is_copied = 1")
            return cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    def delete_photo(self, photo_id: int) -> dict[str, list[str]]:
        """Supprime une photo et nettoie les liens visages associés.

        Retourne:
        - thumb_relpaths: miniatures à supprimer sur disque côté UI
        - orphan_folders: dossiers de personnes devenus orphelins
        """
        cleanup: dict[str, list[str]] = {
            "thumb_relpaths": [],
            "orphan_folders": [],
        }

        pid = int(photo_id or 0)
        if pid <= 0:
            return cleanup

        try:
            with self._conn:
                rows = self._conn.execute(
                    """
                    SELECT pf.person_id, COALESCE(pf.thumb_relpath, '') AS thumb_relpath, COALESCE(fp.folder_name, '') AS folder_name
                    FROM photo_faces pf
                    LEFT JOIN face_persons fp ON fp.id = pf.person_id
                    WHERE pf.photo_id = ?
                    """,
                    (pid,),
                ).fetchall()

                touched_person_ids: set[int] = set()
                thumb_relpaths: set[str] = set()
                for row in rows:
                    person_id = int(row[0] or 0)
                    if person_id > 0:
                        touched_person_ids.add(person_id)
                    rel = str(row[1] or "").strip()
                    if rel:
                        thumb_relpaths.add(rel)

                self._conn.execute("DELETE FROM photo_faces WHERE photo_id = ?", (pid,))
                self._conn.execute("DELETE FROM face_photo_state WHERE photo_id = ?", (pid,))
                self._conn.execute("DELETE FROM virtual_album_members WHERE photo_id = ?", (pid,))
                self._conn.execute("DELETE FROM photos WHERE id = ?", (pid,))

                orphan_folders: set[str] = set()
                for person_id in touched_person_ids:
                    remaining = self._conn.execute(
                        "SELECT COUNT(*) FROM photo_faces WHERE person_id = ?",
                        (person_id,),
                    ).fetchone()
                    remaining_count = int(remaining[0] or 0) if remaining else 0
                    if remaining_count > 0:
                        continue

                    folder_row = self._conn.execute(
                        "SELECT folder_name FROM face_persons WHERE id = ?",
                        (person_id,),
                    ).fetchone()
                    folder_name = str(folder_row[0] or "").strip() if folder_row else ""
                    self._conn.execute("DELETE FROM face_persons WHERE id = ?", (person_id,))
                    if folder_name:
                        orphan_folders.add(folder_name)

                cleanup["thumb_relpaths"] = sorted(thumb_relpaths)
                cleanup["orphan_folders"] = sorted(orphan_folders)
        except sqlite3.Error as e:
            logger.error("Erreur au moment de la suppression SQLite : %s", e)

        return cleanup

    def update_photo_path(self, photo_id: int, new_filepath: str, new_filename: str) -> None:
        """Met à jour le chemin physique et le nom de fichier d'une photo."""
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE photos SET filepath = ?, filename = ? WHERE id = ?",
                    (new_filepath, new_filename, photo_id)
                )
        except sqlite3.Error as e:
            logger.error("Erreur mise à jour chemin: %s", e)

    def update_single_collection_name(self, photo_id: int, new_name: str) -> None:
        """Met à jour le nom de collection d'une photo individuelle."""
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE photos SET collection_name = ? WHERE id = ?",
                    (new_name, photo_id)
                )
        except sqlite3.Error as e:
            logger.error("Erreur mise à jour collection_name: %s", e)

    def update_photo_geo(self, photo_id: int, country: str, region: str, city: str) -> None:
        """Met à jour les champs géographiques d'une photo."""
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE photos SET country = ?, region = ?, city = ? WHERE id = ?",
                    (country, region, city, photo_id)
                )
        except sqlite3.Error as e:
            logger.error("Erreur mise à jour géo: %s", e)

    def update_photo_location(self, photo_id: int, latitude: float | None, longitude: float | None, country: str, region: str, city: str) -> None:
        """Met à jour latitude/longitude + pays/région/ville d'une photo."""
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE photos SET latitude = ?, longitude = ?, country = ?, region = ?, city = ? WHERE id = ?",
                    (latitude, longitude, country, region, city, photo_id)
                )
        except sqlite3.Error as e:
            logger.error("Erreur mise à jour localisation: %s", e)

    def get_photo_by_id(self, photo_id: int) -> dict[str, Any] | None:
        """Récupère une photo unique par son ID."""
        try:
            cursor = self._conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None

    def get_photo_by_path(self, filepath: str) -> dict[str, Any] | None:
        """Récupère une photo par son chemin d'accès."""
        try:
            cursor = self._conn.execute("SELECT * FROM photos WHERE filepath = ?", (filepath,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None

    @staticmethod
    def _clean_virtual_album_name(name: str | None) -> str:
        """Nettoie un nom d'album virtuel saisi par l'utilisateur."""
        return re.sub(r"\s+", " ", str(name or "").strip())

    def get_virtual_album(self, album_id: int) -> dict[str, Any] | None:
        """Retourne un album virtuel avec son nombre de photos."""
        aid = int(album_id or 0)
        if aid <= 0:
            return None
        try:
            row = self._conn.execute(
                """
                SELECT va.id, va.name, va.normalized_name, va.created_at, va.updated_at,
                       COUNT(vam.photo_id) AS photo_count
                FROM virtual_albums va
                LEFT JOIN virtual_album_members vam ON vam.album_id = va.id
                WHERE va.id = ?
                GROUP BY va.id, va.name, va.normalized_name, va.created_at, va.updated_at
                """,
                (aid,),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error("Erreur lecture album virtuel %s : %s", aid, e)
            return None

    def get_virtual_album_by_name(self, name: str) -> dict[str, Any] | None:
        """Retourne un album virtuel à partir de son nom normalisé."""
        normalized_name = _normalize_virtual_album_name(self._clean_virtual_album_name(name))
        if not normalized_name:
            return None
        try:
            row = self._conn.execute(
                "SELECT id FROM virtual_albums WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
        except sqlite3.Error as e:
            logger.error("Erreur recherche album virtuel '%s' : %s", normalized_name, e)
            return None
        return self.get_virtual_album(int(row[0])) if row else None

    def list_virtual_albums(self) -> list[dict[str, Any]]:
        """Liste les albums virtuels de la base avec leur cardinalité."""
        try:
            rows = self._conn.execute(
                """
                SELECT va.id, va.name, va.normalized_name, va.created_at, va.updated_at,
                       COUNT(vam.photo_id) AS photo_count
                FROM virtual_albums va
                LEFT JOIN virtual_album_members vam ON vam.album_id = va.id
                GROUP BY va.id, va.name, va.normalized_name, va.created_at, va.updated_at
                ORDER BY va.name COLLATE NOCASE ASC, va.id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error("Erreur liste albums virtuels : %s", e)
            return []

    def create_virtual_album(self, name: str) -> dict[str, Any] | None:
        """Crée un album virtuel si nécessaire et retourne sa fiche."""
        cleaned_name = self._clean_virtual_album_name(name)
        normalized_name = _normalize_virtual_album_name(cleaned_name)
        if not cleaned_name or not normalized_name:
            return None

        existing = self.get_virtual_album_by_name(cleaned_name)
        if existing is not None:
            return existing

        now_ts = int(time.time())
        try:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT INTO virtual_albums(name, normalized_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (cleaned_name, normalized_name, now_ts, now_ts),
                )
        except sqlite3.IntegrityError:
            return self.get_virtual_album_by_name(cleaned_name)
        except sqlite3.Error as e:
            logger.error("Erreur création album virtuel '%s' : %s", cleaned_name, e)
            return None
        return self.get_virtual_album(int(cursor.lastrowid))

    def rename_virtual_album(self, album_id: int, new_name: str) -> dict[str, Any] | None:
        """Renomme un album virtuel et retourne sa fiche mise à jour."""
        aid = int(album_id or 0)
        cleaned_name = self._clean_virtual_album_name(new_name)
        normalized_name = _normalize_virtual_album_name(cleaned_name)
        if aid <= 0 or not cleaned_name or not normalized_name:
            return None

        existing = self.get_virtual_album_by_name(cleaned_name)
        if existing is not None and int(existing.get("id") or 0) != aid:
            return None

        now_ts = int(time.time())
        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE virtual_albums
                    SET name = ?, normalized_name = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (cleaned_name, normalized_name, now_ts, aid),
                )
        except sqlite3.Error as e:
            logger.error("Erreur renommage album virtuel %s : %s", aid, e)
            return None
        return self.get_virtual_album(aid)

    def delete_virtual_album(self, album_id: int) -> bool:
        """Supprime un album virtuel sans toucher aux fichiers source."""
        aid = int(album_id or 0)
        if aid <= 0:
            return False
        try:
            with self._conn:
                self._conn.execute("DELETE FROM virtual_album_members WHERE album_id = ?", (aid,))
                cursor = self._conn.execute("DELETE FROM virtual_albums WHERE id = ?", (aid,))
            return int(cursor.rowcount or 0) > 0
        except sqlite3.Error as e:
            logger.error("Erreur suppression album virtuel %s : %s", aid, e)
            return False

    def add_photos_to_virtual_album(self, album_id: int, photo_ids: list[int]) -> tuple[int, int]:
        """Ajoute plusieurs photos à un album virtuel en préservant l'ordre d'ajout."""
        aid = int(album_id or 0)
        if aid <= 0:
            return 0, 0

        unique_photo_ids: list[int] = []
        seen: set[int] = set()
        for raw_photo_id in photo_ids:
            pid = int(raw_photo_id or 0)
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            unique_photo_ids.append(pid)

        if not unique_photo_ids:
            return 0, 0

        try:
            placeholders = ",".join("?" for _ in unique_photo_ids)
            existing_rows = self._conn.execute(
                f"SELECT photo_id FROM virtual_album_members WHERE album_id = ? AND photo_id IN ({placeholders})",
                [aid, *unique_photo_ids],
            ).fetchall()
            existing_ids = {int(row[0]) for row in existing_rows}

            next_sort_row = self._conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM virtual_album_members WHERE album_id = ?",
                (aid,),
            ).fetchone()
            next_sort_order = int(next_sort_row[0] or 0)
            now_ts = int(time.time())
            to_insert: list[tuple[int, int, int, int]] = []
            skipped = 0
            for pid in unique_photo_ids:
                if pid in existing_ids:
                    skipped += 1
                    continue
                next_sort_order += 1
                to_insert.append((aid, pid, next_sort_order, now_ts))

            if not to_insert:
                return 0, skipped

            with self._conn:
                self._conn.executemany(
                    """
                    INSERT INTO virtual_album_members(album_id, photo_id, sort_order, added_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    to_insert,
                )
                self._conn.execute(
                    "UPDATE virtual_albums SET updated_at = ? WHERE id = ?",
                    (now_ts, aid),
                )
            return len(to_insert), skipped
        except sqlite3.Error as e:
            logger.error("Erreur ajout photos album virtuel %s : %s", aid, e)
            return 0, len(unique_photo_ids)

    def remove_photo_from_virtual_album(self, album_id: int, photo_id: int) -> bool:
        """Retire une photo d'un album virtuel sans supprimer le fichier.

        Enregistre également une exclusion manuelle afin que les scans IA
        automatiques ne puissent pas réintégrer la photo.
        """
        aid = int(album_id or 0)
        pid = int(photo_id or 0)
        if aid <= 0 or pid <= 0:
            return False
        now_ts = int(time.time())
        try:
            with self._conn:
                cursor = self._conn.execute(
                    "DELETE FROM virtual_album_members WHERE album_id = ? AND photo_id = ?",
                    (aid, pid),
                )
                if int(cursor.rowcount or 0) > 0:
                    self._conn.execute(
                        "UPDATE virtual_albums SET updated_at = ? WHERE id = ?",
                        (now_ts, aid),
                    )
                    # Mémoriser l'exclusion manuelle pour les futurs scans automatiques
                    self._conn.execute(
                        "INSERT OR IGNORE INTO virtual_album_excluded_photos(album_id, photo_id, excluded_at) VALUES (?, ?, ?)",
                        (aid, pid, now_ts),
                    )
                    return True
            return False
        except sqlite3.Error as e:
            logger.error("Erreur retrait photo %s album virtuel %s : %s", pid, aid, e)
            return False

    def add_photo_to_virtual_album(self, album_id: int, photo_id: int, ignore_exclusion: bool = False) -> bool:
        """Ajoute une photo à un album virtuel si elle n'y est pas déjà.

        Par défaut, respecte les exclusions manuelles (ne réajoute pas une photo
        que l'utilisateur a retirée manuellement). Passer ``ignore_exclusion=True``
        pour forcer l'ajout (usage interne ou ré-intégration explicite).
        """
        aid = int(album_id or 0)
        pid = int(photo_id or 0)
        if aid <= 0 or pid <= 0:
            return False
        if not ignore_exclusion:
            try:
                row = self._conn.execute(
                    "SELECT 1 FROM virtual_album_excluded_photos WHERE album_id = ? AND photo_id = ?",
                    (aid, pid),
                ).fetchone()
                if row is not None:
                    return False  # Photo exclue manuellement — on ne la ré-ajoute pas
            except sqlite3.Error:
                pass
        # Vérifier qu'aucun autre photo avec le même nom de fichier n'est déjà dans l'album
        try:
            fn_row = self._conn.execute("SELECT filename FROM photos WHERE id = ?", (pid,)).fetchone()
            if fn_row:
                fname = str(fn_row[0] or "")
                if fname:
                    dup_row = self._conn.execute(
                        """
                        SELECT 1 FROM virtual_album_members vam
                        JOIN photos p ON p.id = vam.photo_id
                        WHERE vam.album_id = ? AND p.filename = ?
                        """,
                        (aid, fname),
                    ).fetchone()
                    if dup_row is not None:
                        return False  # Doublon par nom de fichier
        except sqlite3.Error:
            pass
        try:
            now_ts = int(time.time())
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO virtual_album_members(album_id, photo_id, added_at) VALUES (?, ?, ?)",
                    (aid, pid, now_ts)
                )
            return True
        except sqlite3.Error as e:
            logger.error("Erreur add_photo_to_virtual_album(%s, %s): %s", aid, pid, e)
            return False

    def replace_virtual_album_photos(self, album_id: int, photo_ids: list[int]) -> int:
        """Remplace complètement le contenu d'un album virtuel par `photo_ids`."""
        aid = int(album_id or 0)
        if aid <= 0:
            return 0

        # Récupérer les exclusions manuelles pour filtrer les photo_ids
        excluded: set[int] = set()
        try:
            rows = self._conn.execute(
                "SELECT photo_id FROM virtual_album_excluded_photos WHERE album_id = ?",
                (aid,),
            ).fetchall()
            excluded = {int(r[0]) for r in rows}
        except sqlite3.Error:
            pass

        # Dédoublonnage par photo_id et par exclusion
        unique_photo_ids: list[int] = []
        seen: set[int] = set()
        for raw_photo_id in photo_ids:
            pid = int(raw_photo_id or 0)
            if pid <= 0 or pid in seen or pid in excluded:
                continue
            seen.add(pid)
            unique_photo_ids.append(pid)

        # Dédoublonnage par nom de fichier (même fichier indexé depuis plusieurs chemins)
        if unique_photo_ids:
            try:
                placeholders = ",".join("?" * len(unique_photo_ids))
                fn_rows = self._conn.execute(
                    f"SELECT id, filename FROM photos WHERE id IN ({placeholders})",
                    unique_photo_ids,
                ).fetchall()
                fn_map: dict[int, str] = {int(r[0]): str(r[1] or "") for r in fn_rows}
                seen_filenames: set[str] = set()
                deduped: list[int] = []
                for pid in unique_photo_ids:
                    fname = fn_map.get(pid, "")
                    if fname and fname in seen_filenames:
                        continue
                    if fname:
                        seen_filenames.add(fname)
                    deduped.append(pid)
                unique_photo_ids = deduped
            except sqlite3.Error:
                pass

        now_ts = int(time.time())
        to_insert = [
            (aid, pid, idx + 1, now_ts)
            for idx, pid in enumerate(unique_photo_ids)
        ]

        try:
            with self._conn:
                self._conn.execute("DELETE FROM virtual_album_members WHERE album_id = ?", (aid,))
                if to_insert:
                    self._conn.executemany(
                        """
                        INSERT INTO virtual_album_members(album_id, photo_id, sort_order, added_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        to_insert,
                    )
                self._conn.execute(
                    "UPDATE virtual_albums SET updated_at = ? WHERE id = ?",
                    (now_ts, aid),
                )
            return len(unique_photo_ids)
        except sqlite3.Error as e:
            logger.error("Erreur remplacement album virtuel %s : %s", aid, e)
            return 0

    # ------------------------------------------------------------------
    # Exclusions manuelles d'albums virtuels
    # ------------------------------------------------------------------

    def add_virtual_album_exclusion(self, album_id: int, photo_id: int) -> None:
        """Enregistre l'exclusion manuelle d'une photo d'un album virtuel auto-géré."""
        aid = int(album_id or 0)
        pid = int(photo_id or 0)
        if aid <= 0 or pid <= 0:
            return
        now_ts = int(time.time())
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO virtual_album_excluded_photos(album_id, photo_id, excluded_at) VALUES (?, ?, ?)",
                    (aid, pid, now_ts),
                )
        except sqlite3.Error as e:
            logger.error("Erreur add_virtual_album_exclusion(%s, %s): %s", aid, pid, e)

    def remove_virtual_album_exclusion(self, album_id: int, photo_id: int) -> None:
        """Supprime l'exclusion manuelle d'une photo d'un album virtuel (l'IA peut la ré-ajouter)."""
        aid = int(album_id or 0)
        pid = int(photo_id or 0)
        if aid <= 0 or pid <= 0:
            return
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM virtual_album_excluded_photos WHERE album_id = ? AND photo_id = ?",
                    (aid, pid),
                )
        except sqlite3.Error as e:
            logger.error("Erreur remove_virtual_album_exclusion(%s, %s): %s", aid, pid, e)

    def get_virtual_album_exclusions(self, album_id: int) -> set[int]:
        """Retourne l'ensemble des photo_ids manuellement exclus d'un album virtuel."""
        aid = int(album_id or 0)
        if aid <= 0:
            return set()
        try:
            rows = self._conn.execute(
                "SELECT photo_id FROM virtual_album_excluded_photos WHERE album_id = ?",
                (aid,),
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error:
            return set()

    def is_photo_excluded_from_album(self, album_id: int, photo_id: int) -> bool:
        """Retourne True si la photo est manuellement exclue de cet album virtuel."""
        aid = int(album_id or 0)
        pid = int(photo_id or 0)
        if aid <= 0 or pid <= 0:
            return False
        try:
            row = self._conn.execute(
                "SELECT 1 FROM virtual_album_excluded_photos WHERE album_id = ? AND photo_id = ?",
                (aid, pid),
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------
    # Exclusions manuelles de reconnaissance faciale
    # ------------------------------------------------------------------

    def add_face_exclusion(self, photo_id: int, person_id: int) -> None:
        """Enregistre que la photo ne doit plus être automatiquement attribuée à cette personne."""
        pid = int(photo_id or 0)
        prid = int(person_id or 0)
        if pid <= 0 or prid <= 0:
            return
        now_ts = int(time.time())
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO face_excluded_assignments(photo_id, person_id, excluded_at) VALUES (?, ?, ?)",
                    (pid, prid, now_ts),
                )
        except sqlite3.Error as e:
            logger.error("Erreur add_face_exclusion(%s, %s): %s", pid, prid, e)

    def remove_face_exclusion(self, photo_id: int, person_id: int) -> None:
        """Supprime l'exclusion faciale pour cette photo/personne (autoriser à nouveau l'attribution auto)."""
        pid = int(photo_id or 0)
        prid = int(person_id or 0)
        if pid <= 0 or prid <= 0:
            return
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM face_excluded_assignments WHERE photo_id = ? AND person_id = ?",
                    (pid, prid),
                )
        except sqlite3.Error as e:
            logger.error("Erreur remove_face_exclusion(%s, %s): %s", pid, prid, e)

    def get_face_exclusions_for_photo(self, photo_id: int) -> set[int]:
        """Retourne l'ensemble des person_ids manuellement exclus pour cette photo."""
        pid = int(photo_id or 0)
        if pid <= 0:
            return set()
        try:
            rows = self._conn.execute(
                "SELECT person_id FROM face_excluded_assignments WHERE photo_id = ?",
                (pid,),
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error:
            return set()

    def person_has_face_from_filename(self, person_id: int, filename: str, exclude_photo_id: int = 0) -> bool:
        """Retourne True si cette personne a déjà un visage associé à une photo portant ce nom de fichier."""
        prid = int(person_id or 0)
        fname = str(filename or "").strip()
        if prid <= 0 or not fname:
            return False
        try:
            query = """
                SELECT 1 FROM photo_faces pf
                JOIN photos p ON p.id = pf.photo_id
                WHERE pf.person_id = ? AND p.filename = ?
            """
            params: list[Any] = [prid, fname]
            if exclude_photo_id > 0:
                query += " AND pf.photo_id != ?"
                params.append(exclude_photo_id)
            row = self._conn.execute(query, params).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def clear_theme_tags(self, source: str = "thematic_v1") -> None:
        """Efface tous les tags thématiques d'une source donnée."""
        src = str(source or "").strip() or "thematic_v1"
        try:
            with self._conn:
                self._conn.execute("DELETE FROM photo_theme_tags WHERE source = ?", (src,))
        except sqlite3.Error as e:
            logger.error("Erreur clear_theme_tags(%s) : %s", src, e)

    def upsert_theme_tag(self, photo_id: int, theme_key: str, confidence: float, source: str = "thematic_v1") -> bool:
        """Insère/écrase un tag thématique pour une photo."""
        pid = int(photo_id or 0)
        key = str(theme_key or "").strip().lower()
        src = str(source or "").strip() or "thematic_v1"
        conf = float(confidence or 0.0)
        if pid <= 0 or not key:
            return False
        conf = max(0.0, min(1.0, conf))
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO photo_theme_tags(photo_id, theme_key, confidence, source, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(photo_id, theme_key, source) DO UPDATE SET
                        confidence = excluded.confidence,
                        created_at = excluded.created_at
                    """,
                    (pid, key, conf, src, int(time.time())),
                )
            return True
        except sqlite3.Error as e:
            logger.error("Erreur upsert_theme_tag(photo=%s, theme=%s) : %s", pid, key, e)
            return False

    def get_photo_ids_for_theme(self, theme_key: str, min_confidence: float = 0.0, source: str = "thematic_v1") -> list[int]:
        """Retourne les photo_id taggés pour un thème donné."""
        key = str(theme_key or "").strip().lower()
        src = str(source or "").strip() or "thematic_v1"
        if not key:
            return []
        threshold = max(0.0, min(1.0, float(min_confidence or 0.0)))
        try:
            rows = self._conn.execute(
                """
                SELECT photo_id
                FROM photo_theme_tags
                WHERE theme_key = ? AND source = ? AND confidence >= ?
                ORDER BY confidence DESC, photo_id ASC
                """,
                (key, src, threshold),
            ).fetchall()
            return [int(row[0]) for row in rows]
        except sqlite3.Error as e:
            logger.error("Erreur get_photo_ids_for_theme(%s) : %s", key, e)
            return []

    def get_virtual_album_photo_ids(self, album_id: int) -> list[int]:
        """Retourne les photo_id d'un album virtuel dans son ordre persistant."""
        aid = int(album_id or 0)
        if aid <= 0:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT photo_id
                FROM virtual_album_members
                WHERE album_id = ?
                ORDER BY sort_order ASC, added_at ASC, photo_id ASC
                """,
                (aid,),
            ).fetchall()
            return [int(row[0]) for row in rows]
        except sqlite3.Error as e:
            logger.error("Erreur lecture photos album virtuel %s : %s", aid, e)
            return []

    def get_virtual_albums_for_photo(self, photo_id: int) -> list[dict[str, Any]]:
        """Liste les albums virtuels contenant une photo donnée."""
        pid = int(photo_id or 0)
        if pid <= 0:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT va.id, va.name, va.normalized_name, va.created_at, va.updated_at,
                       vam.sort_order, vam.added_at
                FROM virtual_albums va
                INNER JOIN virtual_album_members vam ON vam.album_id = va.id
                WHERE vam.photo_id = ?
                ORDER BY va.name COLLATE NOCASE ASC, va.id ASC
                """,
                (pid,),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error("Erreur lecture albums virtuels pour photo %s : %s", pid, e)
            return []

    def delete_photos_by_filepaths(self, filepaths: list[str]) -> int:
        """Supprime plusieurs photos par leur chemin relatif (utilisé par le Watcher)."""
        if not filepaths:
            return 0
        try:
            with self._conn:
                cursor = self._conn.execute(
                    f"DELETE FROM photos WHERE filepath IN ({','.join(['?']*len(filepaths))})",
                    filepaths
                )
                return cursor.rowcount
        except sqlite3.Error as e:
            logger.error("Erreur delete_photos_by_filepaths: %s", e)
            return 0

    def close(self) -> None:
        """Ferme la connexion SQLite."""
        self._conn.close()
