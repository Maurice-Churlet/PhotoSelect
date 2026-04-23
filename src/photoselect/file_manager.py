# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

"""Gestion de fichiers : création de répertoires, copie, renommage et scan."""

from __future__ import annotations

import logging
import os
import stat
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Extensions d'images scannées (alignées avec la visionneuse Pillow).
SCAN_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif",
    ".png", ".bmp", ".gif",
    ".tif", ".tiff", ".webp",
    ".heic", ".heif",
}

IGNORED_SCAN_DIRS = {
    ".photoselect_cache",
    "_photoselect",
    ".picasaoriginals",
    "$recycle.bin",
    "system volume information",
}


def _is_ignored_name(name: str) -> bool:
    normalized = name.strip().lower()
    if normalized in IGNORED_SCAN_DIRS:
        return True
    if normalized.startswith("."):
        return True
    return False


def _is_windows_hidden_or_system(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        attrs = path.stat().st_file_attributes
        mask = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
        return bool(attrs & mask)
    except Exception:
        return False


def is_ignored_scan_path(filepath: Path) -> bool:
    """Retourne True si le chemin passe par un dossier à ignorer pendant le scan."""
    for part in filepath.parts:
        if _is_ignored_name(part):
            return True
    if _is_windows_hidden_or_system(filepath):
        return True
    return False


def is_jpg(filepath: Path) -> bool:
    """Compatibilité historique: retourne True pour les extensions JPG/JPEG."""
    return filepath.suffix.lower() in {".jpg", ".jpeg", ".jfif"}


def is_supported_image(filepath: Path) -> bool:
    """Vérifie si l'image est dans un format supporté par le scan/visionneuse."""
    return filepath.suffix.lower() in SCAN_IMAGE_EXTENSIONS


def list_jpg_files(directory: Path) -> list[Path]:
    """Liste tous les fichiers image supportés dans un répertoire (récursif).

    Args:
        directory: Répertoire racine à scanner.

    Returns:
        Liste des chemins vers les fichiers image (non triée, pour gains de perf).
    """
    if not directory.is_dir():
        logger.warning("Le répertoire n'existe pas : %s", directory)
        return []
    
    # Parcours top-down avec pruning : les dossiers ignorés ne sont jamais explorés.
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(directory, topdown=True):
        root_path = Path(root)

        kept_dirs: list[str] = []
        for dirname in dirnames:
            dir_path = root_path / dirname
            if _is_ignored_name(dirname) or _is_windows_hidden_or_system(dir_path):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            file_path = root_path / filename
            if is_supported_image(file_path) and not is_ignored_scan_path(file_path):
                files.append(file_path)

    logger.info("Fin de la recherche : %d fichiers image trouvés (hors cache).", len(files))
    return files


def is_safe_output_dir(input_dir: Path, output_dir: Path) -> bool:
    """Vérifie que le dossier de sortie n'est pas dans l'arborescence d'entrée.
    
    Args:
        input_dir: Dossier d'entrée (racine du scan).
        output_dir: Dossier de sortie prévu.
        
    Returns:
        True si c'est sûr, False si le dossier de sortie est confondu avec l'entrée ou ses enfants.
    """
    try:
        in_res = input_dir.resolve()
        out_res = output_dir.resolve()
        
        # Vérifie si out_res est égal à in_res ou si out_res enfant de in_res
        if out_res == in_res or in_res in out_res.parents:
            return False
            
        return True
    except Exception as e:
        logger.error("Erreur de validation de chemin : %s", e)
        return False


def get_file_size(filepath: Path) -> int:
    """Retourne la taille du fichier en octets."""
    try:
        return filepath.stat().st_size
    except Exception:
        return 0


def format_size(size_bytes: int) -> str:
    """Formate une taille en octets vers une chaine lisible (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / (1024**2):.2f} MB"
    else:
        return f"{size_bytes / (1024**3):.2f} GB"


def ensure_directory(directory: Path) -> Path:
    """Crée un répertoire (et ses parents) s'il n'existe pas."""
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def copy_file(source: Path, destination: Path, overwrite: bool = False) -> Path:
    """Copie un fichier vers une destination."""
    if not source.exists():
        raise FileNotFoundError(f"Fichier source introuvable : {source}")

    if destination.is_dir():
        destination = destination / source.name

    if destination.exists() and not overwrite:
        raise FileExistsError(f"Le fichier existe déjà : {destination}")

    ensure_directory(destination.parent)
    shutil.copy2(source, destination)
    return destination


def rename_file(source: Path, new_name: str) -> Path:
    """Renomme un fichier dans son répertoire actuel."""
    if not source.exists():
        raise FileNotFoundError(f"Fichier introuvable : {source}")

    new_path = source.parent / new_name
    if new_path.exists():
        raise FileExistsError(f"Le fichier existe déjà : {new_path}")

    source.rename(new_path)
    return new_path


def generate_thumbnail(source: Path, cache_dir: Path, size: tuple[int, int] = (400, 400), use_rel_hash_of: Path | None = None) -> Path | None:
    """Génère une vignette pour une image et la sauvegarde dans le cache.
    Utilise un hash du chemin (relatif si fourni) pour garantir l'unicité du nom dans le cache.
    """
    try:
        from PIL import Image
        import hashlib
        ensure_directory(cache_dir)
        
        # Hash unique basé sur le chemin (relatif par rapport au dossier racine si possible)
        path_to_hash = str(source.resolve())
        if use_rel_hash_of:
            try:
                path_to_hash = str(source.resolve().relative_to(use_rel_hash_of.resolve()))
            except Exception:
                pass

        path_hash = hashlib.md5(path_to_hash.encode()).hexdigest()
        thumb_path = cache_dir / f"{path_hash}.jpg"
        
        if thumb_path.exists():
            return thumb_path
            
        with Image.open(source) as img:
            # Rotation automatique basée sur EXIF si possible
            try:
                from PIL import ExifTags
                orientation = next((k for k, v in ExifTags.TAGS.items() if v == 'Orientation'), None)
                if orientation is not None:
                    exif = img.getexif()
                    if exif is not None and orientation in exif:
                        if exif[orientation] == 3: img = img.rotate(180, expand=True)
                        elif exif[orientation] == 6: img = img.rotate(270, expand=True)
                        elif exif[orientation] == 8: img = img.rotate(90, expand=True)
            except Exception:
                pass

            img.thumbnail(size)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(thumb_path, "JPEG", quality=80)
        return thumb_path
    except Exception as e:
        logger.error("Erreur génération vignette pour %s : %s", source.name, e)
        return None
