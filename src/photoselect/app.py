# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

"""Application principale PhotoSelect — Interface graphique tkinter (Double fenêtre)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import csv
from collections import Counter
from datetime import datetime
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import threading
import time
import urllib.parse
import unicodedata
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from PIL import Image, ImageOps, ImageTk
from send2trash import send2trash

from photoselect.database import Database
from photoselect.face_engine import (
    detect_faces_from_image,
    get_unavailability_reason as get_face_engine_unavailability_reason,
    get_supported_backends,
    is_available as is_face_engine_available,
    normalize_backend as normalize_face_backend,
)
from photoselect.exif_reader import (
    extract_all_metadata,
    find_city_candidates,
    find_city_candidates_online,
    get_location_details,
    get_location_details_online,
    read_gps_exif,
    update_image_exif_location,
)
from photoselect.file_manager import (
    list_jpg_files,
    is_supported_image,
    is_ignored_scan_path,
    get_file_size,
    format_size,
    ensure_directory,
    generate_thumbnail,
)
from photoselect.theme_engine import classify_themes_from_image, get_theme_engine_status

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    FileSystemEventHandler = None

CONFIG_FILE = Path("photoselect_config.json")
logger = logging.getLogger(__name__)

import sys
def get_resource_path(relative_path: str) -> Path:
    """Retourne le chemin absolu vers une ressource (fonctionne pour dev et PyInstaller)."""
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).resolve().parent.parent.parent
    return base_path / relative_path

APP_TITLE = "PhotoSelect v1.1"
META_DIR_NAME = "_PhotoSelect"

# Dossier pour la config globale (reste dans AppData)
# Mais on prépare le stockage projet dans le dossier source lui-même.
APP_DATA_DIR = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "PhotoSelect"
ensure_directory(APP_DATA_DIR)

APP_DB_DIR = APP_DATA_DIR / "databases"
APP_CACHE_DIR = APP_DATA_DIR / "cache"
APP_LOG_DIR = APP_DATA_DIR / "logs"
OPS_LOG_FILE = APP_LOG_DIR / "operations.csv"
PATH_REGISTRY_DB_FILE = APP_DB_DIR / "_path_registry.sqlite3"
ALBUM_INDEX_DB_FILE = None    # Sera Source/_PhotoSelect/album_index.sqlite3

THEME_ALBUM_SOURCE = "thematic_v1"
THEME_ALBUMS: dict[str, dict[str, Any]] = {
    "animals": {
        "label": "[IA] Animaux",
        "keywords": [
            "animal", "animaux", "dog", "chien", "cat", "chat", "bird", "oiseau", "horse", "cheval",
            "cow", "vache", "goat", "chevre", "monkey", "singe", "dolphin", "dauphin", "elephant", "lion",
        ],
    },
    "insects": {
        "label": "[IA] Insectes",
        "keywords": [
            "insect", "insecte", "insectes", "butterfly", "papillon", "bee", "abeille", "ant", "fourmi",
            "dragonfly", "libellule", "ladybug", "coccinelle", "mosquito", "moustique", "beetle", "scarabee",
        ],
    },
    "flowers": {
        "label": "[IA] Fleurs",
        "keywords": [
            "flower", "fleur", "fleurs", "rose", "orchid", "orchidee", "tulip", "tulipe", "lily", "lys",
            "daisy", "marguerite", "lotus", "lavender", "lavande", "bouquet", "petal", "petale",
        ],
    },
    "temples": {
        "label": "[IA] Temples",
        "keywords": [
            "temple", "temples", "pagoda", "shrine", "wat", "mandir", "stupa", "sanctuary",
        ],
    },
    "churches": {
        "label": "[IA] Eglises",
        "keywords": [
            "eglise", "eglises", "church", "chapel", "chapelle", "cathedral", "cathedrale", "basilica", "basilique",
        ],
    },
    "statues": {
        "label": "[IA] Statues & Sculptures",
        "keywords": [
            "statue", "sculpture", "scultures", "sculture", "buste", "monument", "statuette", "relief", "figurine", "totem",
        ],
    },
}


# Configuration de l'aide contextuelle pour maintenance facile
HELP_SHORTCUTS = {
    "Tableau de Bord": [
        ("Flèches", "Naviguer dans les photos"),
        ("Espace", "Exporter la photo (copie)"),
        ("Delete / Suppr", "Supprimer / Retirer de l'album"),
        ("D / d", "Lancer le diaporama"),
        ("F2", "Renommer / Éditer la photo"),
        ("Clic Droit", "Menu contextuel"),
        ("Ctrl+Clic", "Multi-sélection"),
    ],
    "Visionneuse": [
        ("Flèches / Molette", "Photo précédente / suivante"),
        ("Ctrl + 1 / 2", "Déplacer vers écran 1 / 2"),
        ("Ctrl + R / L", "Rotation Droite / Gauche"),
        ("Ctrl + U", "Normaliser orientation EXIF"),
        ("F12 / Double-Clic", "Bascule Plein Écran"),
        ("Esc / Echap", "Arrêter Diaporama / Quitter Plein Écran"),
        ("Espace", "Exporter la photo"),
        ("1 à 9", "Vitesse Diaporama (sec/photo)"),
        ("+ / -", "Ajuster vitesse Diaporama"),
    ],
    "Mosaïque": [
        ("Clic", "Ouvrir la photo d'origine"),
        ("Ctrl + Clic", "Marquer comme Correct"),
        ("V", "Valider (Marquer Correct)"),
        ("F", "Faux (Marquer Inconnu)"),
        ("Molette", "Défiler les visages"),
    ],
    "Global": [
        ("F1", "Afficher cette aide contextuelle"),
        ("Logo / Double-Clic Carte", "Réinitialiser les filtres"),
        ("F12 (sur tableau)", "Mode Plein Écran du tableau (si supporté)"),
    ]
}


def _copy_image_to_clipboard(filepath: Path) -> bool:
    """Copie une image dans le presse-papier Windows via l'API native."""
    clipboard_opened = False
    h_mem: int | None = None
    try:
        from io import BytesIO
        img = Image.open(filepath)
        img = img.convert("RGB")
        output = BytesIO()
        img.save(output, "BMP")
        bmp_data = output.getvalue()[14:]  # Retirer le header BMP (14 octets)
        output.close()

        CF_DIB = 8
        GMEM_MOVEABLE = 0x0002

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL

        # Le presse-papiers Windows peut etre temporairement verrouille par une autre app.
        for _ in range(6):
            if user32.OpenClipboard(None):
                clipboard_opened = True
                break
            time.sleep(0.03)

        if not clipboard_opened:
            raise OSError("Impossible d'ouvrir le presse-papiers Windows")

        if not user32.EmptyClipboard():
            raise OSError("Impossible de vider le presse-papiers")

        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(bmp_data))
        if not h_mem:
            raise MemoryError("GlobalAlloc a echoue")

        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            raise OSError("GlobalLock a echoue")

        ctypes.memmove(p_mem, bmp_data, len(bmp_data))
        kernel32.GlobalUnlock(h_mem)

        result = user32.SetClipboardData(CF_DIB, h_mem)
        if not result:
            raise OSError("SetClipboardData a echoue")

        # Ownership transferee au presse-papiers en cas de succes.
        h_mem = None
        return True
    except Exception as e:
        logger.error("Erreur copie image presse-papier : %s", e)
        return False
    finally:
        try:
            if h_mem:
                ctypes.windll.kernel32.GlobalFree(ctypes.c_void_p(h_mem))
        except Exception:
            pass
        try:
            if clipboard_opened:
                ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass


def _locate_on_disk(filepath: Path) -> None:
    """Ouvre l'explorateur Windows et sélectionne le fichier."""
    import subprocess
    if not filepath.exists():
        return
    try:
        # explorer /select,"path" ouvre le dossier et sélectionne le fichier
        subprocess.run(['explorer', '/select,', str(filepath.resolve())])
    except Exception as e:
        logger.error("Erreur localisation disque : %s", e)


def _open_directory(filepath: Path) -> None:
    """Ouvre l'explorateur Windows dans le dossier contenant le fichier."""
    import subprocess
    folder = filepath.parent
    if not folder.exists():
        return
    try:
        subprocess.run(['explorer', str(folder.resolve())])
    except Exception as e:
        logger.error("Erreur ouverture répertoire : %s", e)


def _open_in_maps(lat: float | None, lon: float | None) -> None:
    """Ouvre le navigateur sur Google Maps aux coordonnées indiquées."""
    if lat is None or lon is None:
        messagebox.showinfo("GPS", "Cette photo ne contient pas de coordonnées GPS.")
        return
    url = f"https://www.google.com/maps?q={lat},{lon}"
    webbrowser.open(url)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _get_monitor_rect(x: int, y: int) -> dict[str, int]:
    """Retourne les dimensions du moniteur contenant le point (x, y) via l'API Windows."""
    try:
        user32 = ctypes.windll.user32
        pt = POINT(x, y)
        monitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(mi))
        return {
            "x": mi.rcMonitor.left,
            "y": mi.rcMonitor.top,
            "width": mi.rcMonitor.right - mi.rcMonitor.left,
            "height": mi.rcMonitor.bottom - mi.rcMonitor.top,
        }
    except Exception:
        return {"x": 0, "y": 0, "width": 1920, "height": 1080}


def _get_all_monitors() -> list[dict[str, int]]:
    """Retourne la liste des rectangles de tous les moniteurs connectés (triés de gauche à droite)."""
    monitors = []
    try:
        user32 = ctypes.windll.user32
        
        # Log du nombre de moniteurs vu par le système (SM_CMONITORS = 80)
        sys_mon_count = user32.GetSystemMetrics(80)
        logger.info("[monitors] GetSystemMetrics(80) = %d", sys_mon_count)

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                rect = {
                    "x": mi.rcMonitor.left,
                    "y": mi.rcMonitor.top,
                    "width": mi.rcMonitor.right - mi.rcMonitor.left,
                    "height": mi.rcMonitor.bottom - mi.rcMonitor.top,
                }
                monitors.append(rect)
                logger.info("[monitors] Callback trouvé moniteur: %s", rect)
            return True

        # On définit le type de callback et on l'instancie LOCALEMENT pour éviter les problèmes de closure
        CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p)
        callback_ptr = CMPFUNC(callback)
        
        user32.EnumDisplayMonitors(None, None, callback_ptr, None)
    except Exception as e:
        logger.error("Erreur enumeration moniteurs: %s", e)

    if not monitors:
        monitors = [{"x": 0, "y": 0, "width": 1920, "height": 1080}]

    # Tri par X puis Y pour un ordre logique 1 (gauche) -> 2 (droite)
    monitors.sort(key=lambda m: (m["x"], m["y"]))
    logger.info("[monitors] %d moniteurs détectés: %s", len(monitors), monitors)
    return monitors


def _remove_accents(input_str: str | None) -> str:
    """Supprime les accents d'une chaîne de caractères pour une comparaison neutre."""
    if not input_str:
        return ""
    try:
        # Normalisation NFD (décompose les caractères accentués)
        # filtrage des caractères de type 'Mn' (Nonspacing_Mark)
        return "".join(
            c for c in unicodedata.normalize('NFD', input_str)
            if unicodedata.category(c) != 'Mn'
        )
    except Exception:
        return input_str


class FullscreenViewer(tk.Toplevel):
    """Fenêtre plein écran sans bordure pour afficher les photos."""
    
    def __init__(self, parent: Any, forced_mode: str | None = None) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self._forced_mode = forced_mode
        self._ignore_clicks_until = 0.0
        self.title("PhotoSelect - Visionneuse")
        self.configure(bg="black")
        
        self._is_fullscreen = False
        self._windowed_geometry = ""
        
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        
        # Restauration de la position sauvegardée
        geom = self.parent_app.config_data.get("geometry_viewer", "")
        was_fs = self.parent_app.config_data.get("state_viewer_fullscreen", True)
        
        if geom:
            self._windowed_geometry = geom
            self.geometry(geom)
        
        self.update_idletasks()
        
        # Passer en plein écran après placement sur le bon moniteur
        if was_fs:
            self.after(100, self._go_fullscreen)
        
        # Événements système
        self.bind("<Escape>", self._on_escape_key)
        self.bind("<F12>", lambda _e: self._toggle_fullscreen())
        self.bind("<F1>", lambda _e: self.parent_app._show_help())
        self.bind("<Double-1>", lambda _e: self._toggle_fullscreen())
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Navigation clavier/souris déléguée au parent
        self.bind("<Left>", lambda e: self.parent_app.navigate_tree("prev"))
        self.bind("<Up>", lambda e: self.parent_app.navigate_tree("prev"))
        self.bind("<Right>", lambda e: self.parent_app.navigate_tree("next"))
        self.bind("<Down>", lambda e: self.parent_app.navigate_tree("next"))
        self.bind("<Prior>", self._on_page_up)
        self.bind("<Page_Down>", self._on_page_down)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.bind("<space>", lambda e: self.parent_app.copy_current())
        self.bind("<Delete>", lambda e: self.parent_app.delete_current())
        self.bind("<F2>", lambda e: self.parent_app._rename_single())
        self.bind("<KeyPress-d>", self._start_slideshow_from_shortcut)
        self.bind("<KeyPress-D>", self._start_slideshow_from_shortcut)
        self.canvas.bind("<KeyPress-d>", self._start_slideshow_from_shortcut)
        self.canvas.bind("<KeyPress-D>", self._start_slideshow_from_shortcut)
        for n in range(1, 10):
            self.bind(f"<KeyPress-{n}>", lambda e, sec=n: self._set_slideshow_speed_from_shortcut(sec))
            self.canvas.bind(f"<KeyPress-{n}>", lambda e, sec=n: self._set_slideshow_speed_from_shortcut(sec))
            self.bind(f"<KeyPress-KP_{n}>", lambda e, sec=n: self._set_slideshow_speed_from_shortcut(sec))
            self.canvas.bind(f"<KeyPress-KP_{n}>", lambda e, sec=n: self._set_slideshow_speed_from_shortcut(sec))
            self.bind_all(f"<KeyPress-{n}>", lambda e, sec=n: self._set_slideshow_speed_from_shortcut(sec), add="+")
        self.bind("<KeyPress-plus>", self._increase_slideshow_speed)
        self.canvas.bind("<KeyPress-plus>", self._increase_slideshow_speed)
        self.bind("<KeyPress-KP_Add>", self._increase_slideshow_speed)
        self.canvas.bind("<KeyPress-KP_Add>", self._increase_slideshow_speed)
        self.bind("<KeyPress-minus>", self._decrease_slideshow_speed)
        self.canvas.bind("<KeyPress-minus>", self._decrease_slideshow_speed)
        self.bind("<KeyPress-KP_Subtract>", self._decrease_slideshow_speed)
        self.canvas.bind("<KeyPress-KP_Subtract>", self._decrease_slideshow_speed)
        self.bind_all("<KeyPress-plus>", self._increase_slideshow_speed, add="+")
        self.bind_all("<KeyPress-KP_Add>", self._increase_slideshow_speed, add="+")
        self.bind_all("<KeyPress-minus>", self._decrease_slideshow_speed, add="+")
        self.bind("<Control-r>", self._on_rotate_shortcut_right)
        self.bind("<Control-l>", self._on_rotate_shortcut_left)
        self.bind("<Control-u>", self._on_rotate_shortcut_normalize)
        
        self.bind("<Control-r>", self._on_rotate_shortcut_right)
        self.bind("<Control-l>", self._on_rotate_shortcut_left)
        self.bind("<Control-u>", self._on_rotate_shortcut_normalize)
        
        # Les raccourcis changement d'écran sont gérés globalement par ControlPanel.bind_all
        self.canvas.bind("<Delete>", lambda e: self.parent_app.delete_current())
        self.canvas.bind("<F2>", lambda e: self.parent_app._rename_single())
        self.canvas.bind("<Control-r>", self._on_rotate_shortcut_right)
        self.canvas.bind("<Control-l>", self._on_rotate_shortcut_left)
        self.canvas.bind("<Control-u>", self._on_rotate_shortcut_normalize)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Enter>", self._on_canvas_hover_motion)
        self.canvas.bind("<Motion>", self._on_canvas_hover_motion)
        self.canvas.bind("<Leave>", self._on_canvas_hover_leave)
        self.bind("<Leave>", self._on_canvas_hover_leave)
        
        self._photo_image: ImageTk.PhotoImage | None = None
        self._grid_images: list[ImageTk.PhotoImage] = []
        self._grid_index_to_path: dict[str, Path] = {}
        self._grid_mode = False
        self._grid_page_index = 0
        self._grid_total_pages = 1
        # 0 => pas de cap fixe, on affiche toutes les vignettes qui tiennent à l'écran.
        self._grid_page_size = 0
        self._grid_last_render_size: tuple[int, int] | None = None
        self._grid_preload_token = 0
        self._grid_preloaded_page: int | None = None
        self._grid_preloaded_data: list[tuple[Path, Image.Image]] = []
        self._grid_unreadable_paths: set[Path] = set()
        self._current_path: Path | None = None
        self._slideshow_active = False
        self._slideshow_paths: list[Path] = []
        self._slideshow_index = -1
        self._slideshow_job: str | None = None
        self._slideshow_interval_sec = 3
        self._slideshow_click_guard_until = 0.0
        self._ctx_target_path: Path | None = None
        self._set_slideshow_interval_seconds(
            self.parent_app.config_data.get("slideshow_interval_sec", 3),
            persist=False,
        )
        self.bind("<Configure>", self._on_resize)
        
        # Menu contextuel (clic droit)
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="Ouvrir le répertoire", command=self._open_current_directory)
        self._ctx_menu.add_command(label="Localiser sur le disque", command=self._locate_current_on_disk)
        self._ctx_menu.add_command(label="Voir sur Google Maps", command=self._open_current_in_maps)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Copier la photo", command=self._copy_photo_to_clipboard)
        self._ctx_menu.add_command(label="Ajouter à l'album...", command=self.parent_app._add_current_context_photo_to_virtual_album)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Orientation: corriger via EXIF", command=self._normalize_ctx_orientation)
        self._ctx_menu.add_command(label="Orientation: rotation 90° droite", command=self._rotate_ctx_right)
        self._ctx_menu.add_command(label="Orientation: rotation 90° gauche", command=self._rotate_ctx_left)
        self._ctx_menu.add_command(label="Orientation: rotation 180°", command=self._rotate_ctx_180)
        self._ctx_menu.add_command(label="Supprimer (Corbeille)", command=self.parent_app.delete_current)
        self._ctx_menu_delete_index = int(self._ctx_menu.index("end") or 0)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Diaporama", command=self._start_slideshow_from_current_view)
        self.bind("<ButtonRelease-3>", self._show_ctx_menu)
        self.canvas.bind("<ButtonRelease-3>", self._show_ctx_menu)

        # Un clic souris, où qu'il se produise dans l'application, arrête le diaporama.
        self.bind_all("<ButtonPress-1>", self._stop_slideshow_on_any_click, add="+")
        self.bind_all("<ButtonPress-2>", self._stop_slideshow_on_any_click, add="+")
        self.bind_all("<ButtonPress-3>", self._stop_slideshow_on_any_click, add="+")

    # ── Gestion plein écran multi-moniteur ────────────────────────────

    def _go_fullscreen(self, monitor_rect: dict[str, int] | None = None) -> None:
        """Passe en plein écran sans bordure sur le moniteur courant ou spécifié."""
        if self._is_fullscreen:
            return
        # Sauvegarder la géométrie fenêtrée actuelle
        self._windowed_geometry = self.geometry()
        
        if monitor_rect:
            mon = monitor_rect
        else:
            # Détecter le moniteur sur lequel se trouve la fenêtre
            cx = self.winfo_x() + self.winfo_width() // 2
            cy = self.winfo_y() + self.winfo_height() // 2
            mon = _get_monitor_rect(cx, cy)
        
        # Appliquer le plein écran sans bordure
        self.overrideredirect(True)
        self.state("normal")
        self.update_idletasks()
        self.geometry(f"{mon['width']}x{mon['height']}+{mon['x']}+{mon['y']}")
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self._is_fullscreen = True

    def _exit_fullscreen(self) -> None:
        if not self._is_fullscreen:
            return
        self.overrideredirect(False)
        if self._windowed_geometry:
            self.geometry(self._windowed_geometry)
        self._is_fullscreen = False

    def _toggle_fullscreen(self) -> None:
        """Bascule entre plein écran et fenêtré."""
        if self._is_fullscreen:
            self._exit_fullscreen()
        else:
            self._go_fullscreen()

    def _show_ctx_menu(self, event: tk.Event) -> str | None:
        target = self._get_hovered_path_from_canvas() if self._grid_mode else self._current_path
        if target and target.exists():
            self._ctx_target_path = target
            if self._grid_mode and target != self._current_path:
                self._update_grid_selection_state(target)
            delete_label = "Retirer de l'album" if self.parent_app._is_virtual_album_scope_active() else "Supprimer (Corbeille)"
            try:
                self._ctx_menu.entryconfigure(self._ctx_menu_delete_index, label=delete_label)
            except Exception:
                pass
            try:
                self._ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._ctx_menu.grab_release()
            return "break"
        return None

    def _move_to_monitor(self, index: int) -> str:
        """Déplace la visionneuse sur le moniteur spécifié (0-indexed)."""
        logger.info("[viewer] Demande de déplacement vers écran index %d", index)
        monitors = _get_all_monitors()
        if index < 0 or index >= len(monitors):
            logger.warning("[viewer] Index écran %d invalide (total=%d)", index, len(monitors))
            return "break"
        
        mon = monitors[index]
        logger.info("[viewer] Déplacement vers moniteur: %s", mon)
        was_fs = self._is_fullscreen
        
        # Sortir du plein écran pour pouvoir déplacer proprement
        if was_fs:
            self._exit_fullscreen()
            
        # Placement temporaire au centre du nouveau moniteur pour que _go_fullscreen le détecte
        new_x = mon['x'] + (mon['width'] // 4)
        new_y = mon['y'] + (mon['height'] // 4)
        self.geometry(f"+{new_x}+{new_y}")
        self.update_idletasks()
        
        if was_fs:
            # Re-passer en plein écran sur le nouveau moniteur (en forçant le rectangle)
            self._go_fullscreen(mon)
            
        # Mémoriser la position pour la persistance
        self.parent_app.config_data["geometry_viewer"] = self.geometry()
        self.parent_app._save_config()
        
        return "break"

    def _on_escape_key(self, _event: tk.Event | None = None) -> str | None:
        """Escape : arrête le diaporama s'il est actif, sinon quitte le plein écran."""
        slideshow_stopped = False
        try:
            if hasattr(self.parent_app, "viewer_photo") and self.parent_app.viewer_photo._slideshow_active:
                self.parent_app.viewer_photo._stop_slideshow()
                slideshow_stopped = True
            if hasattr(self.parent_app, "viewer_grid") and self.parent_app.viewer_grid._slideshow_active:
                self.parent_app.viewer_grid._stop_slideshow()
                slideshow_stopped = True
        except Exception:
            pass
        if slideshow_stopped:
            return "break"
        self._exit_fullscreen()
        return None

    def _stop_slideshow_on_any_click(self, _event: tk.Event | None = None) -> None:
        """Stoppe tout diaporama actif dès qu'un clic souris est détecté."""
        guard_until = float(getattr(self, "_slideshow_click_guard_until", 0.0))
        try:
            if hasattr(self.parent_app, "viewer_photo"):
                guard_until = max(guard_until, float(getattr(self.parent_app.viewer_photo, "_slideshow_click_guard_until", 0.0)))
            if hasattr(self.parent_app, "viewer_grid"):
                guard_until = max(guard_until, float(getattr(self.parent_app.viewer_grid, "_slideshow_click_guard_until", 0.0)))
        except Exception:
            pass

        if time.time() < guard_until:
            return
        try:
            if hasattr(self.parent_app, "viewer_photo") and self.parent_app.viewer_photo._slideshow_active:
                self.parent_app.viewer_photo._stop_slideshow()
            if hasattr(self.parent_app, "viewer_grid") and self.parent_app.viewer_grid._slideshow_active:
                self.parent_app.viewer_grid._stop_slideshow()
        except Exception:
            pass

    def _resolve_ctx_path(self) -> Path | None:
        path = self._ctx_target_path or self._current_path
        if path and path.exists():
            return path
        return None

    def _normalize_ctx_orientation(self) -> None:
        path = self._resolve_ctx_path()
        if path:
            self.parent_app._apply_orientation_correction(path, operation="normalize")

    def _rotate_ctx_right(self) -> None:
        path = self._resolve_ctx_path()
        if path:
            self.parent_app._apply_orientation_correction(path, operation="right")

    def _rotate_ctx_left(self) -> None:
        path = self._resolve_ctx_path()
        if path:
            self.parent_app._apply_orientation_correction(path, operation="left")

    def _rotate_ctx_180(self) -> None:
        path = self._resolve_ctx_path()
        if path:
            self.parent_app._apply_orientation_correction(path, operation="180")

    def _on_rotate_shortcut_right(self, _event: tk.Event | None = None) -> str:
        path = self._current_path
        if path and path.exists():
            self.parent_app._apply_orientation_correction(path, operation="right")
        return "break"

    def _on_rotate_shortcut_left(self, _event: tk.Event | None = None) -> str:
        path = self._current_path
        if path and path.exists():
            self.parent_app._apply_orientation_correction(path, operation="left")
        return "break"

    def _on_rotate_shortcut_normalize(self, _event: tk.Event | None = None) -> str:
        path = self._current_path
        if path and path.exists():
            self.parent_app._apply_orientation_correction(path, operation="normalize")
        return "break"

    def _copy_photo_to_clipboard(self) -> None:
        """Copie l'image courante dans le presse-papier."""
        if self._current_path and self._current_path.exists():
            if _copy_image_to_clipboard(self._current_path):
                logger.info("Image copiée dans le presse-papier : %s", self._current_path.name)

    def _locate_current_on_disk(self) -> None:
        """Localise l'image courante dans Windows Explorer."""
        if self._current_path:
            _locate_on_disk(self._current_path)

    def _open_current_directory(self) -> None:
        """Ouvre le répertoire de l'image courante dans Windows Explorer."""
        if self._current_path:
            _open_directory(self._current_path)

    def _open_current_in_maps(self) -> None:
        """Ouvre la position GPS de l'image courante dans Google Maps."""
        if not self._current_path: return
        # Récupérer les coordonnées depuis la DB (via le parent)
        photo_id = self.parent_app._get_id_from_path(self._current_path)
        if photo_id:
            photo = self.parent_app._get_photo_by_id(photo_id)
            if photo:
                _open_in_maps(photo.get("latitude"), photo.get("longitude"))

    def _sync_tree_selection_to_path(self, target_path: Path, center: bool = False) -> None:
        """Synchronise la sélection du tableau avec l'image affichée, tous scopes confondus."""
        if not hasattr(self.parent_app, "tree"):
            return
        tree = self.parent_app.tree
        if not target_path:
            return

        try:
            norm_target = os.path.normcase(str(target_path.resolve()))
        except Exception:
            norm_target = os.path.normcase(str(target_path))

        target_iid: str | None = None

        # Fast-path DB quand disponible.
        try:
            photo_id = self.parent_app._get_id_from_path(target_path)
            if photo_id is not None and tree.exists(str(photo_id)):
                target_iid = str(photo_id)
        except Exception:
            target_iid = None

        # Fallback: recherche par chemin dans les lignes visibles (inclut les lignes virtuelles album).
        if target_iid is None:
            for iid in tree.get_children():
                try:
                    row = self.parent_app._get_photo_by_id(int(iid))
                except Exception:
                    row = None
                if not row:
                    continue
                row_path_raw = str(row.get("filepath") or "").strip()
                if not row_path_raw:
                    continue
                row_path = Path(row_path_raw)
                try:
                    norm_row = os.path.normcase(str(row_path.resolve()))
                except Exception:
                    norm_row = os.path.normcase(str(row_path))
                if norm_row == norm_target:
                    target_iid = str(iid)
                    break

        if target_iid is None:
            return

        current = tree.selection()
        if current and current[0] == target_iid:
            if center:
                self._center_tree_item(target_iid)
            return

        self.parent_app._suspend_tree_select_once = True
        tree.selection_set(target_iid)
        tree.focus(target_iid)
        if center:
            self._center_tree_item(target_iid)

    def _center_tree_item(self, iid: str) -> None:
        """Recentre visuellement une ligne du tableau (utile pendant le diaporama)."""
        if not hasattr(self.parent_app, "tree"):
            return
        tree = self.parent_app.tree
        if not tree.exists(iid):
            return

        # Garantit un bbox disponible avant calcul de recentrage.
        tree.see(iid)

        children = tree.get_children()
        total = len(children)
        if total <= 1:
            return

        try:
            idx = children.index(iid)
        except ValueError:
            return

        bbox = tree.bbox(iid)
        if not bbox:
            return

        row_h = max(1, int(bbox[3]))
        viewport_h = max(1, int(tree.winfo_height()))
        visible_rows = max(1, viewport_h // row_h)
        top_index = max(0, min(total - 1, idx - (visible_rows // 2)))

        denom = max(1, total - 1)
        tree.yview_moveto(float(top_index) / float(denom))

    def _start_slideshow_from_current_view(self) -> None:
        """Lance un diaporama sur les lignes visibles du tableau (tri + filtre + portée active)."""
        if not hasattr(self.parent_app, "_get_visible_ordered_paths"):
            return

        try:
            candidates = self.parent_app._get_visible_ordered_paths()
        except Exception as exc:
            logger.error("Impossible de démarrer le diaporama: %s", exc)
            return

        if not candidates:
            return

        self._stop_slideshow()
        # Ignore le clic de lancement (menu/contextuel) pour éviter un arrêt immédiat.
        self._set_global_slideshow_click_guard(0.35)
        self._slideshow_paths = candidates
        try:
            if self._current_path:
                # _run_next_slide avance avant affichage: on démarre sur la ligne courante.
                self._slideshow_index = self._slideshow_paths.index(self._current_path) - 1
            else:
                self._slideshow_index = -1
        except ValueError:
            self._slideshow_index = -1
        self._slideshow_active = True
        self._update_slideshow_status_text()
        self._run_next_slide()

    def _set_global_slideshow_click_guard(self, seconds: float) -> None:
        """Applique une fenêtre d'ignore-clic sur les deux visionneuses."""
        until = time.time() + max(0.0, float(seconds))
        self._slideshow_click_guard_until = until
        try:
            if hasattr(self.parent_app, "viewer_photo"):
                self.parent_app.viewer_photo._slideshow_click_guard_until = until
            if hasattr(self.parent_app, "viewer_grid"):
                self.parent_app.viewer_grid._slideshow_click_guard_until = until
        except Exception:
            pass

    def _start_slideshow_from_current_folder(self) -> None:
        """Compat: redirige vers le diaporama basé sur la vue tableau courante."""
        self._start_slideshow_from_current_view()

    def _set_slideshow_interval_seconds(self, seconds: Any, persist: bool = True) -> None:
        """Applique et persiste la vitesse du diaporama (1..10s)."""
        try:
            value = int(float(seconds))
        except Exception:
            value = 3
        value = max(1, min(10, value))
        self._slideshow_interval_sec = value
        if persist:
            self.parent_app.config_data["slideshow_interval_sec"] = value
            self.parent_app._save_config()

        if self._slideshow_active and self._slideshow_job:
            try:
                self.after_cancel(self._slideshow_job)
            except Exception:
                pass
            self._slideshow_job = self.after(int(self._slideshow_interval_sec * 1000), self._run_next_slide)

    def _start_slideshow_from_shortcut(self, _event: tk.Event | None = None) -> str:
        """Raccourci clavier 'd'/'D' depuis la visionneuse."""
        return self.parent_app._trigger_slideshow_standard_logic()

    def _set_slideshow_speed_from_shortcut(self, seconds: int) -> str:
        """Raccourci clavier 1..9: vitesse diaporama en secondes."""
        if not self._is_any_slideshow_active():
            return ""
        self._set_slideshow_interval_seconds(seconds, persist=True)
        self._update_slideshow_status_text()
        return "break"

    def _increase_slideshow_speed(self, _event: tk.Event | None = None) -> str:
        """Raccourci '+': accélère le diaporama (sec - 1, min 1)."""
        if not self._is_any_slideshow_active():
            return ""
        self._set_slideshow_interval_seconds(self._slideshow_interval_sec - 1, persist=True)
        self._update_slideshow_status_text()
        return "break"

    def _decrease_slideshow_speed(self, _event: tk.Event | None = None) -> str:
        """Raccourci '-': ralentit le diaporama (sec + 1, max 10)."""
        if not self._is_any_slideshow_active():
            return ""
        self._set_slideshow_interval_seconds(self._slideshow_interval_sec + 1, persist=True)
        self._update_slideshow_status_text()
        return "break"

    def _is_any_slideshow_active(self) -> bool:
        return bool(getattr(self, "_slideshow_active", False)) or bool(
            hasattr(self.parent_app, "viewer") and getattr(self.parent_app.viewer, "_slideshow_active", False)
        )

    def _update_slideshow_status_text(self) -> None:
        if hasattr(self.parent_app, "var_counter"):
            self.parent_app.var_counter.set(f"Mode diaporama : {self._slideshow_interval_sec}s par photo")

    def _run_next_slide(self) -> None:
        """Affiche la prochaine image du diaporama et reprogramme l'étape suivante."""
        if not self._slideshow_active:
            return

        # Le diaporama suit en continu le tableau filtré courant (tri + recherche + portée).
        try:
            current_paths = self.parent_app._get_visible_ordered_paths()
        except Exception:
            current_paths = []

        if not current_paths:
            self._stop_slideshow()
            return

        self._slideshow_paths = current_paths

        base_idx = -1
        if self._current_path:
            try:
                base_idx = self._slideshow_paths.index(self._current_path)
            except ValueError:
                base_idx = -1
        elif 0 <= self._slideshow_index < len(self._slideshow_paths):
            base_idx = self._slideshow_index

        # Saute proprement les fichiers disparus entre deux ticks.
        attempts = 0
        while attempts < len(self._slideshow_paths):
            self._slideshow_index = (base_idx + 1 + attempts) % len(self._slideshow_paths)
            next_path = self._slideshow_paths[self._slideshow_index]
            if next_path.exists():
                self.display_image(next_path, grab_focus=False, from_slideshow=True)
                break
            attempts += 1

        if attempts >= len(self._slideshow_paths):
            self._stop_slideshow()
            return

        self._update_slideshow_status_text()
        self._slideshow_job = self.after(int(self._slideshow_interval_sec * 1000), self._run_next_slide)

    def _stop_slideshow(self) -> None:
        """Arrête le diaporama en cours."""
        self._slideshow_active = False
        self._slideshow_paths = []
        self._slideshow_index = -1
        if self._slideshow_job:
            try:
                self.after_cancel(self._slideshow_job)
            except Exception:
                pass
            self._slideshow_job = None

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        delta = getattr(event, "delta", 0)
        num = getattr(event, "num", 0)

        if num == 4:
            step = -1
        elif num == 5:
            step = 1
        elif delta > 0:
            step = -1
        elif delta < 0:
            step = 1
        else:
            return None

        if self._grid_mode:
            self._navigate_grid_page(step)
            return "break"

        if step < 0:
            self.parent_app.navigate_tree("prev")
        else:
            self.parent_app.navigate_tree("next")
        return "break"

    def _viewer_scope(self) -> str:
        try:
            if hasattr(self.parent_app, "var_viewer_scope"):
                return str(self.parent_app.var_viewer_scope.get() or "folder")
        except Exception:
            pass
        return str(self.parent_app.config_data.get("viewer_scope", "folder"))

    def _list_view_images(self, anchor_path: Path | None = None) -> list[Path]:
        scope = self._viewer_scope()
        if scope == "continuous":
            try:
                return self.parent_app._get_visible_ordered_paths()
            except Exception:
                return []
        if scope == "duplicates":
            try:
                return self.parent_app._get_duplicate_ordered_paths()
            except Exception:
                return []
        if scope == "faces":
            try:
                return self.parent_app._get_faces_ordered_paths()
            except Exception:
                return []
        if scope == "virtual_album":
            try:
                return self.parent_app._get_virtual_album_ordered_paths()
            except Exception:
                return []
        if scope == "album":
            try:
                return self.parent_app._get_album_ordered_paths()
            except Exception:
                return []

        target = anchor_path or self._current_path
        if not target:
            return []
        folder = target.parent
        if not folder.exists():
            return []
        try:
            return sorted(
                [p for p in folder.iterdir() if p.is_file() and is_supported_image(p)],
                key=lambda p: p.name.lower(),
            )
        except Exception:
            return []

    def _filter_grid_unreadable_paths(self, images: list[Path]) -> list[Path]:
        """Exclut les fichiers déjà reconnus comme illisibles en mosaïque."""
        if not self._grid_unreadable_paths:
            return images
        return [path for path in images if path not in self._grid_unreadable_paths]

    def _resolve_grid_anchor_path(
        self,
        selected_path: Path,
        raw_images: list[Path],
        displayable_images: list[Path],
    ) -> Path | None:
        """Choisit la photo lisible la plus proche pour ancrer la page mosaïque."""
        if not displayable_images:
            return None
        if selected_path in displayable_images:
            return selected_path

        displayable_set = set(displayable_images)
        try:
            raw_index = raw_images.index(selected_path)
        except ValueError:
            return displayable_images[0]

        for candidate in raw_images[raw_index + 1:]:
            if candidate in displayable_set:
                return candidate
        for candidate in reversed(raw_images[:raw_index]):
            if candidate in displayable_set:
                return candidate
        return displayable_images[0]

    def _compute_grid_layout(self) -> tuple[int, int]:
        """Calcule le nombre de colonnes et la taille de page active en mode mosaïque."""
        canvas_w = max(1, self.winfo_width() or self.winfo_screenwidth())
        canvas_h = max(1, self.winfo_height() or self.winfo_screenheight())
        pad = 8
        target_cell = 220
        cols = max(1, canvas_w // target_cell)
        cell_w = max(120, (canvas_w - (cols + 1) * pad) // cols)
        thumb_w = max(96, cell_w - 16)
        thumb_h = max(72, int(thumb_w * 0.72))
        row_h = thumb_h + 42 + pad
        visible_rows = max(1, (canvas_h - pad) // max(1, row_h))
        max_on_screen = max(1, cols * visible_rows)
        page_cap = int(self._grid_page_size or 0)
        page_size = max_on_screen if page_cap <= 0 else max(1, min(page_cap, max_on_screen))
        return cols, page_size

    def _navigate_grid_rows(self, row_step: int) -> bool:
        """Déplace la mosaïque d'une ligne (molette) en conservant le mode multiple."""
        if not self._grid_mode:
            return False

        images = self._filter_grid_unreadable_paths(self._list_view_images())
        if not images:
            return False

        cols, _page_size = self._compute_grid_layout()
        try:
            current_idx = images.index(self._current_path) if self._current_path else 0
        except ValueError:
            current_idx = 0

        target_idx = current_idx + (row_step * cols)
        target_idx = max(0, min(len(images) - 1, target_idx))
        if target_idx == current_idx:
            return True

        self.display_image(images[target_idx], grab_focus=False)
        return True

    def _navigate_grid_page(self, page_step: int) -> bool:
        """Charge la page précédente/suivante en mode mosaïque (PageUp/PageDown)."""
        if not self._grid_mode:
            return False

        images = self._filter_grid_unreadable_paths(self._list_view_images())
        if not images:
            return False

        _cols, page_size = self._compute_grid_layout()
        total_pages = max(1, (len(images) + page_size - 1) // page_size)

        try:
            current_idx = images.index(self._current_path) if self._current_path else 0
        except ValueError:
            current_idx = 0

        current_page = current_idx // page_size
        new_page = max(0, min(total_pages - 1, current_page + page_step))
        if new_page == current_page:
            return True

        local_offset = current_idx - (current_page * page_size)
        start = new_page * page_size
        end = min(start + page_size, len(images))
        target_idx = min(start + local_offset, end - 1)

        self.display_image(images[target_idx], grab_focus=False)
        return True

    def _on_page_up(self, _event: tk.Event) -> str | None:
        if self._navigate_grid_page(-1):
            return "break"
        self.parent_app.navigate_tree("prev")
        return "break"

    def _on_page_down(self, _event: tk.Event) -> str | None:
        if self._navigate_grid_page(1):
            return "break"
        self.parent_app.navigate_tree("next")
        return "break"

    def _on_canvas_click(self, event: tk.Event) -> None:
        """Bascule photo->mosaïque, ou sélectionne une miniature en mode multiple."""
        if not self._grid_mode:
            if self._current_path and self._current_path.exists():
                logger.debug(
                    "[viewer-debug] canvas_click photo->multiple path=%s grid_ready=%s grid_path=%s grid_images=%d",
                    self._current_path,
                    self.parent_app.viewer_grid._grid_mode,
                    self.parent_app.viewer_grid._current_path,
                    len(getattr(self.parent_app.viewer_grid, "_grid_images", []) or []),
                )
                try:
                    if hasattr(self.parent_app, "var_viewer_mode"):
                        self.parent_app.var_viewer_mode.set("multiple")
                    self.parent_app.config_data["viewer_mode"] = "multiple"
                    self.parent_app._save_config()
                except Exception:
                    pass
                # Fast path: si la photo courante est déjà dans la mosaïque, on évite tout rebuild.
                grid_viewer = self.parent_app.viewer_grid
                if grid_viewer._grid_mode and bool(grid_viewer._grid_images) and grid_viewer._grid_contains_path(self._current_path):
                    grid_viewer._update_grid_selection_state(self._current_path)
                    self.parent_app._switch_active_viewer("multiple", grab_focus=False, sync_geometry=False)
                else:
                    self.parent_app._on_viewer_mode_changed()
            return

        if time.time() < float(getattr(self, "_ignore_clicks_until", 0.0)):
            return

        clicked = self.canvas.find_withtag("current")
        if not clicked:
            return

        tags = self.canvas.gettags(clicked[0])
        index_tag = next((t for t in tags if t.startswith("thumb_")), "")
        if not index_tag:
            return

        target_path = self._grid_index_to_path.get(index_tag)
        if not target_path:
            return

        # La mosaïque affichée contient déjà cette vignette: on met simplement à jour
        # l'image sélectionnée pour pouvoir revenir en mode multiple sans rebuild.
        self._update_grid_selection_state(target_path)

        logger.debug(
            "[viewer-debug] canvas_click grid->photo target=%s active_viewer=%s",
            target_path,
            "grid" if self.parent_app.viewer is self.parent_app.viewer_grid else "photo",
        )

        photo_id = self.parent_app._get_id_from_path(target_path)
        if photo_id is not None and self.parent_app.tree.exists(str(photo_id)):
            # Evite un rechargement via <<TreeviewSelect>> provoqué par ce selection_set.
            self.parent_app._suspend_tree_select_once = True
            self.parent_app.tree.selection_set(str(photo_id))
            self.parent_app.tree.focus(str(photo_id))
            self.parent_app.tree.see(str(photo_id))

        # Un clic sur une miniature bascule vers le mode photo sur l'image cliquée.
        try:
            # Evite la resynchronisation cachée de la mosaïque pendant ce basculement.
            self.parent_app._skip_hidden_grid_sync_once = True
            if hasattr(self.parent_app, "var_viewer_mode"):
                self.parent_app.var_viewer_mode.set("photo")
            self.parent_app.config_data["viewer_mode"] = "photo"
            self.parent_app._save_config()
        except Exception:
            pass
        self.parent_app._on_viewer_mode_changed()

    def _get_hovered_path_from_canvas(self) -> Path | None:
        """Retourne le chemin réellement survolé dans la visionneuse."""
        if not self._grid_mode:
            return self._current_path if self._current_path and self._current_path.exists() else None

        current = self.canvas.find_withtag("current")
        if not current:
            return None
        tags = self.canvas.gettags(current[0])
        index_tag = next((t for t in tags if t.startswith("thumb_")), "")
        if not index_tag:
            return None
        path = self._grid_index_to_path.get(index_tag)
        if path and path.exists():
            return path
        return None

    def _on_canvas_hover_motion(self, _event: tk.Event) -> None:
        path = self._get_hovered_path_from_canvas()
        if path is not None:
            self.parent_app._set_counter_hover_directory(f"viewer-{id(self)}", path)
        else:
            self.parent_app._clear_counter_hover_directory(f"viewer-{id(self)}")

    def _on_canvas_hover_leave(self, _event: tk.Event) -> None:
        self.parent_app._clear_counter_hover_directory(f"viewer-{id(self)}")

    def _grid_contains_path(self, path: Path) -> bool:
        """Retourne True si un chemin est présent dans la page mosaïque actuellement affichée."""
        return any(item_path == path for item_path in self._grid_index_to_path.values())

    def _update_grid_selection_state(self, selected_path: Path) -> None:
        """Met à jour la sélection visuelle de la grille sans reconstruire les miniatures."""
        self._current_path = selected_path
        self.parent_app._update_logo_preview(selected_path)
        for index_tag, item_path in self._grid_index_to_path.items():
            border = "#00c2ff" if item_path == selected_path else "#2f3b45"
            for item_id in self.canvas.find_withtag(index_tag):
                if self.canvas.type(item_id) == "rectangle":
                    self.canvas.itemconfigure(item_id, outline=border)

    def _clear_grid_preload_cache(self) -> None:
        """Invalide le préchargement en cours et libère le cache de miniatures préchargées."""
        self._grid_preload_token += 1
        self._grid_preloaded_page = None
        self._grid_preloaded_data = []

    def _preload_grid_page(
        self,
        images: list[Path],
        page_index: int,
        page_size: int,
        thumb_w: int,
        thumb_h: int,
        token: int,
    ) -> None:
        """Précharge une page de miniatures en arrière-plan sans créer d'objets Tk."""
        start = page_index * page_size
        end = min(start + page_size, len(images))
        page_images = images[start:end]
        loaded: list[tuple[Path, Image.Image]] = []

        for img_path in page_images:
            if token != self._grid_preload_token:
                return
            try:
                with Image.open(img_path) as opened_img:
                    thumb = ImageOps.exif_transpose(opened_img.copy())
                    thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                    if thumb.mode not in ("RGB", "L"):
                        thumb = thumb.convert("RGB")
                    loaded.append((img_path, thumb.copy()))
            except Exception:
                continue

        if token != self._grid_preload_token:
            return

        def _apply_preload() -> None:
            if token != self._grid_preload_token:
                return
            self._grid_preloaded_page = page_index
            self._grid_preloaded_data = loaded

        self.after(0, _apply_preload)

    def _schedule_preload_next_page(
        self,
        images: list[Path],
        current_page: int,
        total_pages: int,
        page_size: int,
        thumb_w: int,
        thumb_h: int,
    ) -> None:
        """Déclenche le préchargement de la page suivante du mode multiple."""
        next_page = current_page + 1
        if next_page >= total_pages:
            self._grid_preloaded_page = None
            self._grid_preloaded_data = []
            return

        if self._grid_preloaded_page == next_page and self._grid_preloaded_data:
            return

        token = self._grid_preload_token
        threading.Thread(
            target=self._preload_grid_page,
            args=(images, next_page, page_size, thumb_w, thumb_h, token),
            daemon=True,
        ).start()

    def _display_multiple_grid(self, selected_path: Path, grab_focus: bool = True, keep_hidden: bool = False) -> None:
        """Affiche une page de miniatures (max 50) adaptée à la zone visible."""
        raw_images = self._list_view_images(anchor_path=selected_path)
        images = self._filter_grid_unreadable_paths(raw_images)
        resolved_selected_path = self._resolve_grid_anchor_path(selected_path, raw_images, images)
        if resolved_selected_path is not None:
            selected_path = resolved_selected_path

        logger.info(
            "[viewer-debug] build_grid selected=%s keep_hidden=%s grab_focus=%s raw_count=%d displayable_count=%d unreadable_cached=%d viewer=%s mapped=%s current=%s canvas_size=%sx%s",
            selected_path,
            keep_hidden,
            grab_focus,
            len(raw_images),
            len(images),
            len(self._grid_unreadable_paths),
            self._forced_mode or "dynamic",
            self.winfo_ismapped(),
            self._current_path,
            self.winfo_width(),
            self.winfo_height(),
        )

        if not images:
            self.canvas.delete("all")
            return

        self._grid_mode = True
        self._grid_images = []
        self._grid_index_to_path = {}
        self._clear_grid_preload_cache()

        self._current_path = selected_path
        if not keep_hidden:
            self.deiconify()
        if grab_focus and not keep_hidden:
            self.focus_force()

        canvas_w = max(1, self.winfo_width() or self.winfo_screenwidth())
        canvas_h = max(1, self.winfo_height() or self.winfo_screenheight())
        self._grid_last_render_size = (canvas_w, canvas_h)
        pad = 8

        target_cell = 220
        cols = max(1, canvas_w // target_cell)
        cell_w = max(120, (canvas_w - (cols + 1) * pad) // cols)
        thumb_w = max(96, cell_w - 16)
        thumb_h = max(72, int(thumb_w * 0.72))

        row_h = thumb_h + 42 + pad
        visible_rows = max(1, (canvas_h - pad) // max(1, row_h))
        max_on_screen = max(1, cols * visible_rows)
        page_cap = int(self._grid_page_size or 0)
        page_size = max_on_screen if page_cap <= 0 else max(1, min(page_cap, max_on_screen))

        try:
            selected_idx = images.index(selected_path)
        except ValueError:
            selected_idx = 0

        total_pages = max(1, (len(images) + page_size - 1) // page_size)
        page_index = min(total_pages - 1, selected_idx // page_size)
        start = page_index * page_size
        end = min(start + page_size, len(images))

        self._grid_page_index = page_index
        self._grid_total_pages = total_pages

        self.canvas.delete("all")

        total_to_load = max(0, end - start)
        self.parent_app.progress.configure(maximum=max(1, total_to_load), value=0)
        self.parent_app.var_counter.set(f"Visionneuse Mosaïc: chargement 0/{total_to_load}")

        preloaded_map: dict[Path, Image.Image] = {}
        if self._grid_preloaded_page == page_index and self._grid_preloaded_data:
            preloaded_map = {path: img for path, img in self._grid_preloaded_data}

        loaded = 0
        scan_index = start
        while scan_index < len(images) and loaded < total_to_load:
            img_path = images[scan_index]
            idx = start + loaded
            col = loaded % cols
            row = loaded // cols
            x0 = pad + col * (cell_w + pad)
            y0 = pad + row * (thumb_h + 42 + pad)

            try:
                if img_path in preloaded_map:
                    tk_img = ImageTk.PhotoImage(preloaded_map[img_path])
                else:
                    with Image.open(img_path) as opened_img:
                        thumb = ImageOps.exif_transpose(opened_img.copy())
                        thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                        if thumb.mode not in ("RGB", "L"):
                            thumb = thumb.convert("RGB")
                        tk_img = ImageTk.PhotoImage(thumb)
            except Exception as exc:
                self._grid_unreadable_paths.add(img_path)
                logger.warning("[viewer-debug] Image load FAILED for %s: %s", img_path, exc)
                scan_index += 1
                continue

            self._grid_images.append(tk_img)
            tag = f"thumb_{idx}"
            self._grid_index_to_path[tag] = img_path

            border = "#00c2ff" if img_path == selected_path else "#2f3b45"
            self.canvas.create_rectangle(
                x0,
                y0,
                x0 + cell_w,
                y0 + thumb_h + 30,
                outline=border,
                width=2,
                fill="#11161b",
                tags=(tag, "thumb"),
            )
            self.canvas.create_image(
                x0 + cell_w // 2,
                y0 + thumb_h // 2 + 2,
                image=tk_img,
                anchor="center",
                tags=(tag, "thumb"),
            )
            self.canvas.create_text(
                x0 + cell_w // 2,
                y0 + thumb_h + 16,
                text=img_path.name,
                fill="#d5dbe3",
                font=("Segoe UI", 9),
                width=cell_w - 10,
                justify="center",
                tags=(tag, "thumb"),
            )

            loaded += 1
            scan_index += 1
            if loaded % 10 == 0 or loaded == total_to_load:
                self.parent_app.progress.configure(value=loaded)
                self.parent_app.var_counter.set(f"Visionneuse Mosaïc: chargement {loaded}/{total_to_load}")
                self.parent_app.update_idletasks()

        images = self._filter_grid_unreadable_paths(images)
        total_pages = max(1, (len(images) + page_size - 1) // page_size)
        self._grid_total_pages = total_pages
        self.parent_app.var_counter.set(
            f"Visionneuse Mosaïc: page {page_index + 1}/{total_pages}, {loaded} miniatures chargées"
        )
        self._schedule_preload_next_page(images, page_index, total_pages, page_size, thumb_w, thumb_h)

    def display_image(
        self,
        filepath: Path,
        grab_focus: bool = True,
        from_slideshow: bool = False,
        allow_during_edit: bool = False,
        keep_hidden: bool = False,
    ) -> None:
        """Affiche une image redimensionnée."""
        if getattr(self.parent_app, "_editing_dialog_open", False) and not allow_during_edit:
            self._current_path = filepath
            return

        viewer_mode = self._forced_mode or "photo"
        previous_path = self._current_path
        if self._forced_mode is None:
            try:
                if hasattr(self.parent_app, "var_viewer_mode"):
                    viewer_mode = str(self.parent_app.var_viewer_mode.get() or "photo")
                else:
                    viewer_mode = str(self.parent_app.config_data.get("viewer_mode", "photo"))
            except Exception:
                viewer_mode = "photo"

        logger.debug(
            "[viewer-debug] display_image viewer=%s mode=%s path=%s keep_hidden=%s grab_focus=%s from_slideshow=%s grid_mode_before=%s current_before=%s",
            self._forced_mode or "dynamic",
            viewer_mode,
            filepath,
            keep_hidden,
            grab_focus,
            from_slideshow,
            self._grid_mode,
            self._current_path,
        )

        if viewer_mode == "multiple" and not from_slideshow:
            self._stop_slideshow()
            self._display_multiple_grid(filepath, grab_focus=grab_focus, keep_hidden=keep_hidden)
            return

        self._grid_mode = False
        self._grid_images = []
        self._grid_index_to_path = {}
        self._grid_last_render_size = None
        self._clear_grid_preload_cache()

        if self._slideshow_active and not from_slideshow:
            self._stop_slideshow()

        if not filepath.exists():
            # Si le chemin courant est devenu obsolète (ex: renommage),
            # on tente de resynchroniser avec la ligne sélectionnée du tableau.
            try:
                selection = self.parent_app.tree.selection()
                if selection:
                    photo_id = int(selection[0])
                    row = self.parent_app._get_photo_by_id(photo_id)
                    if row and row.get("filepath"):
                        synced_path = Path(row["filepath"])
                        if synced_path.exists() and synced_path != filepath:
                            self._current_path = synced_path
                            self.display_image(
                                synced_path,
                                grab_focus=grab_focus,
                                from_slideshow=from_slideshow,
                                allow_during_edit=allow_during_edit,
                            )
                            return
            except Exception:
                pass

            logger.warning("Image introuvable pour la visionneuse: %s", filepath)
            if from_slideshow:
                return
            # Chemin transitoirement obsolète: on garde l'affichage courant,
            # la sélection suivante resynchronisera la visionneuse.
            return

        self._current_path = filepath
        if previous_path != filepath:
            self._sync_tree_selection_to_path(filepath, center=from_slideshow)
        self.parent_app._update_logo_preview(filepath)
        if not keep_hidden:
            self.deiconify()
        if grab_focus and not keep_hidden:
            self.focus_force()
        
        try:
            with Image.open(filepath) as opened_img:
                img = opened_img.copy()
                # Rotation automatique basée sur EXIF orientation
                try:
                    from PIL import ExifTags
                    orientation = next((k for k, v in ExifTags.TAGS.items() if v == 'Orientation'), None)
                    if orientation is not None:
                        exif = opened_img.getexif()
                        if exif is not None and orientation in exif:
                            if exif[orientation] == 3: img = img.rotate(180, expand=True)
                            elif exif[orientation] == 6: img = img.rotate(270, expand=True)
                            elif exif[orientation] == 8: img = img.rotate(90, expand=True)
                except Exception:
                    pass

            canvas_w = self.winfo_width() or self.winfo_screenwidth()
            canvas_h = self.winfo_height() or self.winfo_screenheight()

            img_w, img_h = img.size
            if img_w > 0 and img_h > 0:
                ratio = min(canvas_w / img_w, canvas_h / img_h)
                new_w, new_h = max(1, int(img_w * ratio)), max(1, int(img_h * ratio))

                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                self._photo_image = ImageTk.PhotoImage(img)

                self.canvas.delete("all")
                self.canvas.create_image(canvas_w // 2, canvas_h // 2, image=self._photo_image, anchor="center")
        except Exception as exc:
            logger.error("Erreur visionneuse %s : %s", filepath, exc)
            self.canvas.delete("all")
            self.canvas.create_text(
                self.winfo_width() // 2, self.winfo_height() // 2,
                text=f"Impossible de lire l'image\n{exc}", fill="red", font=("Arial", 14)
            )

    def _on_resize(self, event: tk.Event) -> None:
        if hasattr(self, '_resize_timer'):
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(200, self._redraw_current)
        
    def _redraw_current(self) -> None:
        if getattr(self.parent_app, "_editing_dialog_open", False):
            return
        if getattr(self, "_skip_next_redraw", False):
            logger.debug(
                "[viewer-debug] redraw_current skip_source viewer=%s path=%s mapped=%s",
                self._forced_mode or "dynamic",
                self._current_path,
                self.winfo_ismapped(),
            )
            self._skip_next_redraw = False
            return
        keep_hidden = not bool(self.winfo_viewable())
        if self._grid_mode and self._current_path and self._current_path.exists():
            current_size = (max(1, self.winfo_width()), max(1, self.winfo_height()))
            if getattr(self, "_skip_next_grid_redraw", False):
                logger.debug(
                    "[viewer-debug] redraw_current grid skip_once path=%s size=%s last=%s keep_hidden=%s",
                    self._current_path,
                    current_size,
                    self._grid_last_render_size,
                    keep_hidden,
                )
                self._skip_next_grid_redraw = False
                self._grid_last_render_size = current_size
                return
            if self._grid_last_render_size == current_size:
                return
            logger.info(
                "[viewer-debug] redraw_current grid reload path=%s size=%s last=%s keep_hidden=%s",
                self._current_path,
                current_size,
                self._grid_last_render_size,
                keep_hidden,
            )
            self.display_image(self._current_path, grab_focus=False, keep_hidden=keep_hidden)
            return
        if self._current_path and self._current_path.exists():
            logger.debug(
                "[viewer-debug] redraw_current photo reload path=%s keep_hidden=%s size=%sx%s",
                self._current_path,
                keep_hidden,
                max(1, self.winfo_width()),
                max(1, self.winfo_height()),
            )
            self.display_image(self._current_path, grab_focus=False, keep_hidden=keep_hidden)

    def _on_closing(self) -> None:
        """Cache la fenêtre au lieu de la fermer, et sauvegarde sa géométrie."""
        self._stop_slideshow()
        if self._is_fullscreen:
            self._exit_fullscreen()
        self.parent_app.config_data["geometry_viewer"] = self.geometry().replace("+-", "-")
        self.parent_app.config_data["state_viewer_fullscreen"] = self._is_fullscreen
        self.parent_app._save_config()
        self.withdraw()


if FileSystemEventHandler is not None:
    class PhotoWatcher(FileSystemEventHandler):
        """Gère les événements systèmes de fichiers pour synchroniser la base en temps réel."""
        def __init__(self, app: ControlPanel):
            super().__init__()
            self.app = app
            self._last_processed_paths: dict[str, float] = {}

        def _should_ignore_path(self, path: Path) -> bool:
            if not is_supported_image(path):
                return True
            try:
                return bool(self.app._is_path_excluded_from_source_scan(path))
            except Exception:
                return is_ignored_scan_path(path)

        def on_created(self, event):
            if event.is_directory:
                return
            self._handle_change(event.src_path)

        def on_moved(self, event):
            if not event.is_directory:
                self._handle_change(event.dest_path)

        def _handle_change(self, path_str: str):
            path = Path(path_str)
            if self._should_ignore_path(path):
                return
            
            # Anti-rebond (debounce) pour éviter de traiter deux fois le même fichier (ex: écriture lente)
            now = time.time()
            if path_str in self._last_processed_paths:
                if now - self._last_processed_paths[path_str] < 1.0:
                    return
            self._last_processed_paths[path_str] = now
            
            logger.debug("[watcher] Nouveau fichier détecté: %s", path.name)
            self.app.after(500, lambda: self.app._sync_single_file_from_watcher(path))

        def on_deleted(self, event):
            if event.is_directory:
                return
            path = Path(event.src_path)
            if self._should_ignore_path(path):
                return
            self.app.after(300, lambda: self.app._sync_deletion_from_watcher(path))
else:
    class PhotoWatcher: # Fallback dummy
        def __init__(self, *args, **kwargs): pass

class ControlPanel(tk.Tk):
    """Panneau de contrôle principal (Fenêtre fixe)."""
    
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE + " - Panneau de Contrôle")
        
        # Configuration de l'icône de l'application
        try:
            icon_path = get_resource_path("src/photoselect/assets/mch_logo.png")
            if icon_path.exists():
                # On utilise iconphoto qui supporte bien les PNG sur Windows et Linux
                icon_img = ImageTk.PhotoImage(Image.open(icon_path))
                self.iconphoto(True, icon_img)
        except Exception as e:
            logger.debug("Impossible de charger l'icône : %s", e)
        
        self._load_config()
        self._saved_source_paths: list[str] = []
        self._saved_album_output_paths: list[str] = []
        self._saved_faces_output_paths: list[str] = []
        self._load_saved_path_lists()
        self.db: Database | None = None
        self.viewer_photo = FullscreenViewer(self, forced_mode="photo")
        self.viewer_grid = FullscreenViewer(self, forced_mode="multiple")
        self.viewer_grid.withdraw()
        self.viewer = self.viewer_photo
        
        # Threads
        self._scan_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._count_thread: threading.Thread | None = None
        self._count_stop_event = threading.Event()
        self._count_request_id = 0
        self._current_source_dir: Path | None = None
        self._editing_dialog_open = False
        self._is_searching = False
        self._audit_thread: threading.Thread | None = None
        self._geo_clipboard: dict[str, Any] | None = None
        self.var_search = tk.StringVar()
        self._scope_injected_search_filter = ""
        self._scope_injected_search_armed = False
        self._last_viewer_scope = str(self.config_data.get("viewer_scope", "folder"))
        self._pending_restore_view_path: str = ""
        self._map_points: list[tuple[int, float, float]] = []
        self._map_selected_photo_id: int | None = None
        self._map_point_coords: dict[int, tuple[float, float]] = {}
        
        # Chemins fixes (portabilité)
        self.var_in = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_faces = tk.StringVar()
        self.var_doublons = tk.StringVar()
        
        # Chemins de données projet (initialisés au choix de la source)
        self._album_index_db: Path | None = None
        self._ops_log_file: Path | None = None
        
        self._map_drag_start_xy: tuple[float, float] | None = None
        self._map_drag_rect_id: int | None = None
        self._map_filter_bbox: tuple[float, float, float, float] | None = None
        self._map_view_lon_min = -180.0
        self._map_view_lon_max = 180.0
        self._map_view_lat_min = -90.0
        self._map_view_lat_max = 90.0
        self._map_min_lon_span = 18.0
        self._map_min_lat_span = 9.0
        self._map_shape_polygons: list[tuple[tuple[float, float, float, float], list[tuple[float, float]]]] = []
        self._map_shapes_loaded = False
        self._map_shapes_loading = False
        self._startup_hydration_complete = False
        self._album_virtual_rows: dict[int, dict[str, Any]] = {}
        self._virtual_album_sidebar_rows: list[dict[str, Any]] = []
        self._active_virtual_album_id: int | None = None
        self._active_virtual_album_name = str(self.config_data.get("last_virtual_album_name") or "").strip()
        self._virtual_album_listbox_syncing = False
        self._duplicate_photo_ids: set[int] = set()
        self._duplicate_group_count: int = 0
        self._duplicate_cache_valid: bool = False
        self._duplicate_prepare_in_progress: bool = False
        self._duplicate_prepare_thread: threading.Thread | None = None
        self._duplicate_prepare_prev_counter: str = ""
        self._album_watch_signature: tuple[str, int] | None = None
        self._album_watch_job: str | None = None
        self._logo_default_photo: ImageTk.PhotoImage | None = None
        self._logo_active_photo: ImageTk.PhotoImage | None = None
        self._logo_active_path: Path | None = None
        self._logo_slot_size: tuple[int, int] | None = None
        self._counter_last_non_hover_text: str = ""
        self._counter_hover_source: str | None = None
        self._counter_hover_internal_set: bool = False
        self._counter_sticky_until: float = 0.0
        self._coffee_lines_cache: list[str] = []
        self._coffee_last_refresh_ts: float = 0.0
        self._orientation_ctx_path: Path | None = None
        self._face_worker_thread: threading.Thread | None = None
        self._face_worker_stop = threading.Event()
        self._face_worker_running = False
        self._face_worker_shutdown_requested = False
        self._face_worker_paused_for_audit = False
        self._face_zero_result_reindex_done = False
        self._face_runtime_status = "Désactivée"
        self._face_engine_perf: dict[str, dict[str, float]] = {
            "face_recognition": {"count": 0.0, "total_sec": 0.0},
            "opencv": {"count": 0.0, "total_sec": 0.0},
            "mediapipe": {"count": 0.0, "total_sec": 0.0},
        }
        self._face_thumb_refs: list[ImageTk.PhotoImage] = []
        self._faces_selected_thumb: tuple[int, str] | None = None
        self._faces_selected_thumb_widget: tk.Misc | None = None
        self._face_edit_queue: dict[str, dict[str, Any]] = {}
        self._face_edit_queue_lock = threading.Lock()
        self._face_batch_thread: threading.Thread | None = None
        self._face_batch_running = False
        self._face_batch_stop = threading.Event()
        self._face_batch_last_progress_ts: float = 0.0
        self._theme_classify_thread: threading.Thread | None = None
        self._theme_classify_running = False
        self._theme_classify_waiting_for_face_stop = False
        self._theme_worker_thread: threading.Thread | None = None
        self._theme_worker_stop = threading.Event()
        self._theme_worker_running = False
        self._theme_runtime_status = str(self.config_data.get("stats_theme_runtime_status") or "Inactif")
        self._theme_last_summary = str(self.config_data.get("stats_theme_last_summary") or "-")
        self._faces_inline_edit_open = False
        self._faces_inline_selected_target: str = ""
        self._faces_tab_needs_refresh = True
        self._faces_tab_visible = False
        self._startup_load_token = 0
        self._startup_tree_loading = False
        self._startup_cleanup_thread: threading.Thread | None = None
        self._faces_preload_job: str | None = None
        self._faces_preload_interval_ms = 2200
        self._last_ui_activity_ts = time.time()
        # Etat de tri disponible avant toute action UI qui charge les donnees.
        self._sort_col = "date_taken"
        self._sort_asc = True
        self._init_album_index_registry()
        self._selected_face_target_person_name: str | None = None
        raw_face_id = self.config_data.get("active_face_person_id")
        self._active_face_person_id: int | None = int(raw_face_id) if raw_face_id is not None else None
        self._face_person_blocks: dict[int, tk.Frame] = {}
        self._pending_scroll_person_id: int | None = None
        self._faces_sidebar_syncing = False
        self._last_notebook_tab_id: str | None = None
        self._notebook_tab_change_guard = False
        
        # Initialisation du scaling dynamique (Homothétie)
        self._scaling_base_width = 1600  # Largeur de référence pour le design
        self._scaling_original_value = self.tk.call('tk', 'scaling')
        self._last_scaled_width = 0
        # Initialisation variables d'état UI
        self.var_face_recognition_enabled = tk.BooleanVar(value=bool(self.config_data.get("face_recognition_enabled", False)))
        self.var_auto_sync_enabled = tk.BooleanVar(value=bool(self.config_data.get("auto_sync_enabled", True)))
        self.var_faces_show_only_marked = tk.BooleanVar(value=False)
        self.var_faces_hide_named = tk.BooleanVar(value=bool(self.config_data.get("faces_hide_named", False)))

        # Initialisation Watcher (Auto-Sync)
        self._watcher: Observer | None = None
        self._watcher_handler: PhotoWatcher | None = None
        self._watcher_ui_refresh_job: str | None = None
        
        geom = self.config_data.get("geometry_main", "1150x750")
        self.geometry(geom)
        
        # On force l'état agrandi (zoomed) pour répondre à la demande de plein écran avec contrôles.
        # On utilise zoomed plutôt que normal pour occuper tout l'écran.
        try:
            self.state("zoomed")
        except Exception:
            self.state("normal")
        
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        self._set_style()
        self._build_ui()
        self._init_photo_orientation_menu()
        self._set_ui_initializing_state(True)
        
        # Activer le suivi du redimensionnement pour l'homothétie
        self.bind("<Configure>", self._handle_homothety_resize)
        
        # Auto-chargement depuis le config
        initial_in = self.var_in.get()
        configured_source = str(self.config_data.get("last_viewed_source") or "").strip()
        configured_view = str(self.config_data.get("last_viewed_path") or "").strip()
        if configured_source and configured_view:
            try:
                if initial_in and Path(configured_source).resolve() == Path(initial_in).resolve():
                    self._pending_restore_view_path = configured_view
            except Exception:
                self._pending_restore_view_path = ""
        self.after(30, self._bootstrap_startup_db_load)

        # Fenêtre d'information affichée en fin d'initialisation.
        self.after(350, self._show_startup_layout_info_if_needed)
        self._bind_ui_activity_tracking()
        
        # Démarrage Watcher
        if self.var_auto_sync_enabled.get():
            self.after(3000, self._start_watcher)

        # Force le mode agrandi une seconde fois après un court délai pour garantir le plein écran
        self.after(200, lambda: self.state("zoomed"))
            
        # Raccourcis clavier (Task 1, 2, 4)
        self.bind_all("<Key-v>", lambda e: self._on_shortcut_mark_face(1))
        self.bind_all("<Key-V>", lambda e: self._on_shortcut_mark_face(1))
        self.bind_all("<Key-f>", lambda e: self._on_shortcut_mark_face(-1))
        self.bind_all("<Key-F>", lambda e: self._on_shortcut_mark_face(-1))
        self.bind_all("<Key-d>", self._on_global_start_slideshow_shortcut, add="+")
        self.bind_all("<Key-D>", self._on_global_start_slideshow_shortcut, add="+")
        # Raccourci P/p pour basculer entre UI principale et visionneuses
        self.bind_all("<Key-p>", self._toggle_window_groups_focus)
        self.bind_all("<Key-P>", self._toggle_window_groups_focus)
        
        # Raccourcis changement d'écran pour les visionneuses (Ctrl+1, Ctrl+2, ...)
        for i in range(1, 4):
            self.bind_all(f"<Control-Key-{i}>", lambda e, idx=i-1: self._move_viewers_to_monitor(idx), add="+")
            self.bind_all(f"<Control-KP_{i}>", lambda e, idx=i-1: self._move_viewers_to_monitor(idx), add="+")

    def _toggle_window_groups_focus(self, event: tk.Event | None = None) -> str:
        """Bascule le premier plan entre le panneau principal et le groupe des visionneuses."""
        focus_widget = self.focus_get()
        focus_top = None
        if focus_widget is not None:
            try:
                focus_top = focus_widget.winfo_toplevel()
            except Exception:
                focus_top = None

        editable_widgets = (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Listbox)
        if focus_widget is not None and isinstance(focus_widget, editable_widgets):
            return "break"

        viewer_windows: list[FullscreenViewer] = []
        for viewer in (getattr(self, "viewer_photo", None), getattr(self, "viewer_grid", None)):
            if viewer is None:
                continue
            try:
                if int(viewer.winfo_exists()):
                    viewer_windows.append(viewer)
            except Exception:
                continue

        focus_is_viewer = focus_top in viewer_windows
        if focus_is_viewer:
            try:
                self.lift()
                self.focus_force()
            except Exception:
                pass
            return "break"

        active_viewer = getattr(self, "viewer", None)
        inactive_viewers = [viewer for viewer in viewer_windows if viewer is not active_viewer]

        for viewer in inactive_viewers:
            try:
                viewer.deiconify()
                viewer.lift()
            except Exception:
                pass

        if active_viewer in viewer_windows:
            try:
                active_viewer.deiconify()
                active_viewer.lift()
                active_viewer.focus_force()
            except Exception:
                pass
        elif viewer_windows:
            try:
                viewer_windows[-1].deiconify()
                viewer_windows[-1].lift()
                viewer_windows[-1].focus_force()
            except Exception:
                pass

        return "break"

    def _move_viewers_to_monitor(self, index: int) -> str:
        """Déplace toutes les visionneuses vers le moniteur spécifié (0-indexed)."""
        logger.info("[app] Commande globale: déplacer les visionneuses vers écran %d", index + 1)
        for v in (getattr(self, "viewer_photo", None), getattr(self, "viewer_grid", None)):
            if v is not None:
                try:
                    if int(v.winfo_exists()):
                        v._move_to_monitor(index)
                except Exception:
                    pass
        return "break"

    def _start_watcher(self) -> None:
        """Démarre le thread de surveillance du dossier source."""
        if self._watcher or not self._current_source_dir:
            return
        self._watcher = Observer()
        self._watcher_handler = PhotoWatcher(self)
        self._watcher.schedule(self._watcher_handler, str(self._current_source_dir), recursive=True)
        self._watcher.start()
        logger.info("[watcher] Surveillance démarrée sur %s", self._current_source_dir)

    def _stop_watcher(self) -> None:
        """Arrête le thread de surveillance."""
        if self._watcher:
            self._watcher.stop()
            self._watcher.join()
            self._watcher = None
            self._watcher_handler = None
            logger.info("[watcher] Surveillance arrêtée.")

    def _on_auto_sync_toggle(self) -> None:
        """Active/Désactive la surveillance temps réel."""
        if self.var_auto_sync_enabled.get():
            self._start_watcher()
        else:
            self._stop_watcher()
        self._save_config()

    def _bootstrap_startup_db_load(self) -> None:
        """Charge la base configurée après affichage UI pour accélérer le démarrage perçu."""
        try:
            initial_in = str(self.var_in.get() or "").strip() if hasattr(self, "var_in") else ""
            if not initial_in:
                self._startup_hydration_complete = True
                return

            path_in = Path(initial_in)
            db_file = self._get_db_file_for_source(path_in)
            legacy_db = path_in / ".photoselect.db"
            if not path_in.exists() or not (db_file.exists() or legacy_db.exists()):
                self._startup_hydration_complete = True
                return

            self.var_counter.set("Initialisation en cours...")
            self.update_idletasks()

            self._init_db(
                path_in,
                cleanup_entries=False,
                start_face_worker=False,
                update_copies=False,
                schedule_faces_preload=False,
            )
            if not self.db:
                self._startup_hydration_complete = True
                return

            photo_count = self.db.get_photo_count()
            if photo_count <= 0:
                self._update_copies_counter()
                self._schedule_delayed_startup_services(self._startup_load_token)
                return

            self.var_counter.set(
                f"Chargement base locale: {photo_count} photos (pas de scan disque)..."
            )
            threading.Thread(
                target=self._startup_snapshot_worker,
                args=(self._get_db_file_for_source(path_in), self._startup_load_token),
                daemon=True,
            ).start()
        finally:
            self._on_face_recognition_toggle(initialize_only=True)
            self._set_ui_initializing_state(False)

    def _set_startup_counter_message(self, photo_count: int) -> None:
        """Applique le message de fin d'initialisation selon le contexte courant."""
        self._refresh_contextual_counter(force=True, startup_photo_count=photo_count)

    def _start_watcher(self) -> None:
        """Démarre la surveillance du dossier source en temps réel."""
        if Observer is None: return
        if self._watcher and self._watcher.is_alive(): return
        
        path_in = str(self.var_in.get() or "").strip()
        if not path_in or not Path(path_in).exists(): return
        
        try:
            self._watcher_handler = PhotoWatcher(self)
            self._watcher = Observer()
            self._watcher.schedule(self._watcher_handler, path_in, recursive=True)
            self._watcher.start()
            logger.info("[watcher] Service de surveillance démarré sur: %s", path_in)
        except Exception as e:
            logger.error("[watcher] Erreur démarrage: %s", e)
            self._watcher = None

    def _stop_watcher(self) -> None:
        """Arrête le service de surveillance."""
        if self._watcher:
            try:
                self._watcher.stop()
                self._watcher.join(timeout=2.0)
            except Exception: pass
            self._watcher = None
            logger.info("[watcher] Service de surveillance arrêté.")

    def _on_auto_sync_toggle(self) -> None:
        """Active/Désactive la surveillance en temps réel selon la checkbox."""
        enabled = bool(self.var_auto_sync_enabled.get())
        self.config_data["auto_sync_enabled"] = enabled
        self._save_config()
        if enabled:
            self._start_watcher()
        else:
            self._stop_watcher()

    def _schedule_watcher_ui_refresh(self) -> None:
        """Regroupe les rafraîchissements UI issus du watcher pour éviter de figer l'interface."""
        if self._watcher_ui_refresh_job is not None:
            try:
                self.after_cancel(self._watcher_ui_refresh_job)
            except Exception:
                pass
        self._watcher_ui_refresh_job = self.after(250, self._apply_watcher_ui_refresh)

    def _apply_watcher_ui_refresh(self) -> None:
        self._watcher_ui_refresh_job = None
        self._update_statistics()
        if not self._is_searching and not self._is_album_scope_active():
            self._load_data_to_treeview()

    def _sync_single_file_from_watcher(self, path: Path) -> None:
        """Synchronise un fichier spécifique ajouté/modifié sur le disque."""
        if not self.db or not path.exists(): return
        try:
            meta = extract_all_metadata(path)
            meta["filepath"] = self._rel_path(path)
            self.db.upsert_photo(meta)
            logger.info("[watcher] Synchronisé: %s", path.name)
            
            # Relancer les workers IA pour traiter le nouveau fichier
            self.after(500, self._ensure_face_worker_running)
            self.after(500, self._ensure_theme_worker_running)

            if not self._scan_thread or not self._scan_thread.is_alive():
                self.after(100, self._load_data_to_treeview)
                self.after(200, self._update_statistics)
        except Exception as e:
            logger.error("[watcher] Erreur sync %s: %s", path.name, e)

    def _sync_deletion_from_watcher(self, path: Path) -> None:
        """Supprime une photo de la base si elle est effacée du disque."""
        if not self.db: return
        try:
            rel_p = self._rel_path(path)
            self.db.remove_photo_by_path(rel_p)
            logger.info("[watcher] Supprimé de la base: %s", path.name)
            if not self._scan_thread or not self._scan_thread.is_alive():
                self.after(500, self._load_data_to_treeview)
                self.after(600, self._update_statistics)
        except Exception as e:
            logger.error("[watcher] Erreur suppression %s: %s", path.name, e)

    def _cancel_pending_startup_load(self) -> int:
        self._startup_load_token += 1
        self._startup_tree_loading = False
        return self._startup_load_token

    def _get_treeview_db_sort_column(self) -> str:
        db_col = self.__dict__.get("_sort_col", "date_taken")
        if db_col == "filesize_str":
            return "filesize"
        if db_col == "camera":
            return "camera_model"
        if db_col == "copied":
            return "is_copied"
        if db_col == "directory_name":
            return "filepath"
        if db_col == "gps_coords":
            return "latitude"
        return str(db_col or "date_taken")

    @staticmethod
    def _build_source_stats_snapshot(rows: list[dict[str, Any]], face_stats: dict[str, int]) -> dict[str, Any]:
        total = len(rows)
        if total <= 0:
            return {
                "photos": "0",
                "dirs": "0",
                "size_total": "0 B",
                "size_avg": "0 B",
                "localized": "0.0 %",
                "geocoded": "0.0 %",
                "copied": "0.0 %",
                "cameras": "0",
                "top_camera": "-",
                "date_range": "-",
                "duplicate_health": "Non calcule (audit ou mode Doublons)",
                "faces_health": "0 / 0 / 0 / 0",
                "album_health": "N/A (mode Source)",
                "copied_count": 0,
            }

        total_size = sum(int(row.get("filesize") or 0) for row in rows)
        avg_size = int(total_size / total) if total else 0

        # On calcule les dossiers en restant robuste aux chemins
        dirs = set()
        for row in rows:
            fp = row.get("filepath")
            if fp:
                try:
                    dirs.add(str(Path(str(fp)).parent))
                except Exception:
                    pass

        # Diag debug: vérification des types de chemins
        abs_count = sum(1 for row in rows if Path(str(row.get("filepath") or "")).is_absolute())
        logger.info("[stats-debug] Total=%d, AbsPaths=%d", total, abs_count)
        if total > 0 and abs_count == total:
            logger.warning("[stats-debug] ALERTE: Toutes les photos sont en chemin ABSOLU.")
                    
        gps_count = sum(1 for row in rows if row.get("latitude") is not None and row.get("longitude") is not None)
        geocoded_count = sum(1 for row in rows if (row.get("country") or row.get("region") or row.get("city")))
        copied_count = sum(1 for row in rows if int(row.get("is_copied") or 0) == 1)

        camera_values = [str(row.get("camera_model") or "").strip() for row in rows]
        camera_values = [value for value in camera_values if value]
        camera_counter = Counter(camera_values)
        top_camera = camera_counter.most_common(1)[0][0] if camera_counter else "-"

        dates = [str(row.get("date_taken") or "").strip() for row in rows if str(row.get("date_taken") or "").strip()]
        if dates:
            date_from = min(dates)
            date_to = max(dates)
            date_range = f"{date_from}  ->  {date_to}" if date_from != date_to else date_from
        else:
            date_range = "-"

        return {
            "photos": str(total),
            "dirs": str(len(dirs)),
            "size_total": format_size(total_size),
            "size_avg": format_size(avg_size),
            "localized": f"{(gps_count * 100 / total):.1f} % ({gps_count}/{total})",
            "geocoded": f"{(geocoded_count * 100 / total):.1f} % ({geocoded_count}/{total})",
            "copied": f"{(copied_count * 100 / total):.1f} % ({copied_count}/{total})",
            "cameras": str(len(camera_counter)),
            "top_camera": top_camera,
            "date_range": date_range,
            "duplicate_health": "Non calcule (audit ou mode Doublons)",
            "faces_health": (
                f"{int(face_stats.get('processed') or 0)} / "
                f"{int(face_stats.get('photos_with_faces') or 0)} / "
                f"{int(face_stats.get('faces_total') or 0)} / "
                f"{int(face_stats.get('people_total') or 0)}"
            ),
            "album_health": "N/A (mode Source)",
            "copied_count": copied_count,
        }

    def _apply_source_stats_snapshot(self, stats_payload: dict[str, Any]) -> None:
        if not hasattr(self, "_stats_vars"):
            return

        for key in (
            "photos",
            "dirs",
            "size_total",
            "size_avg",
            "localized",
            "geocoded",
            "copied",
            "cameras",
            "top_camera",
            "date_range",
            "duplicate_health",
            "faces_health",
            "album_health",
        ):
            if key in self._stats_vars and key in stats_payload:
                self._stats_vars[key].set(str(stats_payload.get(key) or ""))

        self._update_face_engine_perf_stats_lines()
        if "face_runtime_status" in self._stats_vars:
            self._stats_vars["face_runtime_status"].set(str(getattr(self, "_face_runtime_status", "Désactivée") or "Désactivée"))
        if "theme_runtime_status" in self._stats_vars:
            self._stats_vars["theme_runtime_status"].set(str(getattr(self, "_theme_runtime_status", "Inactif") or "Inactif"))
        if "theme_last_summary" in self._stats_vars:
            self._stats_vars["theme_last_summary"].set(str(getattr(self, "_theme_last_summary", "-") or "-"))
        if "last_integrity_audit" in self._stats_vars:
            self._stats_vars["last_integrity_audit"].set(str(self.config_data.get("stats_last_integrity_audit") or "-"))
        if hasattr(self, "var_copies"):
            self.var_copies.set(f"Exportées : {int(stats_payload.get('copied_count') or 0)}")

    def _schedule_delayed_startup_services(self, token: int) -> None:
        def _run() -> None:
            if token != self._startup_load_token:
                return
            self._startup_hydration_complete = True
            self._ensure_face_worker_running()
            self._start_faces_background_preload_scheduler()
            self._render_map()
            if self._current_source_dir is not None and (self._startup_cleanup_thread is None or not self._startup_cleanup_thread.is_alive()):
                self._startup_cleanup_thread = threading.Thread(
                    target=self._deferred_startup_cleanup_worker,
                    args=(self._current_source_dir, token),
                    daemon=True,
                )
                self._startup_cleanup_thread.start()

        self.after(1200, _run)

    def _collect_db_cleanup_ids(
        self,
        rows: list[dict[str, Any]],
        source_root: Path | None,
    ) -> tuple[list[int], list[int]]:
        seen: dict[str, int] = {}
        missing_ids: list[int] = []
        duplicate_ids: list[int] = []

        for row in rows:
            photo_id = int(row["id"])
            raw_path = str(row.get("filepath") or "").strip()
            if not raw_path:
                missing_ids.append(photo_id)
                continue

            # Utilisation du chemin absolu pour la vérification d'existence
            abs_p = self._abs_path(raw_path)
            if self._is_path_excluded_from_source_scan(abs_p, source_root):
                missing_ids.append(photo_id)
                continue
                
            if not abs_p.exists():
                missing_ids.append(photo_id)
                continue

            try:
                canonical = os.path.normcase(str(abs_p.resolve()))
            except Exception:
                canonical = os.path.normcase(str(abs_p))

            if canonical in seen:
                duplicate_ids.append(photo_id)
            else:
                seen[canonical] = photo_id

        return (missing_ids, duplicate_ids)

    def _deferred_startup_cleanup_worker(self, source_root: Path, token: int) -> None:
        deleted = 0
        missing_count = 0
        duplicate_count = 0
        error_text = ""
        loader_db: Database | None = None

        try:
            loader_db = Database(self._get_db_file_for_source(source_root))
            rows = loader_db.get_all_photos(order_by="id")
            missing_ids, duplicate_ids = self._collect_db_cleanup_ids(rows, source_root)
            missing_count = len(missing_ids)
            duplicate_count = len(duplicate_ids)

            for photo_id in missing_ids + duplicate_ids:
                loader_db.delete_photo(photo_id)
                deleted += 1
        except Exception as exc:
            error_text = str(exc)
            logger.exception("[startup] Nettoyage source differe impossible: %s", exc)
        finally:
            if loader_db is not None:
                try:
                    loader_db.close()
                except Exception:
                    pass

        self.after(
            0,
            lambda: self._on_deferred_startup_cleanup_done(token, deleted, missing_count, duplicate_count, error_text),
        )

    def _on_deferred_startup_cleanup_done(
        self,
        token: int,
        deleted: int,
        missing_count: int,
        duplicate_count: int,
        error_text: str,
    ) -> None:
        self._startup_cleanup_thread = None
        if token != self._startup_load_token:
            return
        if error_text:
            logger.warning("[startup] Nettoyage source differe ignore: %s", error_text)
            return
        if deleted <= 0:
            return

        logger.info(
            "Auto-nettoyage DB differe: %d entree(s) supprimee(s) (absentes=%d, doublons=%d).",
            deleted,
            missing_count,
            duplicate_count,
        )
        self._invalidate_duplicate_cache()
        if not self._startup_tree_loading and not (self._scan_thread and self._scan_thread.is_alive()):
            self._load_data_to_treeview()
            self._update_copies_counter()
            self._update_statistics()

    def _startup_snapshot_worker(self, db_file: Path, token: int) -> None:
        rows: list[dict[str, Any]] = []
        stats_payload: dict[str, Any] = {}
        error_text = ""
        loader_db: Database | None = None

        try:
            loader_db = Database(db_file)
            if loader_db.has_missing_collection_names():
                logger.info("[startup] Mise a jour differee des noms de collection.")
                loader_db.update_collection_names()

            rows = loader_db.get_all_photos(
                order_by=self._get_treeview_db_sort_column(),
                asc=bool(self.__dict__.get("_sort_asc", True)),
            )
            stats_payload = self._build_source_stats_snapshot(rows, loader_db.get_face_stats())
            logger.info("[startup] Snapshot charge: %d photos.", len(rows))
        except Exception as exc:
            error_text = str(exc)
            logger.exception("[startup] Echec du chargement differe: %s", exc)
        finally:
            if loader_db is not None:
                try:
                    loader_db.close()
                except Exception:
                    pass

        # Forcer la conversion des chemins absolus en relatifs si possible (Portabilité)
        if rows and self._current_source_dir:
            needs_update = []
            for r in rows:
                p_raw = str(r.get("filepath") or "")
                if p_raw and Path(p_raw).is_absolute():
                    rel = self._rel_path(p_raw)
                    if rel != p_raw:
                        needs_update.append((rel, int(r["id"])))
            
            if needs_update:
                logger.info("[startup] Migration de %d chemins absolus vers relatifs pour portabilité.", len(needs_update))
                try:
                    updater_db = Database(db_file)
                    updater_db.update_filepaths_batch(needs_update)
                    updater_db.close()
                except Exception as e:
                    logger.warning("[startup] Erreur conversion chemins relatifs : %s", e)

        self.after(
            0,
            lambda: self._on_startup_snapshot_ready(token, rows, stats_payload, error_text),
        )

    def _on_startup_snapshot_ready(
        self,
        token: int,
        rows: list[dict[str, Any]],
        stats_payload: dict[str, Any],
        error_text: str,
    ) -> None:
        if token != self._startup_load_token:
            return

        if error_text:
            self.var_counter.set(f"Initialisation partielle: {error_text}")
            self._load_data_to_treeview()
            self._update_copies_counter()
            self._update_statistics()
            self._schedule_delayed_startup_services(self._startup_load_token)
            return

        # LOG DIAGNOSTIC
        logger.info("[startup] Snapshot ready: rows=%d payload_keys=%d error=%s", len(rows), len(stats_payload), error_text)
        if rows:
            logger.info("[startup] Sample row 0: ID=%s Path=%s", rows[0].get("id"), rows[0].get("filepath"))

        # On applique les statistiques IMMEDIATEMENT
        self._apply_source_stats_snapshot(stats_payload)
        self._set_startup_counter_message(len(rows))
        
        # Reset force des scopes pour éviter de rester bloqué sur une vue vide
        if not self._is_album_scope_active() and not self._is_duplicates_scope_active() and not self._is_faces_scope_active():
             if hasattr(self, "var_viewer_scope"):
                 self.var_viewer_scope.set("folder")
             if hasattr(self, "var_search"):
                 self.var_search.set("")

        self.tree.delete(*self.tree.get_children())
        self._album_virtual_rows = {}
        self._startup_tree_loading = True

        context: dict[str, Any] = {
            "rows": rows,
            "available_countries": set(),
            "map_points_all": [],
            "stats_payload": stats_payload,
            "matched_iid": "",
            "target_path": self._pending_restore_view_path.strip(),
        }
        self.var_counter.set(f"Chargement initial des photos : 0/{len(rows)}")
        self.after(0, lambda: self._continue_startup_tree_load(token, 0, context))

    def _continue_startup_tree_load(self, token: int, start_index: int, context: dict[str, Any]) -> None:
        if token != self._startup_load_token or not self._startup_tree_loading:
            return

        rows = context.get("rows") or []
        total = len(rows)
        if total <= 0:
            self._startup_tree_loading = False
            self._apply_source_stats_snapshot(dict(context.get("stats_payload") or {}))
            self._set_startup_counter_message(0)
            self._schedule_delayed_startup_services(token)
            return

        end_index = min(start_index + 250, total)
        available_countries = context.setdefault("available_countries", set())
        map_points_all = context.setdefault("map_points_all", [])
        target_path = str(context.get("target_path") or "").strip()

        for row in rows[start_index:end_index]:
            country = str(row.get("country") or "").strip()
            if country:
                available_countries.add(country)

            filepath_raw = str(row.get("filepath") or "")
            if target_path and not context.get("matched_iid") and filepath_raw == target_path:
                context["matched_iid"] = str(row.get("id") or "")

            lat = row.get("latitude")
            lon = row.get("longitude")
            if lat is not None and lon is not None:
                map_points_all.append((int(row["id"]), float(lat), float(lon)))

            try:
                values = self._build_tree_values(row, album_virtual=False)
                has_gps = row.get("latitude") is not None and row.get("longitude") is not None
                self.tree.insert("", "end", iid=str(row["id"]), values=values, tags=("gps_link",) if has_gps else ())
            except Exception as e:
                logger.debug("[startup] Erreur insertion ligne treeview (id=%s): %s", row.get("id"), e)
                continue

        if end_index < total:
            self.var_counter.set(f"Chargement initial des photos : {end_index}/{total}")
            self.after(1, lambda: self._continue_startup_tree_load(token, end_index, context))
            return

        self._startup_tree_loading = False
        self._set_map_points(list(map_points_all))

        matched_iid = str(context.get("matched_iid") or "")
        if matched_iid and self.tree.exists(matched_iid):
            self.tree.selection_set(matched_iid)
            self.tree.focus(matched_iid)
            self.tree.see(matched_iid)
            self._pending_restore_view_path = ""
        else:
            self._pending_restore_view_path = ""
            self._try_display_first()

        self._set_country_quick_filters(list(available_countries))
        self._apply_source_stats_snapshot(dict(context.get("stats_payload") or {}))
        self._refresh_contextual_counter(force=True, startup_photo_count=total)
        self._refresh_sidebar()
        self._schedule_delayed_startup_services(token)

    def _get_current_tree_position(self) -> tuple[int, int]:
        """Retourne (position, total_visible) dans le tableau affiché."""
        if not hasattr(self, "tree"):
            return (0, 0)

        children = list(self.tree.get_children())
        total = len(children)
        if total <= 0:
            return (0, 0)

        current_iid = ""
        selection = self.tree.selection()
        if selection:
            current_iid = str(selection[0])
        elif self.tree.focus():
            current_iid = str(self.tree.focus())

        if not current_iid:
            return (0, total)

        try:
            return (children.index(current_iid) + 1, total)
        except ValueError:
            return (0, total)

    def _get_current_scope_for_status(self) -> str:
        return str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))

    def _get_current_mode_for_status(self) -> str:
        return str(self.var_viewer_mode.get() if hasattr(self, "var_viewer_mode") else self.config_data.get("viewer_mode", "photo"))

    def _has_active_table_filter(self) -> bool:
        search_term = str(self.var_search.get() or "").strip() if hasattr(self, "var_search") else ""
        return bool(search_term) or (self._map_filter_bbox is not None)

    def _is_counter_busy_message(self, text: str) -> bool:
        busy_prefixes = (
            "Initialisation en cours",
            "Mise à jour des noms de collection",
            "Préparation doublons:",
            "Audit intégrité",
            "Normalisation",
            "Interruption demandée",
            "Visionneuse Mosaïc: chargement",
        )
        content = str(text or "").strip()
        return any(content.startswith(prefix) for prefix in busy_prefixes)

    def _get_scope_label_for_status(self, scope: str) -> str:
        labels = {
            "folder": "Dossier",
            "continuous": "Continu",
            "album": "Sélection",
            "virtual_album": "Albums",
            "duplicates": "Doublons",
            "faces": "Visages",
        }
        return labels.get(scope, "Tableau")

    def _get_virtual_album_sidebar_label(self, row: dict[str, Any]) -> str:
        name = str(row.get("name") or "").strip() or "Album sans nom"
        photo_count = int(row.get("photo_count") or 0)
        return f"{name} ({photo_count})"

    def _get_active_virtual_album(self) -> dict[str, Any] | None:
        if not self.db:
            return None

        if self._active_virtual_album_id is not None:
            album = self.db.get_virtual_album(int(self._active_virtual_album_id))
            if album is not None:
                self._active_virtual_album_name = str(album.get("name") or "").strip()
                return album

        if self._active_virtual_album_name:
            album = self.db.get_virtual_album_by_name(self._active_virtual_album_name)
            if album is not None:
                self._active_virtual_album_id = int(album.get("id") or 0) or None
                self._active_virtual_album_name = str(album.get("name") or "").strip()
                return album
        return None

    def _focus_active_virtual_album_in_sidebar(self) -> None:
        if not hasattr(self, "list_virtual_albums"):
            return

        rows = list(getattr(self, "_virtual_album_sidebar_rows", []))
        listbox = self.list_virtual_albums
        active_id = int(self._active_virtual_album_id or 0)

        self._virtual_album_listbox_syncing = True
        try:
            listbox.selection_clear(0, tk.END)
            if not rows or active_id <= 0:
                return
            for index, row in enumerate(rows):
                if int(row.get("id") or 0) != active_id:
                    continue
                listbox.selection_set(index)
                listbox.activate(index)
                listbox.see(index)
                return
        finally:
            self._virtual_album_listbox_syncing = False

    def _focus_active_face_person_in_sidebar(self) -> None:
        """Restaure visuellement la sélection de la personne active dans la sidebar."""
        if not hasattr(self, "list_virtual_albums"):
            return

        rows = list(getattr(self, "_virtual_album_sidebar_rows", []))
        listbox = self.list_virtual_albums
        active_id = int(self._active_face_person_id or 0)

        self._virtual_album_listbox_syncing = True
        try:
            if not rows or active_id <= 0:
                listbox.selection_clear(0, tk.END)
                return
            
            # Vérifier si la sélection actuelle est déjà la bonne pour éviter les sauts
            current_sel = listbox.curselection()
            target_index = -1
            for index, row in enumerate(rows):
                if row.get("type") == "face" and int(row.get("id") or 0) == active_id:
                    target_index = index
                    break
            
            if target_index != -1:
                if not current_sel or int(current_sel[0]) != target_index:
                    listbox.selection_clear(0, tk.END)
                    listbox.selection_set(target_index)
                    listbox.activate(target_index)
                    listbox.see(target_index)
            else:
                # Evite qu'une sélection fantôme persiste après rebuild.
                listbox.selection_clear(0, tk.END)
        finally:
            self._virtual_album_listbox_syncing = False

    def _sync_faces_tab_sidebar_to_active_person(self) -> None:
        """Aligne la sélection Treeview de l'onglet Visages sur la personne active."""
        if not hasattr(self, "faces_sidebar_list"):
            return

        tree = self.faces_sidebar_list
        active_id = int(self._active_face_person_id or 0)
        active_name = ""
        if active_id > 0 and self.db:
            active_name = str(self.db.get_face_person_display_name(active_id) or "").strip()

        # Par défaut on nettoie toute sélection résiduelle.
        target_node = ""
        for node in tree.get_children(""):
            node_name = str(tree.item(node, "text") or "").strip()
            if active_name and node_name == active_name:
                target_node = str(node)
                break

        current_sel = tree.selection()
        if target_node:
            if not current_sel or str(current_sel[0]) != target_node:
                tree.selection_remove(*current_sel)
                tree.selection_set(target_node)
                tree.focus(target_node)
                tree.see(target_node)
            self._selected_face_target_person_name = active_name
        else:
            if current_sel:
                tree.selection_remove(*current_sel)
            if not active_name:
                self._selected_face_target_person_name = None

    def _refresh_sidebar(self, select_active: bool = True) -> None:
        """Rafraîchit le contenu de la barre latérale selon le mode (Albums ou Visages)."""
        if not hasattr(self, "list_virtual_albums") or not self.db:
            return
            
        scope = str(self.var_viewer_scope.get() or "folder")
        if scope == "faces":
            self._refresh_faces_sidebar(select_active=select_active)
        else:
            self._refresh_virtual_album_sidebar(select_active=select_active)

    def _refresh_faces_sidebar(self, select_active: bool = True) -> None:
        """Affiche la liste des visages reconnus dans la barre latérale."""
        if not self.db: return
        # Task 10: Ne pas afficher "Personne xxx"
        people = [p for p in self.db.get_all_people() if not re.match(r"^Personne\s+\d+$", str(p.get("display_name") or ""), re.I)]
        people.sort(key=lambda p: str(p.get("display_name") or "").lower())
        
        # On compare uniquement les IDs et les noms, pas les compteurs, pour éviter les sauts incessants
        current_structure = [(p.get("id"), p.get("display_name")) for p in people]
        if getattr(self, "_last_faces_sidebar_structure", None) == current_structure:
            # Structure inchangée : restaurer la sélection seulement si demandé explicitement
            if select_active:
                self._focus_active_face_person_in_sidebar()
            return
            
        self._last_faces_sidebar_structure = current_structure
        logger.info("[faces] Reconstruction de la sidebar (structure changée)")
        
        self.var_virtual_album_header.set(f"Visages ({len(people)})")
        self._virtual_album_listbox_syncing = True
        try:
            listbox = self.list_virtual_albums
            listbox.configure(state="normal")
            listbox.delete(0, tk.END)
            self._virtual_album_sidebar_rows = []
            
            for p in people:
                name = str(p.get("display_name") or "Sans nom")
                count = int(p.get("photo_count") or 0)
                listbox.insert(tk.END, f"{name} ({count})")
                self._virtual_album_sidebar_rows.append({"type": "face", "id": p["id"], "name": name})
                
            if people and select_active:
                self._focus_active_face_person_in_sidebar()
            if not people:
                listbox.configure(state="disabled")
        finally:
            self._virtual_album_listbox_syncing = False

    def _refresh_virtual_album_sidebar(self, select_active: bool = True) -> None:
        """Met à jour la liste des albums virtuels."""
        rows = self.db.list_virtual_albums() if self.db else []
        self._virtual_album_sidebar_rows = [{"type": "album", **r} for r in rows]

        if rows:
            active = self._get_active_virtual_album()
            self.config_data["active_virtual_album_name"] = self._active_virtual_album_name
            self.config_data["active_face_person_id"] = self._active_face_person_id
            if active is None:
                first = rows[0]
                self._active_virtual_album_id = int(first.get("id") or 0) or None
                self._active_virtual_album_name = str(first.get("name") or "").strip()
                self.config_data["last_virtual_album_name"] = self._active_virtual_album_name
        else:
            self._active_virtual_album_id = None
            self._active_virtual_album_name = ""
            self.config_data["last_virtual_album_name"] = ""

        if hasattr(self, "var_virtual_album_header"):
            self.var_virtual_album_header.set(f"Albums ({len(rows)})")

        if not hasattr(self, "list_virtual_albums"):
            return

        listbox = self.list_virtual_albums
        self._virtual_album_listbox_syncing = True
        try:
            listbox.configure(state="normal")
            listbox.delete(0, tk.END)
            for row in rows:
                listbox.insert(tk.END, self._get_virtual_album_sidebar_label(row))
            if rows:
                if select_active:
                    self._focus_active_virtual_album_in_sidebar()
            else:
                listbox.configure(state="disabled")
        finally:
            self._virtual_album_listbox_syncing = False

    def _set_active_virtual_album(
        self,
        *,
        album_id: int | None = None,
        album_name: str | None = None,
        refresh_sidebar: bool = True,
    ) -> dict[str, Any] | None:
        if not self.db:
            self._active_virtual_album_id = None
            self._active_virtual_album_name = ""
            if refresh_sidebar:
                self._refresh_virtual_album_sidebar(select_active=False)
            return None

        album: dict[str, Any] | None = None
        if album_id is not None and int(album_id or 0) > 0:
            album = self.db.get_virtual_album(int(album_id))
        elif album_name:
            album = self.db.get_virtual_album_by_name(str(album_name))

        if album is None:
            self._active_virtual_album_id = None
            self._active_virtual_album_name = str(album_name or "").strip()
            self.config_data["last_virtual_album_name"] = self._active_virtual_album_name
            if refresh_sidebar:
                self._refresh_virtual_album_sidebar(select_active=False)
            return None

        self._active_virtual_album_id = int(album.get("id") or 0) or None
        self._active_virtual_album_name = str(album.get("name") or "").strip()
        self.config_data["last_virtual_album_name"] = self._active_virtual_album_name
        if refresh_sidebar:
            self._refresh_virtual_album_sidebar(select_active=True)
        return album

    def _on_virtual_album_listbox_select(self, _event: tk.Event | None = None) -> None:
        if self._virtual_album_listbox_syncing or not hasattr(self, "list_virtual_albums"):
            return

        selection = self.list_virtual_albums.curselection()
        if not selection:
            return

        index = int(selection[0])
        rows = list(getattr(self, "_virtual_album_sidebar_rows", []))
        if index < 0 or index >= len(rows):
            return

        row = rows[index]
        if row.get("type") == "face":
            self._active_face_person_id = row["id"]
            self._active_virtual_album_id = None
            logger.info("[sync] Personne sélectionnée: %s (ID: %s)", row.get("name"), row["id"])
            if hasattr(self, "var_viewer_scope"):
                self.var_viewer_scope.set("faces")
        else:
            album = self._set_active_virtual_album(album_id=int(row.get("id") or 0), refresh_sidebar=False)
            if album is None:
                return
            self._active_face_person_id = None
            logger.info("[sync] Album sélectionné: %s", self._active_virtual_album_name)
            if hasattr(self, "var_viewer_scope"):
                self.var_viewer_scope.set("virtual_album")

        self._apply_active_selection(refresh_sidebar=False)

    def _on_virtual_album_list_mousewheel(self, event: tk.Event) -> str:
        if not hasattr(self, "list_virtual_albums"):
            return "break"

        step = 0
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            step = -1 if delta > 0 else 1
        else:
            num = int(getattr(event, "num", 0) or 0)
            if num == 4:
                step = -1
            elif num == 5:
                step = 1

        if step:
            self.list_virtual_albums.yview_scroll(step, "units")
        return "break"

    def _on_virtual_album_list_delete_request(self, _event: tk.Event | None = None) -> str:
        self._delete_selected_virtual_album()
        return "break"

    def _on_virtual_album_list_rename_request(self, _event: tk.Event | None = None) -> str:
        self._rename_selected_virtual_album()
        return "break"

    def _get_selected_virtual_album_sidebar_row(self) -> dict[str, Any] | None:
        rows = list(getattr(self, "_virtual_album_sidebar_rows", []))
        if not rows or not hasattr(self, "list_virtual_albums"):
            return None

        selection = self.list_virtual_albums.curselection()
        if selection:
            index = int(selection[0])
            if 0 <= index < len(rows):
                return rows[index]

        active_id = int(self._active_virtual_album_id or 0)
        if active_id > 0:
            for row in rows:
                if int(row.get("id") or 0) == active_id:
                    return row
        return None

    def _show_virtual_album_sidebar_ctx_menu(self, event: tk.Event) -> str:
        if not hasattr(self, "list_virtual_albums") or not hasattr(self, "_virtual_album_ctx_menu"):
            return "break"

        if self.list_virtual_albums.size() <= 0:
            return "break"

        index = int(self.list_virtual_albums.nearest(event.y))
        if index < 0 or index >= self.list_virtual_albums.size():
            return "break"

        self._virtual_album_listbox_syncing = True
        try:
            self.list_virtual_albums.selection_clear(0, tk.END)
            self.list_virtual_albums.selection_set(index)
            self.list_virtual_albums.activate(index)
            self.list_virtual_albums.see(index)
        finally:
            self._virtual_album_listbox_syncing = False

        try:
            self._virtual_album_ctx_menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            self._virtual_album_ctx_menu.grab_release()
        return "break"

    def _rename_selected_virtual_album(self) -> None:
        if not self.db:
            return

        row = self._get_selected_virtual_album_sidebar_row()
        if row is None:
            return

        current_name = str(row.get("name") or "").strip()
        new_name = simpledialog.askstring(
            "Renommer l'album",
            "Nouveau nom :",
            initialvalue=current_name,
            parent=self,
        )
        if new_name is None:
            return

        renamed = self.db.rename_virtual_album(int(row.get("id") or 0), new_name)
        if renamed is None:
            messagebox.showwarning("Renommer l'album", "Nom invalide ou déjà utilisé.")
            return

        self._set_active_virtual_album(album_id=int(renamed.get("id") or 0), refresh_sidebar=True)
        if self._is_virtual_album_scope_active():
            self._on_viewer_scope_changed()

    def _delete_selected_virtual_album(self) -> None:
        if not self.db:
            return

        row = self._get_selected_virtual_album_sidebar_row()
        if row is None:
            return

        album_id = int(row.get("id") or 0)
        album_name = str(row.get("name") or "").strip() or "Album"
        photo_count = int(row.get("photo_count") or 0)
        answer = messagebox.askyesno(
            "Supprimer l'album",
            f"Supprimer l'album virtuel '{album_name}' ?\n\n"
            f"Les {photo_count} photo(s) resteront sur disque et en base source.",
        )
        if not answer:
            return

        deleted = self.db.delete_virtual_album(album_id)
        if not deleted:
            messagebox.showerror("Supprimer l'album", "Suppression impossible.")
            return

        if int(self._active_virtual_album_id or 0) == album_id:
            self._active_virtual_album_id = None
            self._active_virtual_album_name = ""
        self._refresh_virtual_album_sidebar()

        if self._is_virtual_album_scope_active():
            if self._get_active_virtual_album() is None:
                self.var_viewer_scope.set("folder")
            self._on_viewer_scope_changed()

    def _choose_virtual_album_for_add(self, photo_count: int) -> dict[str, Any] | None:
        if not self.db:
            return None

        result: dict[str, Any] = {"album": None}
        dlg = tk.Toplevel(self)
        dlg.title("Ajouter à l'album")
        dlg.configure(bg="#f0f2f5")
        dlg.resizable(False, False)
        dlg.transient(self)

        tk.Label(
            dlg,
            text=f"Ajouter {photo_count} photo(s) à un album virtuel",
            bg="#f0f2f5",
            fg="#1f2937",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(14, 4))

        tk.Label(
            dlg,
            text="Tapez pour filtrer la liste. Si aucun album ne correspond, Entrée crée le nom saisi.",
            bg="#f0f2f5",
            fg="#475569",
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(0, 8))

        var_filter = tk.StringVar()
        entry_filter = tk.Entry(dlg, textvariable=var_filter, width=38)
        entry_filter.pack(fill=tk.X, padx=16)

        list_frame = tk.Frame(dlg, bg="#f0f2f5")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 6))
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        listbox = tk.Listbox(
            list_frame,
            width=38,
            height=12,
            exportselection=False,
            activestyle="none",
            bg="white",
            fg="#1f2937",
            selectbackground="#dbeafe",
            selectforeground="#0f172a",
        )
        listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scrollbar.set)

        hint_var = tk.StringVar(value="")
        tk.Label(
            dlg,
            textvariable=hint_var,
            bg="#f0f2f5",
            fg="#64748b",
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(0, 8))

        filtered_rows: list[dict[str, Any]] = []

        def _refresh_dialog_list() -> None:
            nonlocal filtered_rows
            source_rows = self.db.list_virtual_albums() if self.db else []
            term = _remove_accents(str(var_filter.get() or "").strip().lower())
            filtered_rows = []
            active_id = int(self._active_virtual_album_id or 0)

            listbox.delete(0, tk.END)
            for row in source_rows:
                name_norm = _remove_accents(str(row.get("name") or "").lower())
                if term and term not in name_norm:
                    continue
                filtered_rows.append(row)
                listbox.insert(tk.END, self._get_virtual_album_sidebar_label(row))

            if filtered_rows:
                selected_index = 0
                for index, row in enumerate(filtered_rows):
                    if int(row.get("id") or 0) == active_id:
                        selected_index = index
                        break
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(selected_index)
                listbox.activate(selected_index)
                listbox.see(selected_index)
                hint_var.set("Entrée, double-clic ou le bouton Ajouter utilisent l'album sélectionné.")
                return

            if str(var_filter.get() or "").strip():
                hint_var.set("Aucun album trouvé. Entrée créera un nouvel album avec ce nom.")
            else:
                hint_var.set("Saisissez un nom pour filtrer ou créer un nouvel album.")

        def _accept(_event: tk.Event | None = None) -> str:
            selected = listbox.curselection()
            if selected:
                index = int(selected[0])
                if 0 <= index < len(filtered_rows):
                    result["album"] = filtered_rows[index]
                    dlg.destroy()
                    return "break"

            typed_name = re.sub(r"\s+", " ", str(var_filter.get() or "").strip())
            if not typed_name:
                messagebox.showinfo("Ajouter à l'album", "Sélectionnez un album ou saisissez un nom.")
                return "break"

            created = self.db.create_virtual_album(typed_name) if self.db else None
            if created is None:
                messagebox.showwarning("Ajouter à l'album", "Création de l'album impossible.")
                return "break"

            result["album"] = created
            dlg.destroy()
            return "break"

        def _cancel() -> None:
            dlg.destroy()

        var_filter.trace_add("write", lambda *_args: _refresh_dialog_list())
        entry_filter.bind("<Return>", _accept)
        listbox.bind("<Double-Button-1>", _accept)
        listbox.bind("<Return>", _accept)

        btns = tk.Frame(dlg, bg="#f0f2f5")
        btns.pack(fill=tk.X, padx=16, pady=(0, 14))
        tk.Button(btns, text="Annuler", width=12, command=_cancel, bg="lightgray", fg="black").pack(side=tk.RIGHT)
        tk.Button(btns, text="Ajouter", width=12, command=_accept, bg="lightgray", fg="black").pack(side=tk.RIGHT, padx=(0, 8))

        _refresh_dialog_list()
        dlg.grab_set()
        self._center_dialog_on_main(dlg)
        entry_filter.focus_set()
        self.wait_window(dlg)
        return result.get("album")

    def _resolve_virtual_album_photo_ids(
        self,
        *,
        selection_iids: tuple[str, ...] | None = None,
        paths: list[Path] | None = None,
    ) -> tuple[list[int], int]:
        resolved_ids: list[int] = []
        unresolved = 0
        seen: set[int] = set()

        for item_id in selection_iids or ():
            try:
                photo_id = int(item_id)
            except Exception:
                unresolved += 1
                continue

            if photo_id <= 0:
                row = self._get_photo_by_id(photo_id)
                path = self._abs_path(str(row.get("filepath") or "")) if row and row.get("filepath") else None
                photo_id = int(self._get_id_from_path(path) or 0) if path is not None else 0

            if photo_id <= 0:
                unresolved += 1
                continue
            if photo_id in seen:
                continue
            seen.add(photo_id)
            resolved_ids.append(photo_id)

        for path in paths or []:
            if path is None:
                unresolved += 1
                continue
            photo_id = int(self._get_id_from_path(path) or 0)
            if photo_id <= 0:
                unresolved += 1
                continue
            if photo_id in seen:
                continue
            seen.add(photo_id)
            resolved_ids.append(photo_id)

        return resolved_ids, unresolved

    def _add_photo_ids_to_virtual_album(self, photo_ids: list[int], unresolved_count: int = 0) -> None:
        if not self.db:
            messagebox.showinfo("Ajouter à l'album", "Ouvrez d'abord une base source.")
            return

        unique_photo_ids = [int(photo_id) for photo_id in photo_ids if int(photo_id or 0) > 0]
        if not unique_photo_ids:
            messagebox.showinfo(
                "Ajouter à l'album",
                "Albums virtuels disponibles uniquement pour les photos de la base source active.",
            )
            return

        target_album = self._choose_virtual_album_for_add(len(unique_photo_ids))
        if target_album is None:
            return

        target_album_id = int(target_album.get("id") or 0)
        if target_album_id <= 0:
            return

        previously_active_album_id = int(self._active_virtual_album_id or 0)
        added, skipped = self.db.add_photos_to_virtual_album(target_album_id, unique_photo_ids)

        if previously_active_album_id <= 0:
            self._set_active_virtual_album(album_id=target_album_id, refresh_sidebar=True)
        else:
            self._refresh_virtual_album_sidebar()

        if self._is_virtual_album_scope_active() and (previously_active_album_id <= 0 or previously_active_album_id == target_album_id):
            if previously_active_album_id <= 0:
                self._set_active_virtual_album(album_id=target_album_id, refresh_sidebar=True)
            self._on_viewer_scope_changed()
        else:
            self._update_copies_counter()
            self._update_statistics()

        parts = [f"Album '{str(target_album.get('name') or '').strip() or 'Album'}' : {added} photo(s) ajoutée(s)."]
        if skipped > 0:
            parts.append(f"Déjà présentes: {skipped}.")
        if unresolved_count > 0:
            parts.append(f"Hors base source: {unresolved_count} ignorée(s).")
        
        self.var_counter.set(" ".join(parts))

    def _add_current_context_photo_to_virtual_album(self) -> None:
        target = self.viewer._ctx_target_path or self.viewer._current_path
        photo_ids, unresolved = self._resolve_virtual_album_photo_ids(paths=[target] if target is not None else [])
        self._add_photo_ids_to_virtual_album(photo_ids, unresolved_count=unresolved)

    def _add_logo_photo_to_virtual_album(self) -> None:
        target = self._orientation_ctx_path or self._logo_active_path
        photo_ids, unresolved = self._resolve_virtual_album_photo_ids(paths=[target] if target is not None else [])
        self._add_photo_ids_to_virtual_album(photo_ids, unresolved_count=unresolved)

    def _add_selected_table_photos_to_virtual_album(self) -> None:
        selection = tuple(self.tree.selection()) if hasattr(self, "tree") else tuple()
        photo_ids, unresolved = self._resolve_virtual_album_photo_ids(selection_iids=selection)
        self._add_photo_ids_to_virtual_album(photo_ids, unresolved_count=unresolved)

    def _remove_selected_from_current_virtual_album(self) -> None:
        if not self.db:
            return

        active_album = self._get_active_virtual_album()
        if active_album is None:
            return

        selection = self.tree.selection() if hasattr(self, "tree") else ()
        if not selection:
            return

        album_id = int(active_album.get("id") or 0)
        album_name = str(active_album.get("name") or "").strip() or "Album"
        count = len(selection)
        if count > 1:
            answer = messagebox.askyesno(
                "Retirer de l'album",
                f"Retirer {count} photo(s) de l'album '{album_name}' ?",
            )
            if not answer:
                return

        removed = 0
        for item_id in selection:
            try:
                photo_id = int(item_id)
            except Exception:
                continue
            if photo_id <= 0:
                continue
            if self.db.remove_photo_from_virtual_album(album_id, photo_id):
                removed += 1

        # Sauvegarder le prochain élément à sélectionner avant le refresh
        target_photo_id: int | None = None
        if selection:
            last_item = selection[-1]
            next_iid = self.tree.next(last_item) or self.tree.prev(selection[0])
            if next_iid:
                try:
                    target_photo_id = int(next_iid)
                except Exception:
                    pass

        self._refresh_virtual_album_sidebar()
        self._load_data_to_treeview()
        
        # Restaurer la sélection
        if target_photo_id is not None:
             target_iid = str(target_photo_id)
             if self.tree.exists(target_iid):
                 self.tree.selection_set(target_iid)
                 self.tree.focus(target_iid)
                 self.tree.see(target_iid)
        self._update_copies_counter()
        self._update_statistics()
        self.var_counter.set(f"Album '{album_name}' : {removed} photo(s) retirée(s).")

    def _get_folder_position_for_counter(self) -> tuple[int, int]:
        target_path: Path | None = None
        row = self._get_selected_tree_row() if hasattr(self, "tree") else None
        if row and row.get("filepath"):
            target_path = self._abs_path(str(row["filepath"]))
        elif self.viewer and self.viewer._current_path:
            target_path = self.viewer._current_path

        if not target_path or not target_path.exists():
            return (0, 0)

        try:
            folder_paths = sorted(
                [
                    p for p in target_path.parent.iterdir()
                    if p.is_file() and is_supported_image(p)
                ],
                key=lambda p: p.name.lower(),
            )
        except Exception:
            return (0, 0)

        total = len(folder_paths)
        if total <= 0:
            return (0, 0)

        try:
            position = folder_paths.index(target_path) + 1
        except ValueError:
            position = 0
        return (position, total)

    def _build_contextual_counter_message(self, startup_photo_count: int | None = None) -> str:
        scope = self._get_current_scope_for_status()
        mode = self._get_current_mode_for_status()
        has_filter = self._has_active_table_filter()

        if startup_photo_count is not None and scope == "continuous" and not has_filter:
            return f"Base de données chargée. ({int(startup_photo_count)} photos inscrites)"

        visible_position, visible_total = self._get_current_tree_position()
        if visible_total <= 0:
            return "Prêt. Sélectionnez les dossiers."

        mode_label = "Photo" if mode == "photo" else "Mosaïc"
        scope_label = self._get_scope_label_for_status(scope)

        if scope == "folder":
            folder_position, folder_total = self._get_folder_position_for_counter()
            if folder_total > 0:
                display_position = folder_position if folder_position > 0 else 1
                return f"Visionneuse {mode_label} | {scope_label}: {display_position}/{folder_total}"

        display_position = visible_position if visible_position > 0 else 1
        scope_total = self._get_scope_total_for_counter()
        if has_filter and scope_total > visible_total:
            return f"Visionneuse {mode_label} | {scope_label}: {display_position}/{visible_total} (filtré/{scope_total})"
        return f"Visionneuse {mode_label} | {scope_label}: {display_position}/{visible_total}"

    def _refresh_contextual_counter(self, force: bool = False, startup_photo_count: int | None = None) -> None:
        if not hasattr(self, "var_counter"):
            return

        if time.time() < float(getattr(self, "_counter_sticky_until", 0.0) or 0.0):
            return

        current = str(self.var_counter.get() or "")
        if not force and self._is_counter_busy_message(current):
            return

        message = self._build_contextual_counter_message(startup_photo_count=startup_photo_count)

        if self._counter_hover_source:
            self._counter_last_non_hover_text = message
            return

        self.var_counter.set(message)

    def _set_sticky_counter_message(self, message: str, ttl_sec: float = 8.0) -> None:
        if not hasattr(self, "var_counter"):
            return
        text = str(message or "").strip()
        if not text:
            return
        self._counter_sticky_until = time.time() + max(0.0, float(ttl_sec or 0.0))
        self.var_counter.set(text)

    def _set_ui_initializing_state(self, initializing: bool) -> None:
        """Active/désactive les contrôles interactifs pendant l'initialisation UI."""
        target_state = "disabled" if initializing else "normal"

        def _apply(widget: tk.Misc) -> None:
            children = widget.winfo_children() if hasattr(widget, "winfo_children") else []
            for child in children:
                _apply(child)

            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="disabled" if initializing else "readonly")
                    return
                if isinstance(widget, ttk.Treeview):
                    widget.state(["disabled"] if initializing else ["!disabled"])
                    return
                if isinstance(widget, (tk.Entry, tk.Button, tk.Radiobutton, tk.Checkbutton, tk.Spinbox, tk.Listbox)):
                    widget.configure(state=target_state)
                    return
                if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Entry, ttk.Spinbox)):
                    widget.configure(state=target_state)
                    return
            except Exception:
                return

        _apply(self)

    def _center_dialog_on_main(self, dlg: tk.Toplevel) -> None:
        """Centre une fenêtre fille sur la fenêtre principale."""
        try:
            dlg.update_idletasks()
            w = max(dlg.winfo_width(), dlg.winfo_reqwidth())
            h = max(dlg.winfo_height(), dlg.winfo_reqheight())
            x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
            dlg.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _load_config(self) -> None:
        defaults = {
            "last_in": "",
            "last_out": "",
            "last_virtual_album_name": "",
            "geometry_main": "",
            "state_main": "normal",
            "geometry_viewer": "",
            "state_viewer_fullscreen": True,
            "geo_mode": "local",
            "rename_protect_original_names": True,
            "viewer_mode": "photo",
            "viewer_scope": "folder",
            "slideshow_interval_sec": 3,
            "last_viewed_source": "",
            "last_viewed_path": "",
            "show_startup_layout_info": True,
            "debug_validate_tree_columns": False,
            "face_recognition_enabled": False,
            "face_engine_backend": "face_recognition",
            "face_match_threshold": 0.48,
            "last_faces_out": "",
            "audit_check_missing": True,
            "audit_check_duplicate_paths": True,
            "audit_check_signature_duplicates": True,
            "audit_check_exif_to_db": True,
            "audit_check_db_to_exif": False,
            "audit_check_gps_without_place": True,
            "audit_check_hidden_dirs": False,
            "audit_check_unreadable_viewer": True,
            "audit_auto_fix_defects": True,
            "audit_reconcile_remove_missing": True,
            "audit_reconcile_remove_duplicates": True,
            "audit_reconcile_sync_db_from_exif": True,
            "audit_reconcile_sync_exif_from_db": False,
            "audit_reconcile_fill_geo_from_local": True,
            "audit_reconcile_fill_geo_exif_write": False,
            "audit_reconcile_remove_hidden_dirs": False,
            "audit_reconcile_copy_unreadable": True,
            "audit_reconcile_trash_unreadable": True,
            "stats_last_integrity_audit": "-",
            "theme_classify_threshold": 0.55,
            "auto_sync_enabled": True,
        }
        self.config_data = defaults.copy()
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.config_data.update(loaded)
            except Exception: pass

    @staticmethod
    def _normalize_saved_path(path_str: str) -> str:
        candidate = str(path_str or "").strip()
        if not candidate:
            return ""
        try:
            return str(Path(candidate).resolve())
        except Exception:
            return candidate

    def _init_path_registry(self) -> None:
        if not PATH_REGISTRY_DB_FILE:
            return
        ensure_directory(PATH_REGISTRY_DB_FILE.parent)
        with sqlite3.connect(PATH_REGISTRY_DB_FILE) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_paths (
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    PRIMARY KEY(kind, path)
                )
                """
            )

    def _load_saved_path_lists(self) -> None:
        self._init_path_registry()

        sources: list[str] = []
        outputs: list[str] = []
        faces: list[str] = []
        to_delete: list[tuple[str, str]] = []

        with sqlite3.connect(PATH_REGISTRY_DB_FILE) as conn:
            rows = conn.execute("SELECT kind, path FROM saved_paths").fetchall()
        
        for kind, path in rows:
            normalized = self._normalize_saved_path(path)
            if not normalized:
                to_delete.append((kind, path))
                continue
            
            # Vérification physique des répertoires enregistrés
            try:
                p = Path(normalized)
                if not p.exists() or not p.is_dir():
                    logger.info("[startup] Répertoire '%s' introuvable, retrait du registre : %s", kind, normalized)
                    to_delete.append((kind, path))
                    continue
            except Exception:
                to_delete.append((kind, path))
                continue

            if kind == "source":
                sources.append(normalized)
            elif kind == "album_output":
                outputs.append(normalized)
            elif kind == "faces_output":
                faces.append(normalized)

        if to_delete:
            with sqlite3.connect(PATH_REGISTRY_DB_FILE) as conn:
                for k, p in to_delete:
                    conn.execute("DELETE FROM saved_paths WHERE kind = ? AND path = ?", (k, p))
            logger.info("[startup] %d entrées purgées du registre des chemins (répertoires inexistants).", len(to_delete))

        cfg_source = self._normalize_saved_path(str(self.config_data.get("last_in") or ""))
        cfg_output = self._normalize_saved_path(str(self.config_data.get("last_out") or ""))
        cfg_faces = self._normalize_saved_path(str(self.config_data.get("last_faces_out") or ""))
        
        config_changed = False
        if cfg_source:
            if Path(cfg_source).exists():
                sources.append(cfg_source)
                if hasattr(self, "var_in"):
                    self.var_in.set(cfg_source)
            else:
                logger.info("[startup] Répertoire source de la config introuvable : %s", cfg_source)
                self.config_data["last_in"] = ""
                if hasattr(self, "var_in"): self.var_in.set("")
                config_changed = True

        if cfg_output:
            if Path(cfg_output).exists():
                outputs.append(cfg_output)
            else:
                self.config_data["last_out"] = ""
                config_changed = True

        if cfg_faces:
            if Path(cfg_faces).exists():
                faces.append(cfg_faces)
            else:
                self.config_data["last_faces_out"] = ""
                config_changed = True

        if config_changed:
            self._save_config()

        self._saved_source_paths = sorted(set(sources), key=str.casefold)
        self._saved_album_output_paths = sorted(set(outputs), key=str.casefold)
        self._saved_faces_output_paths = sorted(set(faces), key=str.casefold)

        # Persiste les chemins issus de config (s'ils existent) pour les migrations
        if cfg_source and Path(cfg_source).exists():
            self._add_saved_path("source", cfg_source)
        if cfg_output and Path(cfg_output).exists():
            self._add_saved_path("album_output", cfg_output)
        if cfg_faces and Path(cfg_faces).exists():
            self._add_saved_path("faces_output", cfg_faces)

    def _refresh_path_selectors(self) -> None:
        if hasattr(self, "selector_in"):
            self.selector_in.configure(values=self._saved_source_paths)
        if hasattr(self, "selector_out"):
            self.selector_out.configure(values=self._saved_album_output_paths)
        if hasattr(self, "selector_faces"):
            self.selector_faces.configure(values=self._saved_faces_output_paths)

    def _add_saved_path(self, kind: str, path_str: str) -> str:
        normalized = self._normalize_saved_path(path_str)
        if not normalized:
            return ""

        self._init_path_registry()
        with sqlite3.connect(PATH_REGISTRY_DB_FILE) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO saved_paths(kind, path) VALUES (?, ?)",
                (kind, normalized),
            )

        if kind == "source":
            if normalized not in self._saved_source_paths:
                self._saved_source_paths.append(normalized)
                self._saved_source_paths.sort(key=str.casefold)
        elif kind == "album_output":
            if normalized not in self._saved_album_output_paths:
                self._saved_album_output_paths.append(normalized)
                self._saved_album_output_paths.sort(key=str.casefold)
        elif kind == "faces_output":
            if normalized not in self._saved_faces_output_paths:
                self._saved_faces_output_paths.append(normalized)
                self._saved_faces_output_paths.sort(key=str.casefold)
        self._refresh_path_selectors()
        return normalized

    def _on_faces_selector_changed(self, _event: tk.Event | None = None) -> None:
        faces_root = self._normalize_saved_path(self.var_faces.get())
        if not faces_root:
            return
        self.var_faces.set(faces_root)
        self.config_data["last_faces_out"] = faces_root
        self._add_saved_path("faces_output", faces_root)
        self._save_config()
        self._request_faces_tab_refresh()


    def _init_project_paths(self, path_in: Path) -> None:
        """Initialise les chemins de données internes pour un dossier source donné (Mode Portable)."""
        meta_dir = path_in / META_DIR_NAME
        ensure_directory(meta_dir)
        
        sel_dir = meta_dir / "Sélection"
        faces_dir = meta_dir / "Visages"
        doublons_dir = meta_dir / "Doublons"
        
        ensure_directory(sel_dir)
        ensure_directory(faces_dir)
        ensure_directory(doublons_dir)
        
        # Mise à jour des variables internes et bases projets
        self.var_out.set(str(sel_dir))
        self.var_faces.set(str(faces_dir))
        self.var_doublons.set(str(doublons_dir))
        self._album_index_db = meta_dir / "album_index.sqlite3"
        self._ops_log_file = meta_dir / "operations.csv"

    def _on_source_selector_changed(self, _event: tk.Event | None = None) -> None:
        source = self._normalize_saved_path(self.var_in.get())
        if not source:
            return
        self.var_in.set(source)
        self.config_data["last_in"] = source
        self._add_saved_path("source", source)
        self._save_config()

        path_in = Path(source)
        if not path_in.exists():
            return

        self._init_project_paths(path_in)

        changed = self._current_source_dir != path_in.resolve() if self._current_source_dir else True
        if changed:
            self._clear_table_display("Nouveau dossier source sélectionné (Mode Portable).")
        
        self._current_source_dir = path_in.resolve()
        self._init_db(path_in)
        
        if changed:
            self._start_background_count(path_in)
            
            # Réparer les échecs de reconnaissance faciale dus au changement de chemins (relativisation)
            if self.db:
                reset_count = self.db.reset_unsupported_face_states()
                if reset_count > 0:
                    logger.info("[faces] %d photos réinitialisées pour une nouvelle détection faciale.", reset_count)
        
        # Charger les données existantes dans le tableau
        self._load_data_to_treeview()

    def _on_output_selector_changed(self, _event: tk.Event | None = None) -> None:
        output = self._normalize_saved_path(self.var_out.get())
        if not output:
            return
        self.var_out.set(output)
        self.config_data["last_out"] = output
        self._add_saved_path("album_output", output)
        self._save_config()
        self._invalidate_duplicate_cache()
        output_path = Path(output)
        # Ne jamais imposer le mode Album ici: on respecte le mode choisi par l'utilisateur.
        # Si l'utilisateur est déjà en mode Album, on rafraîchit simplement la portée.
        if output_path.exists() and output_path.is_dir() and hasattr(self, "var_viewer_scope"):
            if str(self.var_viewer_scope.get() or "") == "album":
                self._on_viewer_scope_changed()
            return
        if str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder")) in ("album", "duplicates"):
            self._on_viewer_scope_changed()

    def _show_startup_layout_info_if_needed(self) -> None:
        """Affiche la fenêtre d'aide au placement des 3 fenêtres au démarrage."""
        if not bool(self.config_data.get("show_startup_layout_info", True)):
            return
        self._show_startup_layout_info_dialog()

    def _show_startup_layout_info_dialog(self) -> None:
        """Fenêtre onboarding pour expliquer la configuration multi-fenetres."""
        dlg = tk.Toplevel(self)
        dlg.title("Configuration des fenetres")
        dlg.configure(bg="#f2f4f8")
        dlg.transient(self)
        dlg.resizable(False, False)

        width, height = 760, 500
        try:
            x = self.winfo_rootx() + max(20, (self.winfo_width() - width) // 2)
            y = self.winfo_rooty() + max(20, (self.winfo_height() - height) // 3)
        except Exception:
            x, y = 120, 120
        dlg.geometry(f"{width}x{height}+{x}+{y}")

        dlg.attributes("-topmost", True)
        dlg.lift()
        dlg.focus_force()
        dlg.grab_set()
        dlg.after(900, lambda: dlg.attributes("-topmost", False))

        title_band = tk.Frame(dlg, bg="#0f3d5e", height=78)
        title_band.pack(fill=tk.X)
        title_band.pack_propagate(False)
        tk.Label(
            title_band,
            text="3 fenetres ont ete chargees",
            bg="#0f3d5e",
            fg="white",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(
            title_band,
            text="2 visionneuses + 1 tableau de bord",
            bg="#0f3d5e",
            fg="#c7e0f5",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=18)

        body = tk.Frame(dlg, bg="#f2f4f8", padx=18, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        pref_box = tk.Frame(body, bg="#e7edf5", bd=1, relief="solid")
        pref_box.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            pref_box,
            text="Preference de demarrage",
            bg="#e7edf5",
            fg="#0f3d5e",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 2))

        dont_show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            pref_box,
            text="Ne plus afficher cette fenetre au prochain lancement",
            variable=dont_show_var,
            bg="#e7edf5",
            activebackground="#e7edf5",
            fg="#1f2937",
            font=("Segoe UI", 10, "bold"),
            selectcolor="#ffffff",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        # Ajout des raccourcis multi-écrans demandés en gras
        tk.Label(
            body,
            text="Touche F1 : Aide et liste complète des raccourcis\n"
                 "Ctrl + 1 / Ctrl + 2 : Déplacer les visionneuses sur l'Écran 1 ou 2\n"
                 "Touche P : Basculer le tableau de bord au premier/arrière plan",
            bg="#f2f4f8",
            fg="#0f3d5e",
            font=("Segoe UI", 11, "bold"),
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            body,
            text="Commencez par sélectionner votre dossier photos",
            bg="#f2f4f8",
            fg="#1f2937",
            font=("Segoe UI", 10, "italic"),
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, pady=(10, 0))

        # Suppression des étapes 1 à 5 (non-grasses) demandée.
        
        footer = tk.Frame(dlg, bg="#f2f4f8", padx=18, pady=4)
        footer.pack(fill=tk.X, pady=(0, 14))

        btn_style = {
            "bg": "#e5e7eb",
            "fg": "#111827",
            "activebackground": "#d1d5db",
            "activeforeground": "#111827",
            "relief": "raised",
            "bd": 1,
            "width": 16,
        }

        def _close_dialog() -> None:
            self.config_data["show_startup_layout_info"] = not bool(dont_show_var.get())
            self._save_config()
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()

        tk.Button(footer, text="J'ai compris", command=_close_dialog, **btn_style).pack(side=tk.RIGHT)
        dlg.protocol("WM_DELETE_WINDOW", _close_dialog)

    def _set_style(self) -> None:
        """Applique une esthétique Premium (Light Mode) à l'application."""
        style = ttk.Style()
        # Theme 'clam' est une bonne base pour la personnalisation
        style.theme_use('clam')
        
        bg_main = "#f0f2f5"
        bg_secondary = "#ffffff"
        accent = "#3498db"
        text_color = "#2c3e50"
        
        self.configure(bg=bg_main)
        
        # Style général
        style.configure(".", background=bg_main, foreground=text_color, font=("Segoe UI", 10))
        
        # LabelFrames
        style.configure("TLabelframe", background=bg_main, foreground=accent, bordercolor="#cbd5e0")
        style.configure("TLabelframe.Label", background=bg_main, foreground=accent, font=("Segoe UI", 10, "bold"))
        
        # Treeview
        style.configure("Treeview", 
                        background=bg_secondary, 
                        foreground=text_color, 
                        fieldbackground=bg_secondary,
                        rowheight=28,
                        borderwidth=0)
        style.map("Treeview", background=[('selected', accent)], foreground=[('selected', 'white')])
        style.configure("Treeview.Heading", background="#e2e8f0", foreground=text_color, relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview.Heading", background=[('active', "#cbd5e0")])
        
        # Progressbar
        style.configure("TProgressbar", 
                        thickness=15, 
                        background=accent, 
                        troughcolor="#e2e8f0", 
                        borderwidth=1, 
                        bordercolor="#cbd5e0")
        
        # Buttons spéciaux (ceux qui ne sont pas en ttk)
        self.btn_colors = {"bg": "#ecf0f1", "fg": "#2c3e50", "activebackground": accent, "activeforeground": "white", "relief": "flat", "padx": 10, "pady": 5}

    def _handle_homothety_resize(self, event: tk.Event) -> None:
        """Ajuste le facteur de scaling en fonction de la largeur de la fenêtre."""
        if event.widget != self:
            return
            
        new_width = event.width
        # Éviter les calculs inutiles si la largeur n'a pas significativement changé
        if abs(new_width - self._last_scaled_width) < 5:
            return
            
        self._last_scaled_width = new_width
        ratio = new_width / self._scaling_base_width
        
        # On limite le ratio pour éviter des tailles extrêmes
        ratio = max(0.6, min(2.0, ratio))
        
        # Mise à jour du scaling global de Tk (affecte les polices en points)
        new_scaling = self._scaling_original_value * ratio
        self.tk.call('tk', 'scaling', new_scaling)
        
        # Mise à jour des éléments de style basés sur les pixels
        self._update_styles_scaling(ratio)

    def _update_styles_scaling(self, ratio: float) -> None:
        """Met à jour les styles TTK qui ne suivent pas automatiquement le scaling global."""
        style = ttk.Style()
        
        # Hauteur de ligne du Treeview (pixel-based)
        style.configure("Treeview", rowheight=int(28 * ratio))
        
        # Épaisseur de la barre de progression
        style.configure("TProgressbar", thickness=int(15 * ratio))
        
        # On peut aussi ajuster les polices explicitement si nécessaire, 
        # mais 'tk scaling' gère déjà la plupart des cas.
        # Note: Certains widgets Tk (non-ttk) peuvent nécessiter un rafraîchissement.

    def _save_config(self) -> None:
        try:
            if hasattr(self, 'var_auto_sync_enabled'):
                 self.config_data["auto_sync_enabled"] = bool(self.var_auto_sync_enabled.get())
            if hasattr(self, 'var_geo_mode'):
                self.config_data["geo_mode"] = self.var_geo_mode.get()
            if hasattr(self, 'var_viewer_mode'):
                self.config_data["viewer_mode"] = self.var_viewer_mode.get()
            if hasattr(self, 'var_viewer_scope'):
                self.config_data["viewer_scope"] = self.var_viewer_scope.get()
            self.config_data["active_face_person_id"] = self._active_face_person_id
            self.config_data["last_virtual_album_name"] = self._active_virtual_album_name
            if hasattr(self, 'var_faces_hide_named'):
                self.config_data["faces_hide_named"] = bool(self.var_faces_hide_named.get())
            if hasattr(self, 'var_face_recognition_enabled'):
                self.config_data["face_recognition_enabled"] = bool(self.var_face_recognition_enabled.get())
            if hasattr(self, 'var_face_engine'):
                self.config_data["face_engine_backend"] = normalize_face_backend(self.var_face_engine.get())
            if hasattr(self, 'var_face_match_threshold'):
                try:
                    threshold = float(self.var_face_match_threshold.get())
                except Exception:
                    threshold = 0.48
                self.config_data["face_match_threshold"] = max(0.00, min(0.95, threshold))
            if hasattr(self, 'var_faces'):
                self.config_data["last_faces_out"] = self._normalize_saved_path(self.var_faces.get())
            if hasattr(self, 'txt_user_notes'):
                self.config_data["user_notes"] = self.txt_user_notes.get("1.0", tk.END).strip()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f)
        except Exception: pass

    def _get_audit_function_preferences(self) -> dict[str, bool]:
        prefs = {
            "check_missing": bool(self.config_data.get("audit_check_missing", True)),
            "check_duplicate_paths": bool(self.config_data.get("audit_check_duplicate_paths", True)),
            "check_signature_duplicates": bool(self.config_data.get("audit_check_signature_duplicates", True)),
            "check_exif_to_db": bool(self.config_data.get("audit_check_exif_to_db", True)),
            "check_db_to_exif": bool(self.config_data.get("audit_check_db_to_exif", False)),
            "check_gps_without_place": bool(self.config_data.get("audit_check_gps_without_place", True)),
            "check_hidden_dirs": bool(self.config_data.get("audit_check_hidden_dirs", False)),
            "check_unreadable_viewer": bool(self.config_data.get("audit_check_unreadable_viewer", True)),
            "auto_fix_defects": bool(self.config_data.get("audit_auto_fix_defects", True)),
        }
        if prefs["check_exif_to_db"] and prefs["check_db_to_exif"]:
            prefs["check_db_to_exif"] = False
        return prefs

    def _save_audit_function_preferences(self, options: dict[str, bool]) -> None:
        self.config_data["audit_check_missing"] = bool(options.get("check_missing", True))
        self.config_data["audit_check_duplicate_paths"] = bool(options.get("check_duplicate_paths", True))
        self.config_data["audit_check_signature_duplicates"] = bool(options.get("check_signature_duplicates", True))
        self.config_data["audit_check_exif_to_db"] = bool(options.get("check_exif_to_db", True))
        self.config_data["audit_check_db_to_exif"] = bool(options.get("check_db_to_exif", False))
        self.config_data["audit_check_gps_without_place"] = bool(options.get("check_gps_without_place", True))
        self.config_data["audit_check_hidden_dirs"] = bool(options.get("check_hidden_dirs", False))
        self.config_data["audit_check_unreadable_viewer"] = bool(options.get("check_unreadable_viewer", True))
        self.config_data["audit_auto_fix_defects"] = bool(options.get("auto_fix_defects", True))
        if self.config_data["audit_check_exif_to_db"] and self.config_data["audit_check_db_to_exif"]:
            self.config_data["audit_check_db_to_exif"] = False
        self._save_config()

    def _set_last_integrity_audit_stats(self, summary: str) -> None:
        text = str(summary or "").strip() or "-"
        self.config_data["stats_last_integrity_audit"] = text
        self._save_config()
        if hasattr(self, "_stats_vars") and "last_integrity_audit" in self._stats_vars:
            self._stats_vars["last_integrity_audit"].set(text)

    def _set_theme_stats(self, runtime_status: str | None = None, summary: str | None = None) -> None:
        if runtime_status is not None:
            self._theme_runtime_status = str(runtime_status or "").strip() or "Inactif"
            self.config_data["stats_theme_runtime_status"] = self._theme_runtime_status
            if hasattr(self, "_stats_vars") and "theme_runtime_status" in self._stats_vars:
                self._stats_vars["theme_runtime_status"].set(self._theme_runtime_status)
            self._update_theme_status_indicator(self._theme_runtime_status)
        if summary is not None:
            self._theme_last_summary = str(summary or "").strip() or "-"
            self.config_data["stats_theme_last_summary"] = self._theme_last_summary
            if hasattr(self, "_stats_vars") and "theme_last_summary" in self._stats_vars:
                self._stats_vars["theme_last_summary"].set(self._theme_last_summary)
        self._save_config()

    def _get_audit_reconcile_preferences(self) -> dict[str, bool]:
        prefs = {
            "remove_missing": bool(self.config_data.get("audit_reconcile_remove_missing", True)),
            "remove_duplicates": bool(self.config_data.get("audit_reconcile_remove_duplicates", True)),
            "sync_db_from_exif": bool(self.config_data.get("audit_reconcile_sync_db_from_exif", True)),
            "sync_exif_from_db": bool(self.config_data.get("audit_reconcile_sync_exif_from_db", False)),
            "fill_geo_from_local": bool(self.config_data.get("audit_reconcile_fill_geo_from_local", True)),
            "fill_geo_exif_write": bool(self.config_data.get("audit_reconcile_fill_geo_exif_write", False)),
            "remove_hidden_dirs": bool(self.config_data.get("audit_reconcile_remove_hidden_dirs", False)),
            "copy_unreadable": bool(self.config_data.get("audit_reconcile_copy_unreadable", True)),
            "trash_unreadable": bool(self.config_data.get("audit_reconcile_trash_unreadable", True)),
        }
        if prefs["sync_db_from_exif"] and prefs["sync_exif_from_db"]:
            prefs["sync_exif_from_db"] = False
        return prefs

    def _save_audit_reconcile_preferences(self, options: dict[str, bool]) -> None:
        self.config_data["audit_reconcile_remove_missing"] = bool(options.get("remove_missing", True))
        self.config_data["audit_reconcile_remove_duplicates"] = bool(options.get("remove_duplicates", True))
        self.config_data["audit_reconcile_sync_db_from_exif"] = bool(options.get("sync_db_from_exif", True))
        self.config_data["audit_reconcile_sync_exif_from_db"] = bool(options.get("sync_exif_from_db", False))
        self.config_data["audit_reconcile_fill_geo_from_local"] = bool(options.get("fill_geo_from_local", True))
        self.config_data["audit_reconcile_fill_geo_exif_write"] = bool(options.get("fill_geo_exif_write", False))
        self.config_data["audit_reconcile_remove_hidden_dirs"] = bool(options.get("remove_hidden_dirs", False))
        self.config_data["audit_reconcile_copy_unreadable"] = bool(options.get("copy_unreadable", True))
        self.config_data["audit_reconcile_trash_unreadable"] = bool(options.get("trash_unreadable", True))
        if self.config_data["audit_reconcile_sync_db_from_exif"] and self.config_data["audit_reconcile_sync_exif_from_db"]:
            self.config_data["audit_reconcile_sync_exif_from_db"] = False
        self._save_config()

    def _on_viewer_mode_changed(self) -> None:
        """Applique immédiatement le mode Photo/Mosaïc via deux visionneuses synchronisées."""
        self._save_config()

        # Si le grid a été marqué périmé (changement de personne/scope en mode photo),
        # on l'invalide maintenant qu'on bascule en mosaïque.
        mode = str(self.var_viewer_mode.get() if hasattr(self, "var_viewer_mode") else self.config_data.get("viewer_mode", "photo"))
        if mode == "multiple" and getattr(self, "_grid_scope_dirty", False):
            self._grid_scope_dirty = False
            self.viewer_grid._grid_mode = False
            self.viewer_grid._grid_images = []
            self.viewer_grid._grid_index_to_path = {}
            self.viewer_grid._grid_last_render_size = None
            self.viewer_grid._clear_grid_preload_cache()

        target_path: Path | None = None
        row = self._get_selected_tree_row() if hasattr(self, "tree") else None
        if row and row.get("filepath"):
            target_path = self._abs_path(str(row["filepath"]))
        elif self.viewer._current_path:
            target_path = self.viewer._current_path

        target_path = self._resolve_target_path_for_scope(target_path)

        if target_path and target_path.exists():
            self._display_on_viewers(target_path, grab_focus=False)
        else:
            self._switch_active_viewer(str(self.var_viewer_mode.get() or "photo"), grab_focus=False)
        self._refresh_contextual_counter(force=True)

    def _get_scope_total_for_counter(self) -> int:
        """Retourne le total de référence du scope courant pour afficher filtré/total."""
        if self._is_album_scope_active():
            return len(self._build_album_virtual_rows())

        if self._is_virtual_album_scope_active():
            return len(self._build_virtual_album_rows())

        if not self.db:
            return 0

        if self._is_duplicates_scope_active():
            if not self._duplicate_cache_valid and not self._duplicate_prepare_in_progress:
                self._refresh_duplicate_cache()
            return len(self._duplicate_photo_ids) if self._duplicate_cache_valid else 0

        if self._is_faces_scope_active():
            return len(self.db.get_photo_ids_with_faces())

        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        if scope == "continuous":
            return int(self.db.get_photo_count())

        if scope == "folder":
            anchor_path: Path | None = None
            selected = self._get_selected_tree_row()
            if selected and selected.get("filepath"):
                anchor_path = Path(str(selected["filepath"]))
            elif self.viewer and self.viewer._current_path:
                anchor_path = self.viewer._current_path

            if anchor_path and anchor_path.exists():
                folder = anchor_path.parent
                rows = self.db.get_all_photos(order_by="id")
                return sum(
                    1
                    for r in rows
                    if r.get("filepath") and Path(str(r["filepath"])).parent == folder
                )

        return int(self.db.get_photo_count())

    def _mirror_viewer_geometry(self, source: FullscreenViewer, target: FullscreenViewer) -> None:
        """Aligne la géométrie et l'état plein écran entre deux visionneuses."""
        try:
            target.geometry(source.geometry())
        except Exception:
            pass

        try:
            if source._is_fullscreen and not target._is_fullscreen:
                target._go_fullscreen()
            elif not source._is_fullscreen and target._is_fullscreen:
                target._exit_fullscreen()
        except Exception:
            pass

    def _switch_active_viewer(self, mode: str, grab_focus: bool = False, sync_geometry: bool = True) -> None:
        """Bascule instantanément entre la fenêtre photo et la fenêtre mosaïque."""
        target = self.viewer_grid if mode == "multiple" else self.viewer_photo
        if self.viewer is target:
            try:
                target.deiconify()
                target.lift()
            except Exception:
                pass
            if grab_focus:
                try:
                    self.viewer.focus_force()
                except Exception:
                    pass
            return

        source = self.viewer
        logger.debug(
            "[viewer-debug] switch_viewer mode=%s sync_geometry=%s source=%s target=%s target_grid_mode=%s target_path=%s target_images=%d",
            mode,
            sync_geometry,
            "grid" if source is self.viewer_grid else "photo",
            "grid" if target is self.viewer_grid else "photo",
            target._grid_mode,
            target._current_path,
            len(getattr(target, "_grid_images", []) or []),
        )
        source._skip_next_redraw = True
        if target is self.viewer_grid and target._grid_mode and bool(target._grid_images):
            target._skip_next_grid_redraw = True
        if sync_geometry:
            self._mirror_viewer_geometry(source, target)
        try:
            source.withdraw()
        except Exception:
            pass
        if mode == "multiple":
            target._ignore_clicks_until = time.time() + 0.25
        target.deiconify()
        try:
            target.lift()
        except Exception:
            pass
        if grab_focus:
            target.focus_force()
        self.viewer = target

    def _display_on_viewers(
        self,
        target_path: Path,
        grab_focus: bool = False,
        allow_during_edit: bool = False,
    ) -> None:
        """Charge photo+mosaïque en parallèle (une visible, l'autre en arrière-plan)."""
        if not target_path.exists():
            return

        self._update_logo_preview(target_path)

        mode = str(self.var_viewer_mode.get() if hasattr(self, "var_viewer_mode") else self.config_data.get("viewer_mode", "photo"))
        logger.debug(
            "[viewer-debug] display_on_viewers mode=%s target=%s active=%s photo_ready=%s photo_path=%s grid_ready=%s grid_path=%s skip_hidden_once=%s",
            mode,
            target_path,
            "grid" if self.viewer is self.viewer_grid else "photo",
            not self.viewer_photo._grid_mode,
            self.viewer_photo._current_path,
            self.viewer_grid._grid_mode,
            self.viewer_grid._current_path,
            getattr(self, "_skip_hidden_grid_sync_once", False),
        )

        # Evite les rechargements inutiles quand photo + mode n'ont pas changé.
        if mode == "multiple":
            if self.viewer_grid._grid_mode and bool(self.viewer_grid._grid_images) and self.viewer_grid._grid_contains_path(target_path):
                logger.debug("[viewer-debug] display_on_viewers early_switch to existing grid page target=%s", target_path)
                self.viewer_grid._update_grid_selection_state(target_path)
                self._switch_active_viewer(mode, grab_focus=grab_focus, sync_geometry=False)
                return
        else:
            if (not self.viewer_photo._grid_mode) and self.viewer_photo._current_path == target_path:
                logger.debug("[viewer-debug] display_on_viewers early_switch to ready photo target=%s", target_path)
                self._switch_active_viewer(mode, grab_focus=grab_focus, sync_geometry=False)
                return

        if mode == "multiple":
            self._mirror_viewer_geometry(self.viewer, self.viewer_photo)
            self.viewer_photo.display_image(
                target_path,
                grab_focus=False,
                allow_during_edit=allow_during_edit,
                keep_hidden=True,
            )
            self._mirror_viewer_geometry(self.viewer, self.viewer_grid)
            self.viewer_grid.display_image(
                target_path,
                grab_focus=grab_focus,
                allow_during_edit=allow_during_edit,
                keep_hidden=False,
            )
        else:
            # Exigence métier: en mode photo, la mosaïque cachée n'est jamais resynchronisée.
            if getattr(self, "_skip_hidden_grid_sync_once", False):
                self._skip_hidden_grid_sync_once = False
            scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
            if scope == "faces":
                # En scope Visages, la sélection peut changer fortement en mode photo.
                # On force un rebuild propre de la mosaïque au prochain basculement.
                self._grid_scope_dirty = True
            logger.debug("[viewer-debug] display_on_viewers photo_branch target=%s hidden_grid_sync=disabled", target_path)
            self._mirror_viewer_geometry(self.viewer, self.viewer_photo)
            self.viewer_photo.display_image(
                target_path,
                grab_focus=grab_focus,
                allow_during_edit=allow_during_edit,
                keep_hidden=False,
            )

        self._switch_active_viewer(mode, grab_focus=grab_focus)

    def _update_logo_preview(self, active_path: Path | None) -> None:
        """Remplace le logo par une miniature de la photo active, sans déplacer la mise en page."""
        if not self._logo_label or not self._logo_slot_size:
            return

        if active_path is None or not active_path.exists() or not is_supported_image(active_path):
            self._logo_active_path = None
            if self._logo_default_photo is not None:
                self._logo_label.configure(image=self._logo_default_photo)
                self._logo_label.image = self._logo_default_photo
            return

        slot_w, slot_h = self._logo_slot_size
        try:
            with Image.open(active_path) as opened_img:
                img = ImageOps.exif_transpose(opened_img.copy())

            # Cadre fixe (même taille que le logo) avec centrage de la miniature.
            canvas = Image.new("RGB", (max(1, slot_w), max(1, slot_h)), (240, 242, 245))
            img.thumbnail((max(1, slot_w), max(1, slot_h)), Image.Resampling.LANCZOS)
            offset = ((slot_w - img.width) // 2, (slot_h - img.height) // 2)
            canvas.paste(img, offset)

            self._logo_active_photo = ImageTk.PhotoImage(canvas)
            self._logo_active_path = active_path
            self._logo_label.configure(image=self._logo_active_photo)
            self._logo_label.image = self._logo_active_photo
        except Exception:
            self._logo_active_path = None
            if self._logo_default_photo is not None:
                self._logo_label.configure(image=self._logo_default_photo)
                self._logo_label.image = self._logo_default_photo

    def _on_viewer_scope_changed(self, refresh_sidebar: bool = True) -> None:
        """Applique immédiatement la portée d'affichage (dossier/continu/album/doublons)."""
        scope = str(self.var_viewer_scope.get() or "folder")

        prev_scope = str(getattr(self, "_last_viewer_scope", scope) or scope)
        if hasattr(self, "var_search") and scope != prev_scope:
            current_search = str(self.var_search.get() or "").strip()
            injected = str(getattr(self, "_scope_injected_search_filter", "") or "").strip()
            # En quittant un mode, on efface uniquement le filtre injecté automatiquement.
            if bool(getattr(self, "_scope_injected_search_armed", False)) and injected and current_search == injected:
                self.var_search.set("")
                self._scope_injected_search_filter = ""
                self._scope_injected_search_armed = False

        self._set_album_scope_radio_enabled(scope == "album")

        target_for_scope: Path | None = None
        row_for_scope = self._get_selected_tree_row() if hasattr(self, "tree") else None
        if row_for_scope and row_for_scope.get("filepath"):
            target_for_scope = self._abs_path(str(row_for_scope["filepath"]))
        elif self.viewer._current_path:
            target_for_scope = self.viewer._current_path

        if scope == "folder":
            self._apply_directory_filter_for_path(target_for_scope)

        self._apply_active_selection(refresh_sidebar=refresh_sidebar)
        self._last_viewer_scope = scope

        if hasattr(self, "notebook"):
            try:
                # On ne force l'onglet Tableau que si on change de mode alors qu'on n'est pas déjà sur l'onglet Visages
                # Cela permet de rester sur la mosaïque si on change de personne en étant déjà dessus.
                if self.notebook.index("current") != self.notebook.index(self.tab_faces):
                    self.notebook.select(0)
            except Exception: pass
        self.update_idletasks()

    def _apply_active_selection(self, refresh_sidebar: bool = False) -> None:
        """Point de synchronisation unique pour la sélection (Album ou Visage)."""
        if not self.db: return

        # Guard contre la ré-entrée : _refresh_faces_tab → sidebar rebuild →
        # <<TreeviewSelect>> → _on_faces_sidebar_select → _apply_active_selection.
        # Sans ce guard, le cache grid serait détruit après sa reconstruction.
        if getattr(self, "_applying_active_selection", False):
            return
        self._applying_active_selection = True
        try:
            self._apply_active_selection_inner(refresh_sidebar)
        finally:
            self._applying_active_selection = False

    def _apply_active_selection_inner(self, refresh_sidebar: bool = False) -> None:
        """Implémentation interne de _apply_active_selection (protégée par le guard)."""
        scope = str(self.var_viewer_scope.get() or "folder")
        
        # 1. Synchronisation des noms/IDs pour la cohérence
        if scope == "faces" and self._active_face_person_id:
            self._sync_face_selection_from_active_person()
        elif (scope == "virtual_album" or scope == "album") and self._active_virtual_album_id:
            album = self.db.get_virtual_album(int(self._active_virtual_album_id))
            if album:
                self._active_virtual_album_name = str(album.get("name") or "").strip()
        
        # 2. Rafraîchissement de la sidebar si demandé
        if refresh_sidebar:
            self._refresh_sidebar()

        # 3. Mise à jour du tableau principal
        self._load_data_to_treeview()
        
        # 4. Signalement à la mosaïque qu'elle doit se mettre à jour
        if scope == "faces":
            self._request_faces_tab_refresh()
        
        # 5. Persistance et stats
        self._save_config()
        self._update_statistics()
        self.update_idletasks()

        # 6. Gestion des observateurs de dossiers (Albums/Doublons)
        if self._is_album_scope_active():
            self._map_filter_bbox = None
            self._ensure_album_watch_running()
        else:
            self._stop_album_watch()

        if self._is_duplicates_scope_active() and self.db:
            must_reindex = self.db.duplicate_index_needs_refresh()
            if must_reindex or not self._duplicate_cache_valid:
                self._prepare_duplicate_scope_async(reindex=must_reindex)
        self._update_copies_counter()
        self._update_statistics()

        mode = str(self.var_viewer_mode.get() if hasattr(self, "var_viewer_mode") else self.config_data.get("viewer_mode", "photo"))

        target_path: Path | None = None
        row = self._get_selected_tree_row() if hasattr(self, "tree") else None
        if row and row.get("filepath"):
            target_path = self._abs_path(str(row["filepath"]))
        elif self.viewer._current_path:
            target_path = self.viewer._current_path

        target_path = self._resolve_target_path_for_scope(target_path)

        # Invalider le cache mosaïque pour éviter le fast-path qui garderait l'ancienne portée.
        if mode == "multiple":
            # En mode mosaïque : invalidation immédiate + rebuild par _display_on_viewers.
            self.viewer_grid._grid_mode = False
            self.viewer_grid._grid_images = []
            self.viewer_grid._grid_index_to_path = {}
            self.viewer_grid._grid_last_render_size = None
            self.viewer_grid._clear_grid_preload_cache()
        else:
            # En mode photo : on ne vide pas le cache (sinon écran noir),
            # mais on marque le grid comme périmé pour forcer le rebuild
            # quand l'utilisateur basculera en mosaïque.
            self._grid_scope_dirty = True

        if target_path and target_path.exists():
            self._display_on_viewers(target_path, grab_focus=False)
        else:
            self._on_viewer_mode_changed()
        self._refresh_contextual_counter(force=True)

    def _apply_directory_filter_for_path(self, path: Path | None) -> None:
        """Applique un filtre explicite de dossier dans la barre de recherche."""
        if not path or not hasattr(self, "var_search"):
            return
        folder_name = str(path.parent.name or "").strip()
        if not folder_name:
            return
        new_filter = f"Dossier={folder_name}"
        if str(self.var_search.get() or "") != new_filter:
            self.var_search.set(new_filter)
        self._scope_injected_search_filter = new_filter
        self._scope_injected_search_armed = True

    def _get_visible_ordered_paths(self) -> list[Path]:
        """Retourne les chemins visibles dans l'ordre exact du tableau (tri + filtre)."""
        if not hasattr(self, "tree"):
            return []

        paths: list[Path] = []
        for iid in self.tree.get_children():
            try:
                row = self._get_photo_by_id(int(iid))
                if row and row.get("filepath"):
                    full_p = self._abs_path(str(row["filepath"]))
                    if full_p and full_p.exists() and is_supported_image(full_p):
                        paths.append(full_p)
            except Exception:
                continue
        return paths

    def _get_album_ordered_paths(self) -> list[Path]:
        """Retourne les images de sélection selon l'index persistant (tri alphabétique)."""
        rows = self._sync_album_index()
        paths: list[Path] = []
        for row in rows:
            p = self._abs_path(str(row.get("filepath") or ""))
            if p and p.exists():
                paths.append(p)
        return paths

    def _is_album_scope_active(self) -> bool:
        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        return scope == "album"

    def _is_virtual_album_scope_active(self) -> bool:
        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        return scope == "virtual_album"

    def _get_virtual_album_ordered_paths(self) -> list[Path]:
        rows = self._build_virtual_album_rows()
        paths: list[Path] = []
        for row in rows:
            path_str = str(row.get("filepath") or "")
            if not path_str:
                continue
            full_p = self._abs_path(path_str)
            if full_p and full_p.exists():
                paths.append(full_p)
        return paths

    def _is_duplicates_scope_active(self) -> bool:
        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        return scope == "duplicates"

    def _is_faces_scope_active(self) -> bool:
        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        return scope == "faces"

    def _get_duplicate_ordered_paths(self) -> list[Path]:
        if not self.db:
            return []
        if self._duplicate_prepare_in_progress and not self._duplicate_cache_valid:
            return []
        if not self._duplicate_cache_valid:
            self._refresh_duplicate_cache()
        ids = self._duplicate_photo_ids
        if not ids:
            return []
        rows = self.db.get_all_photos(order_by="id")
        paths: list[Path] = []
        for row in rows:
            photo_id = int(row.get("id") or 0)
            if photo_id not in ids:
                continue
            path_str = str(row.get("filepath") or "")
            if not path_str:
                continue
            path = self._abs_path(path_str)
            if path and path.exists():
                paths.append(path)
        return paths

    def _get_faces_ordered_paths(self) -> list[Path]:
        if not self.db:
            return []
        active_person_id = int(getattr(self, "_active_face_person_id", 0) or 0)
        if active_person_id > 0:
            rows = self.db.get_photos_by_person(active_person_id)
        else:
            ids = self.db.get_photo_ids_with_faces()
            if not ids:
                return []
            all_rows = self.db.get_all_photos(order_by="id")
            rows = [r for r in all_rows if int(r.get("id") or 0) in ids]

        paths: list[Path] = []
        for row in rows:
            path_str = str(row.get("filepath") or "")
            if not path_str:
                continue
            path = self._abs_path(path_str)
            if path and path.exists():
                paths.append(path)
        return paths

    def _init_album_index_registry(self) -> None:
        if not self._album_index_db:
            return
        ensure_directory(self._album_index_db.parent)
        with sqlite3.connect(self._album_index_db) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS album_index (
                    album_root TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filesize INTEGER NOT NULL DEFAULT 0,
                    mtime_ns INTEGER NOT NULL DEFAULT 0,
                    date_taken TEXT,
                    indexed_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (album_root, filepath)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_album_index_root_filename ON album_index(album_root, filename)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS album_audit_state (
                    album_root TEXT PRIMARY KEY,
                    unreadable_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    audited_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _set_album_audit_state(self, album_root: Path, unreadable_count: int, missing_count: int) -> None:
        self._init_album_index_registry()
        with sqlite3.connect(self._album_index_db) as conn:
            conn.execute(
                """
                INSERT INTO album_audit_state(album_root, unreadable_count, missing_count, audited_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(album_root) DO UPDATE SET
                    unreadable_count=excluded.unreadable_count,
                    missing_count=excluded.missing_count,
                    audited_at=excluded.audited_at
                """,
                (str(album_root), int(unreadable_count), int(missing_count), int(time.time())),
            )

    def _get_album_audit_state(self, album_root: Path) -> tuple[int, int, int]:
        self._init_album_index_registry()
        with sqlite3.connect(self._album_index_db) as conn:
            row = conn.execute(
                "SELECT unreadable_count, missing_count, audited_at FROM album_audit_state WHERE album_root = ?",
                (str(album_root),),
            ).fetchone()
        if not row:
            return (0, 0, 0)
        return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))

    def _normalize_album_root(self) -> Path | None:
        if not hasattr(self, "var_out"):
            return None
        raw = self._normalize_saved_path(str(self.var_out.get() or ""))
        if not raw:
            return None
        root = Path(raw)
        if not root.exists() or not root.is_dir():
            return None
        try:
            return root.resolve()
        except Exception:
            return root

    def _get_known_album_roots(self) -> list[Path]:
        raw_paths: set[str] = set()

        if hasattr(self, "var_out"):
            current = self._normalize_saved_path(str(self.var_out.get() or ""))
            if current:
                raw_paths.add(current)

        config_out = self._normalize_saved_path(str(self.config_data.get("last_out") or ""))
        if config_out:
            raw_paths.add(config_out)

        for saved in getattr(self, "_saved_album_output_paths", []):
            normalized = self._normalize_saved_path(str(saved or ""))
            if normalized:
                raw_paths.add(normalized)

        roots: list[Path] = []
        for raw in sorted(raw_paths, key=str.casefold):
            path = Path(raw)
            if not path.exists() or not path.is_dir():
                continue
            try:
                roots.append(path.resolve())
            except Exception:
                roots.append(path)
        return roots

    def _is_path_in_album_output(self, candidate: Path | None) -> bool:
        if candidate is None:
            return False
        return self._path_is_within_root(candidate, self._normalize_album_root())

    def _is_path_in_any_album_output(self, candidate: Path | None) -> bool:
        if candidate is None:
            return False
        for root in self._get_known_album_roots():
            if self._path_is_within_root(candidate, root):
                return True
        return False

    def _is_path_in_faces_output(self, candidate: Path | None) -> bool:
        root = self._get_faces_root_dir()
        if root is None or candidate is None:
            return False
        try:
            c = candidate.resolve()
        except Exception:
            c = candidate
        try:
            c.relative_to(root.resolve())
            return True
        except Exception:
            return False

    def _get_faces_duplicates_dir(self) -> Path | None:
        root = self._get_faces_root_dir()
        if root is None:
            return None
        return root / "Doublons"

    def _is_path_in_faces_duplicates_dir(self, candidate: Path | None) -> bool:
        dup_root = self._get_faces_duplicates_dir()
        if dup_root is None or candidate is None:
            return False
        try:
            c = candidate.resolve()
        except Exception:
            c = candidate
        try:
            c.relative_to(dup_root.resolve())
            return True
        except Exception:
            return False

    def _is_path_excluded_from_source_scan(self, candidate: Path | None, source_root: Path | None = None) -> bool:
        """Retourne True si le chemin doit être exclu du scan source (car situé dans _PhotoSelect)."""
        if candidate is None:
            return False
        
        # 1. Vérification rapide des patterns ignorés (vignettes, etc.)
        if is_ignored_scan_path(candidate):
            return True
        
        # 2. Vérification optimisée du dossier portable _PhotoSelect
        # On évite Path.resolve() qui est extrêmement lent sur des dizaines de milliers de fichiers.
        root = source_root or self._current_source_dir
        if root:
            # On stocke le chemin normalisé du dossier meta pour comparaison textuelle
            if not hasattr(self, "_cached_meta_dir_norm"):
                self._cached_meta_dir_norm = os.path.normcase(str(root / META_DIR_NAME))
            
            cand_norm = os.path.normcase(str(candidate))
            if cand_norm.startswith(self._cached_meta_dir_norm):
                return True
                
        return False

        if self._is_path_in_faces_output(candidate):
            return True

        # Si une source explicite est fournie, ne considère les exclusions imbriquées
        # que lorsqu'elles sont réellement dans cette source.
        if source_root is not None:
            try:
                c = candidate.resolve()
            except Exception:
                c = candidate
            try:
                sr = source_root.resolve()
            except Exception:
                sr = source_root
            try:
                c.relative_to(sr)
            except Exception:
                return False
        return False

    def _iter_album_index_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        try:
            for current_root, dirnames, filenames in os.walk(root, topdown=True):
                root_path = Path(current_root)

                kept_dirs: list[str] = []
                for dirname in dirnames:
                    dir_path = root_path / dirname
                    if is_ignored_scan_path(dir_path):
                        continue
                    if self._is_path_in_faces_output(dir_path):
                        continue
                    if self._is_path_in_faces_duplicates_dir(dir_path):
                        continue
                    kept_dirs.append(dirname)
                dirnames[:] = kept_dirs

                for filename in filenames:
                    file_path = root_path / filename
                    if not is_supported_image(file_path):
                        continue
                    if is_ignored_scan_path(file_path):
                        continue
                    if self._is_path_in_faces_output(file_path):
                        continue
                    if self._is_path_in_faces_duplicates_dir(file_path):
                        continue
                    files.append(file_path)
        except Exception:
            return []
        return files

    def _ensure_faces_duplicates_dir(self) -> Path | None:
        dup_dir = self._get_faces_duplicates_dir()
        if dup_dir is None:
            return None
        try:
            ensure_directory(dup_dir)
        except Exception:
            return None

        if os.name == "nt":
            try:
                hidden = 0x2
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(dup_dir))
                if attrs != -1 and (attrs & hidden) == 0:
                    ctypes.windll.kernel32.SetFileAttributesW(str(dup_dir), attrs | hidden)
            except Exception:
                pass
        return dup_dir

    def _get_unreadable_audit_dir(self) -> Path | None:
        if not self._current_source_dir:
            return None
        return (self._current_source_dir / META_DIR_NAME / "Illisibles").resolve()

    def _ensure_unreadable_audit_dir(self) -> Path | None:
        unreadable_dir = self._get_unreadable_audit_dir()
        if unreadable_dir is None:
            return None
        try:
            ensure_directory(unreadable_dir)
        except Exception:
            return None
        return unreadable_dir

    @staticmethod
    def _build_incremented_unreadable_name(target_dir: Path, source_name: str) -> str:
        source = Path(source_name)
        stem = source.stem
        suffix = source.suffix
        idx = 1
        while True:
            candidate = f"{stem}__bad-{idx:04d}{suffix}"
            if not (target_dir / candidate).exists():
                return candidate
            idx += 1

    def _build_unreadable_copy_target(self, source_path: Path, unreadable_dir: Path) -> Path:
        rel_text = str(self._rel_path(source_path) or "").strip()
        rel_candidate = Path(rel_text) if rel_text else Path(source_path.name)
        if rel_candidate.is_absolute():
            rel_candidate = Path(source_path.name)

        safe_parts = [part for part in rel_candidate.parts if part not in ("", ".", "..")]
        rel_safe = Path(*safe_parts) if safe_parts else Path(source_path.name)
        target_path = unreadable_dir / rel_safe
        ensure_directory(target_path.parent)
        if target_path.exists():
            target_path = target_path.with_name(
                self._build_incremented_unreadable_name(target_path.parent, target_path.name)
            )
        return target_path

    def _build_signature_duplicate_drop_plan(
        self,
        rows: list[dict[str, Any]],
        only_photo_ids: set[int] | None = None,
    ) -> tuple[list[tuple[int, Path]], int]:
        """Construit la liste des doublons à retirer (on garde 1 fichier par signature)."""
        by_sig: dict[str, list[tuple[int, Path]]] = {}
        allowed_ids = set(only_photo_ids or set()) if only_photo_ids is not None else None
        for row in rows:
            photo_id = int(row.get("id") or 0)
            if photo_id <= 0:
                continue
            if allowed_ids is not None and photo_id not in allowed_ids:
                continue
            path_str = str(row.get("filepath") or "").strip()
            if not path_str:
                continue
            path = Path(path_str)
            if self._is_path_in_any_album_output(path):
                continue
            if self._is_path_in_faces_duplicates_dir(path):
                continue
            sig = self._compute_duplicate_signature_from_row(row)
            by_sig.setdefault(sig, []).append((photo_id, path))

        drop_list: list[tuple[int, Path]] = []
        group_count = 0
        for items in by_sig.values():
            if len(items) <= 1:
                continue
            group_count += 1
            items_sorted = sorted(items, key=lambda t: t[0])
            for photo_id, path in items_sorted[1:]:
                drop_list.append((photo_id, path))
        return drop_list, group_count

    @staticmethod
    def _build_incremented_duplicate_name(target_dir: Path, source_name: str) -> str:
        source = Path(source_name)
        stem = source.stem
        suffix = source.suffix
        idx = 1
        while True:
            candidate = f"{stem}__dup-{idx:04d}{suffix}"
            if not (target_dir / candidate).exists():
                return candidate
            idx += 1

    def _compute_duplicate_signature_from_row(self, row: dict[str, Any]) -> str:
        title = Path(str(row.get("filename") or "")).stem.strip().lower()
        date_taken = str(row.get("date_taken") or "").strip()
        size = int(row.get("filesize") or 0)
        return f"{size}|{title}|{date_taken}"

    def _get_active_duplicate_info(self) -> tuple[set[int], int]:
        """Retourne (ids_doublons, nb_groupes) en excluant explicitement le dossier de sélection."""
        if not self.db:
            return set(), 0
        rows = self.db.get_all_photos(order_by="id")
        if not rows:
            return set(), 0

        signatures: dict[str, list[int]] = {}
        for row in rows:
            photo_id = int(row.get("id") or 0)
            if photo_id <= 0:
                continue
            path_str = str(row.get("filepath") or "")
            if not path_str:
                continue
            path = Path(path_str)
            if self._is_path_in_any_album_output(path):
                continue
            if self._is_path_in_faces_duplicates_dir(path):
                continue
            key = self._compute_duplicate_signature_from_row(row)
            signatures.setdefault(key, []).append(photo_id)

        dup_ids: set[int] = set()
        group_count = 0
        for ids in signatures.values():
            if len(ids) > 1:
                group_count += 1
                dup_ids.update(ids)
        return dup_ids, group_count

    @staticmethod
    def _format_album_mtime(mtime_ns: int) -> str:
        try:
            return datetime.fromtimestamp(float(mtime_ns) / 1_000_000_000.0).strftime("%Y:%m:%d %H:%M:%S")
        except Exception:
            return "Inconnue"

    def _sync_album_index(self) -> list[dict[str, Any]]:
        root = self._normalize_album_root()
        if root is None:
            return []

        self._init_album_index_registry()
        album_root = str(root)
        now_ts = int(time.time())

        files: dict[str, tuple[str, int, int]] = {}
        for path in self._iter_album_index_files(root):
            try:
                stat = path.stat()
                # On stocke en RELATIF dans l'index de l'album aussi pour portabilité
                rel = self._rel_path(path)
            except Exception:
                continue
            files[rel] = (path.name, int(stat.st_size), int(stat.st_mtime_ns))

        with sqlite3.connect(self._album_index_db) as conn:
            existing_rows = conn.execute(
                "SELECT filepath, filename, filesize, mtime_ns FROM album_index WHERE album_root = ?",
                (album_root,),
            ).fetchall()
            existing = {str(r[0]): (str(r[1]), int(r[2] or 0), int(r[3] or 0)) for r in existing_rows}

            stale_paths = set(existing.keys()) - set(files.keys())
            for stale in stale_paths:
                conn.execute(
                    "DELETE FROM album_index WHERE album_root = ? AND filepath = ?",
                    (album_root, stale),
                )

            for filepath, (filename, filesize, mtime_ns) in files.items():
                prev = existing.get(filepath)
                if prev == (filename, filesize, mtime_ns):
                    conn.execute(
                        "UPDATE album_index SET indexed_at = ? WHERE album_root = ? AND filepath = ?",
                        (now_ts, album_root, filepath),
                    )
                    continue

                conn.execute(
                    """
                    INSERT INTO album_index(album_root, filepath, filename, filesize, mtime_ns, date_taken, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(album_root, filepath) DO UPDATE SET
                        filename=excluded.filename,
                        filesize=excluded.filesize,
                        mtime_ns=excluded.mtime_ns,
                        date_taken=excluded.date_taken,
                        indexed_at=excluded.indexed_at
                    """,
                    (
                        album_root,
                        filepath,
                        filename,
                        filesize,
                        mtime_ns,
                        self._format_album_mtime(mtime_ns),
                        now_ts,
                    ),
                )

            rows = conn.execute(
                "SELECT filepath, filename, filesize, date_taken FROM album_index WHERE album_root = ? ORDER BY filename COLLATE NOCASE",
                (album_root,),
            ).fetchall()

        return [
            {
                "filepath": str(r[0]),
                "filename": str(r[1]),
                "filesize": int(r[2] or 0),
                "date_taken": str(r[3] or "Inconnue"),
            }
            for r in rows
        ]

    def _remove_album_index_entry(self, path: Path) -> None:
        root = self._normalize_album_root()
        if root is None:
            return
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        with sqlite3.connect(self._album_index_db) as conn:
            conn.execute(
                "DELETE FROM album_index WHERE album_root = ? AND filepath = ?",
                (str(root), resolved),
            )

    def _album_folder_signature(self) -> tuple[str, int] | None:
        root = self._normalize_album_root()
        if root is None:
            return None
        try:
            return (str(root.resolve()), int(root.stat().st_mtime_ns))
        except Exception:
            return (str(root), 0)

    def _stop_album_watch(self) -> None:
        if self._album_watch_job:
            try:
                self.after_cancel(self._album_watch_job)
            except Exception:
                pass
        self._album_watch_job = None
        self._album_watch_signature = None

    def _ensure_album_watch_running(self) -> None:
        if self._album_watch_job:
            return
        self._album_watch_signature = self._album_folder_signature()
        self._album_watch_job = self.after(1500, self._tick_album_watch)

    def _tick_album_watch(self) -> None:
        self._album_watch_job = None
        if not self._is_album_scope_active():
            self._stop_album_watch()
            return

        current_sig = self._album_folder_signature()
        if current_sig != self._album_watch_signature:
            self._album_watch_signature = current_sig
            self._load_data_to_treeview()
            self._update_copies_counter()
            self._update_statistics()

        self._album_watch_job = self.after(1500, self._tick_album_watch)

    def _build_album_virtual_rows(self) -> list[dict[str, Any]]:
        """Construit les lignes virtuelles du tableau à partir du dossier de sélection."""
        indexed_rows = self._sync_album_index()
        rows: list[dict[str, Any]] = []
        for idx, entry in enumerate(indexed_rows, start=1):
            path = Path(str(entry.get("filepath") or ""))
            if not path.exists():
                continue
            filesize = int(entry.get("filesize") or 0)
            date_taken = str(entry.get("date_taken") or "Inconnue")
            directory_name = path.parent.name or ""

            rows.append(
                {
                    "id": -idx,
                    "is_copied": 0,
                    "directory_name": directory_name,
                    "collection_name": path.stem,
                    "filename": path.name,
                    "date_taken": date_taken,
                    "filesize": filesize,
                    "filepath": str(path),
                    "latitude": None,
                    "longitude": None,
                    "country": "",
                    "region": "",
                    "city": "",
                    "camera_model": "",
                    "_album_virtual": True,
                }
            )

        sort_col = self.__dict__.get("_sort_col", "date_taken")
        if sort_col == "filesize_str":
            sort_col = "filesize"
        elif sort_col == "camera":
            sort_col = "camera_model"
        elif sort_col == "copied":
            sort_col = "is_copied"
        elif sort_col == "directory_name":
            sort_col = "directory_name"

        def _sort_key(row: dict[str, Any]) -> Any:
            value = row.get(sort_col)
            if sort_col in ("filesize", "is_copied"):
                try:
                    return int(value or 0)
                except Exception:
                    return 0
            return str(value or "").lower()

        rows.sort(key=_sort_key, reverse=not bool(self.__dict__.get("_sort_asc", True)))
        return rows

    def _build_virtual_album_rows(self) -> list[dict[str, Any]]:
        """Construit les lignes du tableau pour l'album virtuel actif."""
        if not self.db:
            return []

        active_album = self._get_active_virtual_album()
        if active_album is None:
            return []

        photo_ids = self.db.get_virtual_album_photo_ids(int(active_album.get("id") or 0))
        if not photo_ids:
            return []

        rows: list[dict[str, Any]] = []
        for photo_id in photo_ids:
            row = self.db.get_photo_by_id(int(photo_id))
            if row is None:
                continue
            rows.append(row)
        return rows

    def _get_selected_tree_row(self) -> dict[str, Any] | None:
        if not hasattr(self, "tree"):
            return None
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self._get_photo_by_id(int(selection[0]))
        except Exception:
            return None

    def _resolve_target_path_for_scope(self, candidate: Path | None) -> Path | None:
        """Ajuste la cible selon la portée courante (incluant le mode Sélection)."""
        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        if scope == "faces":
            face_paths = self._get_faces_ordered_paths()
            if not face_paths:
                return None
            if candidate and any(p == candidate for p in face_paths):
                return candidate
            return face_paths[0]

        if scope == "duplicates":
            duplicate_paths = self._get_duplicate_ordered_paths()
            if not duplicate_paths:
                return None
            if candidate and any(p == candidate for p in duplicate_paths):
                return candidate
            return duplicate_paths[0]

        if scope == "virtual_album":
            virtual_album_paths = self._get_virtual_album_ordered_paths()
            if not virtual_album_paths:
                return None
            if candidate and any(p == candidate for p in virtual_album_paths):
                return candidate
            return virtual_album_paths[0]

        if scope != "album":
            return candidate

        album_paths = self._get_album_ordered_paths()
        if not album_paths:
            return None
        if candidate and any(p == candidate for p in album_paths):
            return candidate
        return album_paths[0]

    def _viewer_display_count(self) -> int:
        """Nombre d'images affichées simultanément par page en visionneuse Mosaïc."""
        try:
            _cols, page_size = self.viewer_grid._compute_grid_layout()
            return max(1, int(page_size))
        except Exception:
            return 1

    def _move_tree_selection_by_page(self, delta: int) -> str:
        """Déplace la sélection du tableau de N lignes (N = images affichées en mosaïque)."""
        children = self.tree.get_children()
        if not children:
            return "break"

        step = self._viewer_display_count()
        selection = self.tree.selection()
        if selection:
            try:
                current_index = children.index(selection[0])
            except ValueError:
                current_index = 0
        else:
            current_index = 0

        target_index = max(0, min(len(children) - 1, current_index + (delta * step)))
        target_iid = children[target_index]
        self.tree.selection_set(target_iid)
        self.tree.focus(target_iid)
        self.tree.see(target_iid)
        return "break"

    def _on_tree_page_up(self, _event: tk.Event) -> str:
        return self._move_tree_selection_by_page(-1)

    def _on_tree_page_down(self, _event: tk.Event) -> str:
        return self._move_tree_selection_by_page(1)

    def _audit_event(self, action: str, details: dict[str, Any] | None = None) -> None:
        """Enregistre un événement métier dans un CSV local d'audit."""
        try:
            ensure_directory(APP_LOG_DIR)
            exists = self._ops_log_file.exists()
            with open(self._ops_log_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=';')
                if not exists:
                    writer.writerow(["timestamp", "action", "source", "details_json"])
                source = str(self.var_in.get().strip()) if hasattr(self, "var_in") else ""
                writer.writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    action,
                    source,
                    json.dumps(details or {}, ensure_ascii=False),
                ])
        except Exception as exc:
            logger.debug("Audit non écrit (%s): %s", action, exc)

    def _export_operations_log(self) -> None:
        """Exporte le journal d'opérations métier vers un CSV choisi par l'utilisateur."""
        if not self._ops_log_file.exists():
            messagebox.showinfo("Journal opérations", "Aucun journal disponible pour le moment.")
            return

        default_name = f"operations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        target = filedialog.asksaveasfilename(
            title="Exporter le journal opérations",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV", "*.csv"), ("Tous les fichiers", "*.*")],
        )
        if not target:
            return

        try:
            shutil.copy2(self._ops_log_file, Path(target))
            self._audit_event("export_operations_log", {"target": str(target)})
            messagebox.showinfo("Journal opérations", "Export terminé.")
        except Exception as exc:
            messagebox.showerror("Journal opérations", f"Export impossible:\n{exc}")

    def _export_statistics_markdown(self) -> None:
        """Exporte les statistiques visibles vers un fichier Markdown."""
        if not hasattr(self, "_stats_vars"):
            messagebox.showinfo("Export statistiques", "Aucune statistique disponible pour le moment.")
            return

        self._update_statistics()

        default_name = f"statistiques_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        target = filedialog.asksaveasfilename(
            title="Exporter les statistiques (Markdown)",
            defaultextension=".md",
            initialfile=default_name,
            filetypes=[("Markdown", "*.md"), ("Tous les fichiers", "*.*")],
        )
        if not target:
            return

        labels = {
            "photos": "Nombre de photos",
            "dirs": "Nombre de dossiers",
            "size_total": "Taille totale",
            "copied": "Photos copiées",
            "gps_count": "Photos géolocalisées",
            "missing_gps_count": "Sans géolocalisation",
            "camera_count": "Modèles caméra",
            "camera_top": "Top caméra",
            "date_range": "Période de prise de vue",
            "duplicate_health": "Doublons signature",
            "faces_health": "Visages (traitées / photos / visages / personnes)",
            "face_perf": "Temps moyen / fichier (OpenCV)",
            "face_runtime_status": "Reconnaissance faciale",
            "album_health": "Santé sélection",
            "last_integrity_audit": "Dernier audit intégrité",
        }

        lines: list[str] = []
        lines.append("# Statistiques PhotoSelect")
        lines.append("")
        lines.append(f"- Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        source = str(self.var_in.get() or "").strip() if hasattr(self, "var_in") else ""
        if source:
            lines.append(f"- Source: {source}")
        scope = str(self.var_viewer_scope.get() or "") if hasattr(self, "var_viewer_scope") else ""
        if scope:
            lines.append(f"- Portée affichage: {self._get_scope_label_for_status(scope)}")
        lines.append("")

        for key, label in labels.items():
            var = self._stats_vars.get(key)
            value = str(var.get() if var is not None else "-")
            lines.append(f"- {label}: {value}")

        content = "\n".join(lines) + "\n"

        try:
            Path(target).write_text(content, encoding="utf-8")
            self._audit_event("export_statistics_markdown", {"target": str(target)})
            messagebox.showinfo("Export statistiques", "Export Markdown terminé.")
        except Exception as exc:
            messagebox.showerror("Export statistiques", f"Export impossible:\n{exc}")

    @staticmethod
    def _normalize_country_label(country: str) -> str:
        raw = country.strip()
        if not raw:
            return ""
        key = _remove_accents(raw).upper().replace(".", "").replace("_", " ").replace("-", " ")
        key = " ".join(key.split())
        mapping = {
            "FR": "FR",
            "FRA": "FR",
            "FRANCE": "FR",
            "FRENCH REPUBLIC": "FR",
            "REPUBLIC FRANCAISE": "FR",
            "US": "US",
            "USA": "US",
            "UNITED STATES": "US",
            "UNITED STATES OF AMERICA": "US",
            "GB": "GB",
            "UK": "GB",
            "UNITED KINGDOM": "GB",
            "DE": "DE",
            "DEU": "DE",
            "GERMANY": "DE",
            "IT": "IT",
            "ITA": "IT",
            "ITALY": "IT",
            "ES": "ES",
            "ESP": "ES",
            "SPAIN": "ES",
        }
        if key in mapping:
            return mapping[key]
        if len(key) == 2 and key.isalpha():
            return key
        return raw

    @staticmethod
    def _normalize_place_label(text: str) -> str:
        val = " ".join(text.strip().split())
        val = re.sub(r"\s*-\s*", "-", val)
        val = re.sub(r"\s*'\s*", "'", val)
        return val

    def _normalize_geo_labels(self) -> None:
        """Normalise les libellés lieux (pays/region/ville) sur toute la base active."""
        if not self.db:
            messagebox.showinfo("Normalisation", "Aucune base active.")
            return

        rows = self.db.get_all_photos(order_by="id")
        if not rows:
            messagebox.showinfo("Normalisation", "Aucune photo à normaliser.")
            return

        # Canonicalisation par variantes pour préserver les accents majoritaires déjà présents.
        def _build_variant_map(values: list[str]) -> dict[str, str]:
            counter: dict[str, int] = {}
            original_by_key: dict[str, str] = {}
            for val in values:
                clean = self._normalize_place_label(val)
                if not clean:
                    continue
                key = _remove_accents(clean).lower()
                counter[key] = counter.get(key, 0) + 1
                best = original_by_key.get(key)
                if best is None or len(clean) > len(best):
                    original_by_key[key] = clean
            return original_by_key

        region_map = _build_variant_map([str(r.get("region") or "") for r in rows])
        city_map = _build_variant_map([str(r.get("city") or "") for r in rows])

        changed_rows = 0
        changed_fields = 0
        processed = 0

        self._begin_long_action()

        try:
            self._begin_photo_progress(len(rows), "Normalisation lieux")

            for idx, row in enumerate(rows, start=1):
                if self._stop_event.is_set():
                    break
                photo_id = int(row["id"])
                old_country = str(row.get("country") or "")
                old_region = str(row.get("region") or "")
                old_city = str(row.get("city") or "")

                new_country = self._normalize_country_label(old_country)
                region_clean = self._normalize_place_label(old_region)
                city_clean = self._normalize_place_label(old_city)
                new_region = region_map.get(_remove_accents(region_clean).lower(), region_clean) if region_clean else ""
                new_city = city_map.get(_remove_accents(city_clean).lower(), city_clean) if city_clean else ""

                if (new_country, new_region, new_city) == (old_country, old_region, old_city):
                    processed = idx
                    self._tick_photo_progress(idx, len(rows), "Normalisation lieux")
                    continue

                lat = row.get("latitude")
                lon = row.get("longitude")
                self.db.update_photo_location(photo_id, lat, lon, new_country, new_region, new_city)
                changed_rows += 1
                changed_fields += int(new_country != old_country) + int(new_region != old_region) + int(new_city != old_city)
                processed = idx
                self._tick_photo_progress(idx, len(rows), "Normalisation lieux")

            if self._stop_event.is_set():
                self.progress.configure(maximum=max(1, len(rows)), value=min(processed, max(1, len(rows))))
                self.var_counter.set(f"Normalisation interrompue: {processed}/{len(rows)}")
            else:
                self._end_photo_progress(len(rows), "Normalisation lieux")

            self._load_data_to_treeview()
            self._update_statistics()
            self._audit_event("normalize_geo_labels", {
                "rows_total": len(rows),
                "rows_changed": changed_rows,
                "fields_changed": changed_fields,
                "cancelled": bool(self._stop_event.is_set()),
            })
            if self._stop_event.is_set():
                messagebox.showwarning(
                    "Normalisation lieux",
                    f"Interrompue. Lignes modifiées: {changed_rows} / {processed}."
                )
            else:
                messagebox.showinfo(
                    "Normalisation lieux",
                    f"Terminé. Lignes modifiées: {changed_rows} / {len(rows)}\nChamps ajustés: {changed_fields}."
                )
        finally:
            self._end_long_action()

    def _run_integrity_audit_and_reconcile(self) -> None:
        """Lance l'audit d'intégrité en arrière-plan puis propose une réconciliation guidée."""
        if not self.db:
            messagebox.showinfo("Audit intégrité", "Aucune base active.")
            return

        if self._audit_thread and self._audit_thread.is_alive():
            messagebox.showinfo("Audit intégrité", "Un audit est déjà en cours.")
            return

        self._pause_face_worker_for_audit()
        options = self._ask_integrity_audit_options()
        if not options:
            self._resume_face_worker_after_audit()
            return

        # Capture des chemins exclus depuis l'UI (accès Tkinter), puis préparation en thread fond.
        album_root = self._normalize_album_root()
        faces_root = self._get_faces_root_dir()
        duplicates_root = (faces_root / "Doublons") if faces_root is not None else None
        source_root: Path | None = None
        try:
            src_candidate = Path(str(self.var_in.get() or "").strip()) if hasattr(self, "var_in") else None
            if src_candidate and src_candidate.exists() and src_candidate.is_dir():
                source_root = src_candidate
        except Exception:
            source_root = None

        self._begin_long_action()
        photo_analysis_required = any((
            bool(options.get("check_missing", True)),
            bool(options.get("check_duplicate_paths", True)),
            bool(options.get("check_signature_duplicates", True)),
            bool(options.get("check_exif_to_db", True)),
            bool(options.get("check_db_to_exif", False)),
            bool(options.get("check_gps_without_place", True)),
            bool(options.get("check_unreadable_viewer", True)),
            bool(options.get("cleanup_duplicates_from_scan", False)),
        ))
        if bool(options.get("check_hidden_dirs", False)) and not photo_analysis_required:
            self.var_counter.set("Audit intégrité: balayage des répertoires cachés...")
        else:
            self.var_counter.set("Audit intégrité: préparation...")

        self._audit_thread = threading.Thread(
            target=self._integrity_audit_start_worker,
            args=(options, album_root, faces_root, duplicates_root, source_root),
            daemon=True,
        )
        self._audit_thread.start()

    @staticmethod
    def _path_is_within_root(candidate: Path, root: Path | None) -> bool:
        if root is None:
            return False
        try:
            c = candidate.resolve()
        except Exception:
            c = candidate
        try:
            r = root.resolve()
        except Exception:
            r = root
        try:
            c.relative_to(r)
            return True
        except Exception:
            return False

    @staticmethod
    def _delete_path_permanently(path: Path) -> bool:
        """Supprime définitivement un fichier/dossier (sans corbeille)."""
        def _make_deletable(target: Path) -> None:
            try:
                os.chmod(target, 0o777)
            except Exception:
                pass
            if os.name == "nt":
                try:
                    attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(target)))
                    if attrs != -1:
                        file_attribute_readonly = 0x1
                        updated_attrs = attrs & ~file_attribute_readonly
                        if updated_attrs != attrs:
                            ctypes.windll.kernel32.SetFileAttributesW(str(target), updated_attrs)
                except Exception:
                    pass

        def _on_rmtree_error(func: Any, failed_path: str, _exc_info: Any) -> None:
            failed_target = Path(failed_path)
            _make_deletable(failed_target)
            func(failed_path)

        try:
            if not path.exists():
                return True
            if path.is_dir() and not path.is_symlink():
                for root, dirnames, filenames in os.walk(path, topdown=False):
                    root_path = Path(root)
                    for filename in filenames:
                        _make_deletable(root_path / filename)
                    for dirname in dirnames:
                        _make_deletable(root_path / dirname)
                    _make_deletable(root_path)
                _make_deletable(path)
                shutil.rmtree(path, onerror=_on_rmtree_error)
            else:
                _make_deletable(path)
                path.unlink()
            return True
        except Exception as exc:
            logger.warning("Suppression définitive impossible pour %s: %s", path, exc)
            return False

    @staticmethod
    def _is_hidden_directory_path(path: Path) -> bool:
        """Retourne True si un dossier est caché (attribut Windows ou nom '.')."""
        try:
            if not path.exists() or not path.is_dir():
                return False
        except Exception:
            return False

        if str(path.name or "").startswith("."):
            return True

        if os.name == "nt":
            try:
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
                if attrs == -1:
                    return False
                hidden = 0x2
                return bool(attrs & hidden)
            except Exception:
                return False
        return False

    def _discover_hidden_dirs_for_audit(self, source_root: Path | None) -> list[dict[str, Any]]:
        """Détecte les dossiers cachés sur disque sous la source, indépendamment de la base."""
        if source_root is None:
            return []
        try:
            if not source_root.exists() or not source_root.is_dir():
                return []
        except Exception:
            return []

        found: dict[str, dict[str, Any]] = {}
        for root, dirnames, _filenames in os.walk(source_root, topdown=True):
            root_path = Path(root)
            kept_dirs: list[str] = []
            for dirname in dirnames:
                dir_path = root_path / dirname
                if self._is_hidden_directory_path(dir_path):
                    try:
                        resolved = dir_path.resolve()
                    except Exception:
                        resolved = dir_path
                    key = str(resolved)
                    if key not in found:
                        found[key] = {
                            "path": key,
                            "name": str(resolved.name or key),
                            "photo_ids": [],
                        }
                    # On prune le sous-arbre: supprimer le parent caché suffit.
                    continue
                kept_dirs.append(dirname)
            dirnames[:] = kept_dirs
        return list(found.values())

    def _integrity_audit_start_worker(
        self,
        options: dict[str, bool],
        album_root: Path | None,
        faces_root: Path | None,
        duplicates_root: Path | None,
        source_root: Path | None,
    ) -> None:
        """Prépare la liste d'audit en thread de fond puis lance l'analyse."""
        try:
            needs_photo_rows = any((
                bool(options.get("check_missing", True)),
                bool(options.get("check_duplicate_paths", True)),
                bool(options.get("check_signature_duplicates", True)),
                bool(options.get("check_exif_to_db", True)),
                bool(options.get("check_db_to_exif", False)),
                bool(options.get("check_gps_without_place", True)),
                bool(options.get("check_unreadable_viewer", True)),
                bool(options.get("cleanup_duplicates_from_scan", False)),
            ))
            hidden_dirs = self._discover_hidden_dirs_for_audit(source_root) if bool(options.get("check_hidden_dirs", False)) else []
            rows = (
                self._get_rows_for_scope_audit(
                    album_root=album_root,
                    faces_root=faces_root,
                    duplicates_root=duplicates_root,
                )
                if needs_photo_rows else
                []
            )
            if not rows and not hidden_dirs:
                self.after(0, self._on_integrity_audit_no_rows)
                return

            self.after(0, lambda total=len(rows): self._on_integrity_audit_rows_ready(total))
            self._integrity_audit_worker(rows, options, source_root, hidden_dirs)
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._on_integrity_audit_start_failed(e))

    def _on_integrity_audit_rows_ready(self, total: int) -> None:
        self._begin_photo_progress(total, "Audit intégrité")
        self.var_counter.set(f"Audit intégrité: 0/{total} (estimation en cours...)")

    def _on_integrity_audit_no_rows(self) -> None:
        self._end_long_action()
        self._resume_face_worker_after_audit()
        messagebox.showinfo("Audit intégrité", "Aucun élément à auditer.")

    def _on_integrity_audit_start_failed(self, error_text: str) -> None:
        self._end_long_action()
        self._resume_face_worker_after_audit()
        messagebox.showerror("Audit intégrité", f"Préparation de l'audit impossible:\n{error_text}")

    def _ask_integrity_audit_options(self) -> dict[str, bool] | None:
        """Fenêtre de choix des fonctions à exécuter pendant l'audit intégré."""
        prefs = self._get_audit_function_preferences()
        dlg = tk.Toplevel(self)
        dlg.title("Audit intégrité - Fonctions")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        frame = tk.Frame(dlg, padx=14, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Sélectionnez les fonctions d'audit à exécuter :",
            justify="left",
            anchor="w",
            font=("Segoe UI", 10, "bold"),
        ).pack(fill=tk.X, pady=(0, 8))

        if bool(getattr(self, "_face_worker_paused_for_audit", False)):
            tk.Label(
                frame,
                text="Reconnaissance faciale en pause temporaire pendant l'audit.",
                justify="left",
                anchor="w",
                fg="#084298",
                bg="#cfe2ff",
                padx=8,
                pady=4,
                font=("Segoe UI", 9, "bold"),
            ).pack(fill=tk.X, pady=(0, 8))

        auto_fix_badge = tk.Label(
            frame,
            text="Auto-correction active: les défauts détectés seront appliqués automatiquement.",
            justify="left",
            anchor="w",
            fg="#0f5132",
            bg="#d1e7dd",
            padx=8,
            pady=4,
            font=("Segoe UI", 9, "bold"),
        )

        var_missing = tk.BooleanVar(value=bool(prefs.get("check_missing", True)))
        var_dupe_path = tk.BooleanVar(value=bool(prefs.get("check_duplicate_paths", True)))
        var_sig_dupes = tk.BooleanVar(value=bool(prefs.get("check_signature_duplicates", True)))
        var_exif_to_db = tk.BooleanVar(value=bool(prefs.get("check_exif_to_db", True)))
        var_db_to_exif = tk.BooleanVar(value=bool(prefs.get("check_db_to_exif", False)))
        var_gps_without_place = tk.BooleanVar(value=bool(prefs.get("check_gps_without_place", True)))
        var_hidden_dirs = tk.BooleanVar(value=bool(prefs.get("check_hidden_dirs", False)))
        var_unreadable = tk.BooleanVar(value=bool(prefs.get("check_unreadable_viewer", True)))
        var_auto_fix = tk.BooleanVar(value=bool(prefs.get("auto_fix_defects", True)))

        duplicates_scan_ready = bool(self._duplicate_cache_valid and not self._duplicate_prepare_in_progress and self._duplicate_photo_ids)
        var_cleanup_duplicates_from_scan = tk.BooleanVar(value=False)

        tk.Checkbutton(frame, text="Vérifier fichiers manquants", variable=var_missing).pack(anchor="w")
        def _sync_direction_exclusive(direction: str) -> None:
            if direction == "exif_to_db" and var_exif_to_db.get():
                var_db_to_exif.set(False)
            elif direction == "db_to_exif" and var_db_to_exif.get():
                var_exif_to_db.set(False)

        tk.Checkbutton(
            frame,
            text="Vérifier: copier GPS du fichier (EXIF) vers la base",
            variable=var_exif_to_db,
            command=lambda: _sync_direction_exclusive("exif_to_db"),
        ).pack(anchor="w")
        tk.Checkbutton(
            frame,
            text="Vérifier: écrire GPS de la base vers le fichier (EXIF)",
            variable=var_db_to_exif,
            command=lambda: _sync_direction_exclusive("db_to_exif"),
        ).pack(anchor="w")
        tk.Checkbutton(frame, text="Vérifier GPS sans lieu (corrigeable offline)", variable=var_gps_without_place).pack(anchor="w")
        tk.Checkbutton(frame, text="Vérifier fichiers illisibles par la visionneuse (via la base)", variable=var_unreadable).pack(anchor="w")
        tk.Checkbutton(frame, text="Effacer dossiers cachés (sélection après audit)", variable=var_hidden_dirs).pack(anchor="w")
        tk.Checkbutton(frame, text="Vérifier doublons DB de chemin", variable=var_dupe_path).pack(anchor="w")
        tk.Checkbutton(frame, text="Vérifier doublons signature (taille+titre+date)", variable=var_sig_dupes).pack(anchor="w")
        cb_cleanup_dupes = tk.Checkbutton(
            frame,
            text="Déplacer les doublons (scan Doublons vérifié obligatoire)",
            variable=var_cleanup_duplicates_from_scan,
        )
        cb_cleanup_dupes.pack(anchor="w", pady=(6, 0))
        if not duplicates_scan_ready:
            cb_cleanup_dupes.config(state="disabled")
            tk.Label(
                frame,
                text="Indisponible: ouvrez d'abord la portée Doublons pour préparer et vérifier le scan.",
                justify="left",
                anchor="w",
                fg="#7f1d1d",
                bg="#fee2e2",
                padx=8,
                pady=4,
                font=("Segoe UI", 9, "bold"),
            ).pack(fill=tk.X, pady=(4, 0))
        tk.Checkbutton(
            frame,
            text="Corriger les défauts détectés (si possible)",
            variable=var_auto_fix,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(8, 0))

        def _refresh_auto_fix_badge(*_args: Any) -> None:
            if var_auto_fix.get():
                auto_fix_badge.pack(fill=tk.X, pady=(8, 0))
            else:
                auto_fix_badge.pack_forget()

        var_auto_fix.trace_add("write", _refresh_auto_fix_badge)
        _refresh_auto_fix_badge()

        result: dict[str, bool] = {}

        def _confirm(_event: tk.Event | None = None) -> None:
            selected = {
                "check_missing": bool(var_missing.get()),
                "check_duplicate_paths": bool(var_dupe_path.get()),
                "check_signature_duplicates": bool(var_sig_dupes.get()),
                "check_exif_to_db": bool(var_exif_to_db.get()),
                "check_db_to_exif": bool(var_db_to_exif.get()),
                "check_gps_without_place": bool(var_gps_without_place.get()),
                "check_hidden_dirs": bool(var_hidden_dirs.get()),
                "check_unreadable_viewer": bool(var_unreadable.get()),
                "cleanup_duplicates_from_scan": bool(var_cleanup_duplicates_from_scan.get()),
                "auto_fix_defects": bool(var_auto_fix.get()),
            }
            if selected["check_exif_to_db"] and selected["check_db_to_exif"]:
                messagebox.showwarning(
                    "Audit intégrité",
                    "Choisissez un seul sens de synchronisation GPS: fichier(EXIF)->base OU base->fichier(EXIF).",
                )
                return
            if not any(selected.values()):
                messagebox.showwarning("Audit intégrité", "Sélectionnez au moins une fonction d'audit.")
                return
            self._save_audit_function_preferences(selected)
            result.update(selected)
            dlg.destroy()

        def _cancel(_event: tk.Event | None = None) -> None:
            dlg.destroy()

        btns = tk.Frame(frame)
        btns.pack(fill=tk.X, pady=(10, 0))
        tk.Button(btns, text="Lancer", width=14, command=_confirm, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btns, text="Annuler", width=14, command=_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT)

        dlg.bind("<Return>", _confirm)
        dlg.bind("<Escape>", _cancel)
        self._center_dialog_on_main(dlg)
        self.wait_window(dlg)

        return result or None

    def _get_rows_for_scope_audit(
        self,
        album_root: Path | None = None,
        faces_root: Path | None = None,
        duplicates_root: Path | None = None,
    ) -> list[dict[str, Any]]:
        """Retourne toutes les lignes auditées (indépendant scope/filtre), avec exclusions techniques."""
        if not self.db:
            return []

        if album_root is None and faces_root is None and duplicates_root is None:
            album_root = self._normalize_album_root()
            faces_root = self._get_faces_root_dir()
            duplicates_root = (faces_root / "Doublons") if faces_root is not None else None

        rows = self.db.get_all_photos(order_by="id")
        if not rows:
            return []

        filtered: list[dict[str, Any]] = []
        for row in rows:
            path_str = str(row.get("filepath") or "").strip()
            if not path_str:
                continue
            path = Path(path_str)
            if self._path_is_within_root(path, album_root):
                continue
            if self._path_is_within_root(path, faces_root):
                continue
            if self._path_is_within_root(path, duplicates_root):
                continue
            filtered.append(row)
        return filtered

    def _integrity_audit_worker(
        self,
        rows: list[dict[str, Any]],
        options: dict[str, bool],
        source_root: Path | None,
        hidden_dirs_seed: list[dict[str, Any]] | None = None,
    ) -> None:
        """Analyse intégrité lourde en thread de fond pour garder l'UI réactive."""
        total = len(rows)
        missing_ids: list[int] = []
        duplicate_ids: list[int] = []
        unreadable_entries: list[dict[str, Any]] = []
        local_geo_fill: list[tuple[int, float, float, str, str, str, Path]] = []
        signature_counts: dict[str, int] = {}
        signature_by_photo_id: dict[int, str] = {}
        seen_paths: dict[str, int] = {}
        exif_to_db: list[tuple[int, float, float]] = []
        db_to_exif: list[tuple[int, float, float, str, str, str, Path]] = []
        tol = 1e-5

        started = time.time()
        eta_ready = False
        eta_probe = max(50, min(500, total // 20 if total > 0 else 50))

        check_missing = bool(options.get("check_missing", True))
        check_duplicate_paths = bool(options.get("check_duplicate_paths", True))
        check_signature_duplicates = bool(options.get("check_signature_duplicates", True))
        check_exif_to_db = bool(options.get("check_exif_to_db", True))
        check_db_to_exif = bool(options.get("check_db_to_exif", True))
        check_gps_without_place = bool(options.get("check_gps_without_place", True))
        check_hidden_dirs = bool(options.get("check_hidden_dirs", False))
        check_unreadable_viewer = bool(options.get("check_unreadable_viewer", True))
        needs_file_probe = bool(check_missing or check_exif_to_db or check_db_to_exif or check_unreadable_viewer)
        needs_exif_probe = bool(check_exif_to_db or check_db_to_exif)
        reconcile_prefs = self._get_audit_reconcile_preferences()
        gps_without_place_eligible = 0
        gps_without_place_resolved = 0
        gps_without_place_unresolved = 0
        gps_without_place_invalid_coords = 0
        hidden_dirs_map: dict[str, dict[str, Any]] = {}
        for item in hidden_dirs_seed or []:
            path_str = str(item.get("path") or "").strip()
            if not path_str:
                continue
            hidden_dirs_map[path_str] = {
                "path": path_str,
                "name": str(item.get("name") or Path(path_str).name or path_str),
                "photo_ids": set(int(pid) for pid in (item.get("photo_ids") or []) if int(pid) > 0),
            }
        hidden_dir_roots: list[tuple[Path, dict[str, Any]]] = []
        for entry in hidden_dirs_map.values():
            try:
                hidden_dir_roots.append((Path(str(entry.get("path") or "")), entry))
            except Exception:
                continue

        def _to_float_or_none(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                s = str(value).strip()
                if not s:
                    return None
                return float(s)
            except Exception:
                return None

        for idx, row in enumerate(rows, start=1):
            if self._stop_event.is_set():
                self.after(0, lambda p=idx: self._on_integrity_audit_cancelled(total, p))
                return

            photo_id = int(row["id"])
            path = self._abs_path(str(row.get("filepath") or ""))

            if check_signature_duplicates:
                title = Path(str(row.get("filename") or "")).stem.strip().lower()
                date_taken = str(row.get("date_taken") or "").strip()
                size = int(row.get("filesize") or 0)
                sig_key = f"{size}|{title}|{date_taken}"
                signature_by_photo_id[photo_id] = sig_key
                signature_counts[sig_key] = int(signature_counts.get(sig_key, 0)) + 1

            if check_duplicate_paths:
                try:
                    canon = os.path.normcase(str(path.resolve(strict=False)))
                except Exception:
                    canon = os.path.normcase(str(path))
                if canon in seen_paths:
                    duplicate_ids.append(photo_id)
                else:
                    seen_paths[canon] = photo_id

            if check_hidden_dirs and hidden_dir_roots:
                for hidden_root, entry in hidden_dir_roots:
                    if self._path_is_within_root(path, hidden_root):
                        entry["photo_ids"].add(photo_id)
                        break

            raw_lat = row.get("latitude")
            raw_lon = row.get("longitude")
            db_lat = _to_float_or_none(raw_lat)
            db_lon = _to_float_or_none(raw_lon)
            db_has = db_lat is not None and db_lon is not None
            if check_gps_without_place:
                db_has_geo_text = bool(str(row.get("country") or "").strip() or str(row.get("region") or "").strip() or str(row.get("city") or "").strip())
                if not db_has_geo_text:
                    if db_has:
                        gps_without_place_eligible += 1
                        local_loc = get_location_details(db_lat, db_lon)
                        if local_loc.get("country") or local_loc.get("region") or local_loc.get("city"):
                            local_geo_fill.append(
                                (
                                    photo_id,
                                    db_lat,
                                    db_lon,
                                    str(local_loc.get("country") or ""),
                                    str(local_loc.get("region") or ""),
                                    str(local_loc.get("city") or ""),
                                    path,
                                )
                            )
                            gps_without_place_resolved += 1
                        else:
                            gps_without_place_unresolved += 1
                    else:
                        has_raw_lat = raw_lat is not None and str(raw_lat).strip() != ""
                        has_raw_lon = raw_lon is not None and str(raw_lon).strip() != ""
                        if has_raw_lat or has_raw_lon:
                            gps_without_place_invalid_coords += 1

            path_exists: bool | None = None
            if needs_file_probe:
                path_exists = path.exists()
                if check_missing and not path_exists:
                    missing_ids.append(photo_id)

            if check_unreadable_viewer and path_exists:
                try:
                    with Image.open(path) as img:
                        img.verify()
                except Exception as exc:
                    unreadable_entries.append({
                        "photo_id": photo_id,
                        "path": str(path),
                        "error": str(exc),
                    })

            if needs_exif_probe and path_exists and self._supports_exif_write(path):
                exif = read_gps_exif(path)
                exif_has = "latitude" in exif and "longitude" in exif

                if check_exif_to_db and exif_has and not db_has:
                    exif_to_db.append((photo_id, float(exif["latitude"]), float(exif["longitude"])))
                elif check_db_to_exif and db_has and not exif_has:
                    db_to_exif.append((
                        photo_id,
                        db_lat,
                        db_lon,
                        str(row.get("country") or ""),
                        str(row.get("region") or ""),
                        str(row.get("city") or ""),
                        path,
                    ))
                elif check_exif_to_db and exif_has and db_has:
                    if abs(float(exif["latitude"]) - db_lat) > tol or abs(float(exif["longitude"]) - db_lon) > tol:
                        exif_to_db.append((photo_id, float(exif["latitude"]), float(exif["longitude"])))

            eta_seconds: float | None = None
            if idx >= eta_probe:
                elapsed = time.time() - started
                if elapsed > 0:
                    eta_seconds = (elapsed / idx) * (total - idx)
                    eta_ready = True
            if not eta_ready:
                self._tick_photo_progress_threadsafe(idx, total, "Audit intégrité")
            else:
                self._tick_photo_progress_threadsafe(idx, total, "Audit intégrité", eta_seconds)

        if check_signature_duplicates:
            signature_duplicate_ids = [
                int(r.get("id") or 0)
                for r in rows
                if int(r.get("id") or 0) > 0 and signature_counts.get(signature_by_photo_id.get(int(r.get("id") or 0), ""), 0) > 1
            ]
            signature_group_count = len({
                signature_by_photo_id.get(pid, "")
                for pid in signature_duplicate_ids
                if signature_by_photo_id.get(pid, "")
            })
        else:
            signature_duplicate_ids = []
            signature_group_count = 0

        if check_gps_without_place:
            logger.info(
                "Audit intégrité GPS sans lieu: photos=%d, éligibles=%d, résolues_local=%d, non_résolues=%d, coords_invalides=%d",
                total,
                gps_without_place_eligible,
                gps_without_place_resolved,
                gps_without_place_unresolved,
                gps_without_place_invalid_coords,
            )

        self.after(0, lambda: self._on_integrity_audit_ready({
            "rows": rows,
            "options": options,
            "missing_ids": missing_ids,
            "duplicate_ids": duplicate_ids,
            "unreadable_entries": unreadable_entries,
            "local_geo_fill": local_geo_fill,
            "gps_without_place_stats": {
                "eligible": gps_without_place_eligible,
                "resolved": gps_without_place_resolved,
                "unresolved": gps_without_place_unresolved,
                "invalid_coords": gps_without_place_invalid_coords,
            },
            "signature_duplicate_ids": signature_duplicate_ids,
            "signature_group_count": signature_group_count,
            "exif_to_db": exif_to_db,
            "db_to_exif": db_to_exif,
            "hidden_dirs": [
                {
                    "path": str(v.get("path") or ""),
                    "name": str(v.get("name") or ""),
                    "photo_ids": sorted(int(pid) for pid in (v.get("photo_ids") or set()) if int(pid) > 0),
                }
                for v in hidden_dirs_map.values()
            ],
        }))

    def _ask_hidden_dirs_selection(self, hidden_dirs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Propose les dossiers cachés détectés, groupés par nom et cochés par défaut."""
        if not hidden_dirs:
            return []

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in hidden_dirs:
            name = str(item.get("name") or "Sans nom")
            grouped.setdefault(name, []).append(item)

        dlg = tk.Toplevel(self)
        dlg.title("Audit - Dossiers cachés")
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()
        dlg.minsize(760, 420)

        frame = tk.Frame(dlg, padx=12, pady=10)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Sélectionnez les dossiers cachés à supprimer définitivement :",
            anchor="w",
            justify="left",
            font=("Segoe UI", 10, "bold"),
        ).pack(fill=tk.X, pady=(0, 8))

        body = tk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        list_frame = tk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")

        def _on_list_configure(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=max(1, int(event.width)))

        list_frame.bind("<Configure>", _on_list_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        selections: list[tuple[tk.BooleanVar, dict[str, Any]]] = []
        for group_name in sorted(grouped.keys(), key=lambda s: _remove_accents(str(s)).lower()):
            group_items = grouped[group_name]
            tk.Label(
                list_frame,
                text=f"{group_name} ({len(group_items)})",
                anchor="w",
                justify="left",
                fg="#1f2937",
                font=("Segoe UI", 10, "bold"),
            ).pack(fill=tk.X, pady=(8, 2))
            for item in sorted(group_items, key=lambda d: _remove_accents(str(d.get("path") or "")).lower()):
                var = tk.BooleanVar(value=True)
                linked = int(len(item.get("photo_ids") or []))
                tk.Checkbutton(
                    list_frame,
                    text=f"{str(item.get('path') or '')} (photos liées: {linked})",
                    variable=var,
                    anchor="w",
                    justify="left",
                    wraplength=700,
                ).pack(fill=tk.X, padx=(12, 0))
                selections.append((var, item))

        result: dict[str, Any] = {"ok": False, "items": []}

        def _confirm(_event: tk.Event | None = None) -> None:
            result["items"] = [item for var, item in selections if bool(var.get())]
            result["ok"] = True
            dlg.destroy()

        def _cancel(_event: tk.Event | None = None) -> None:
            dlg.destroy()

        btns = tk.Frame(frame)
        btns.pack(fill=tk.X, pady=(10, 0))
        tk.Button(btns, text="Appliquer", width=14, command=_confirm, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btns, text="Annuler", width=14, command=_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT)

        dlg.bind("<Return>", _confirm)
        dlg.bind("<Escape>", _cancel)
        self._center_dialog_on_main(dlg)
        self.wait_window(dlg)

        if not result.get("ok"):
            return []
        return list(result.get("items") or [])

    def _on_integrity_audit_cancelled(self, total: int, processed: int) -> None:
        self.progress.configure(maximum=max(1, total), value=min(processed, max(1, total)))
        self.var_counter.set(f"Audit intégrité interrompu: {processed}/{total}")
        self._set_last_integrity_audit_stats(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | Audit interrompu: {processed}/{total}"
        )
        self._audit_event("integrity_audit", {
            "rows": total,
            "cancelled": True,
            "processed": processed,
        })
        self._end_long_action()
        self._resume_face_worker_after_audit()

    def _on_integrity_audit_ready(self, payload: dict[str, Any]) -> None:
        rows = payload["rows"]
        options = payload.get("options", {})
        missing_ids = payload["missing_ids"]
        duplicate_ids = payload["duplicate_ids"]
        unreadable_entries = payload.get("unreadable_entries", [])
        local_geo_fill = payload.get("local_geo_fill", [])
        gps_without_place_stats = payload.get("gps_without_place_stats", {})
        signature_duplicate_ids = payload.get("signature_duplicate_ids", [])
        signature_group_count = int(payload.get("signature_group_count") or 0)
        exif_to_db = payload["exif_to_db"]
        db_to_exif = payload["db_to_exif"]
        hidden_dirs = payload.get("hidden_dirs", [])

        check_missing = bool(options.get("check_missing", True))
        check_duplicate_paths = bool(options.get("check_duplicate_paths", True))
        check_signature_duplicates = bool(options.get("check_signature_duplicates", True))
        check_exif_to_db = bool(options.get("check_exif_to_db", True))
        check_db_to_exif = bool(options.get("check_db_to_exif", True))
        check_gps_without_place = bool(options.get("check_gps_without_place", True))
        check_hidden_dirs = bool(options.get("check_hidden_dirs", False))
        check_unreadable_viewer = bool(options.get("check_unreadable_viewer", True))
        cleanup_duplicates_from_scan = bool(options.get("cleanup_duplicates_from_scan", False))
        auto_fix_defects = bool(options.get("auto_fix_defects", False))
        photo_analysis_required = any((
            check_missing,
            check_duplicate_paths,
            check_signature_duplicates,
            check_exif_to_db,
            check_db_to_exif,
            check_gps_without_place,
            check_unreadable_viewer,
            cleanup_duplicates_from_scan,
        ))
        reconcile_prefs = self._get_audit_reconcile_preferences()

        duplicate_scan_ids = set(self._duplicate_photo_ids) if (self._duplicate_cache_valid and not self._duplicate_prepare_in_progress) else set()
        if cleanup_duplicates_from_scan and duplicate_scan_ids:
            signature_drop_plan, signature_group_count_plan = self._build_signature_duplicate_drop_plan(rows, only_photo_ids=duplicate_scan_ids)
        else:
            signature_drop_plan, signature_group_count_plan = ([], 0)

        self._end_photo_progress(len(rows), "Audit intégrité")

        dlg = tk.Toplevel(self)
        dlg.title("Audit intégrité & réconciliation")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        frame = tk.Frame(dlg, padx=14, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        summary = (
            f"Photos analysées: {len(rows) if photo_analysis_required else 'non requis'}\n"
            f"Fichiers manquants: {len(missing_ids) if check_missing else 'désactivé'}\n"
            f"Doublons DB (même chemin): {len(duplicate_ids) if check_duplicate_paths else 'désactivé'}\n"
            f"Doublons signature (taille+titre+date): {(str(len(signature_duplicate_ids)) + ' photos (' + str(signature_group_count) + ' groupes)') if check_signature_duplicates else 'désactivé'}\n"
            f"Déplacement doublons depuis scan Doublons vérifié: {(str(len(signature_drop_plan)) + ' fichiers (' + str(signature_group_count_plan) + ' groupes)') if cleanup_duplicates_from_scan else 'désactivé'}\n"
            f"Fichiers illisibles visionneuse: {len(unreadable_entries) if check_unreadable_viewer else 'désactivé'}\n"
            f"GPS sans lieu (corrigeable local): {len(local_geo_fill) if check_gps_without_place else 'désactivé'}\n"
            f"  - Éligibles GPS sans lieu: {int(gps_without_place_stats.get('eligible', 0)) if check_gps_without_place else 'désactivé'}\n"
            f"  - Non résolues en local: {int(gps_without_place_stats.get('unresolved', 0)) if check_gps_without_place else 'désactivé'}\n"
            f"  - Coordonnées invalides: {int(gps_without_place_stats.get('invalid_coords', 0)) if check_gps_without_place else 'désactivé'}\n"
            f"EXIF -> DB à synchroniser: {len(exif_to_db) if check_exif_to_db else 'désactivé'}\n"
            f"DB -> EXIF à écrire: {len(db_to_exif) if check_db_to_exif else 'désactivé'}\n"
            f"Dossiers cachés détectés: {len(hidden_dirs) if check_hidden_dirs else 'désactivé'}"
        )
        tk.Label(frame, text=summary, justify="left", anchor="w").pack(fill=tk.X, pady=(0, 8))

        if bool(getattr(self, "_face_worker_paused_for_audit", False)):
            tk.Label(
                frame,
                text="Reconnaissance faciale en pause temporaire pendant la réconciliation.",
                justify="left",
                anchor="w",
                fg="#084298",
                bg="#cfe2ff",
                padx=8,
                pady=4,
                font=("Segoe UI", 9, "bold"),
            ).pack(fill=tk.X, pady=(0, 8))

        force_fix = bool(auto_fix_defects)
        var_remove_missing = tk.BooleanVar(value=(len(missing_ids) > 0 and (force_fix or bool(reconcile_prefs.get("remove_missing", True)))))
        var_remove_duplicates = tk.BooleanVar(value=(cleanup_duplicates_from_scan and len(signature_drop_plan) > 0 and (force_fix or bool(reconcile_prefs.get("remove_duplicates", True)))))
        var_hard_delete_duplicates = tk.BooleanVar(value=False)  # non persistant
        var_copy_unreadable = tk.BooleanVar(value=(len(unreadable_entries) > 0 and (force_fix or bool(reconcile_prefs.get("copy_unreadable", True)))))
        var_hard_delete_unreadable = tk.BooleanVar(value=False)  # non persistant
        var_sync_db_from_exif = tk.BooleanVar(value=(len(exif_to_db) > 0 and (force_fix or bool(reconcile_prefs.get("sync_db_from_exif", True)))))
        var_sync_exif_from_db = tk.BooleanVar(value=(len(db_to_exif) > 0 and (force_fix or bool(reconcile_prefs.get("sync_exif_from_db", False)))))
        var_fill_geo_from_local = tk.BooleanVar(value=(len(local_geo_fill) > 0 and (force_fix or bool(reconcile_prefs.get("fill_geo_from_local", True)))))
        var_fill_geo_exif_write = tk.BooleanVar(value=bool(reconcile_prefs.get("fill_geo_exif_write", False)))
        var_remove_hidden_dirs = tk.BooleanVar(value=(len(hidden_dirs) > 0 and bool(reconcile_prefs.get("remove_hidden_dirs", False))))

        if var_sync_db_from_exif.get() and var_sync_exif_from_db.get():
            var_sync_exif_from_db.set(False)

        cb1 = tk.Checkbutton(frame, text="Supprimer de la DB les fichiers manquants", variable=var_remove_missing)
        cb2 = tk.Checkbutton(
            frame,
            text="Déplacer les doublons vers Visages/Doublons (scan vérifié, conserver 1 fichier/groupe)",
            variable=var_remove_duplicates,
        )
        cb2_hard = tk.Checkbutton(
            frame,
            text="Effacement définitif des doublons (sans corbeille, non persistant)",
            variable=var_hard_delete_duplicates,
        )
        cb_unreadable = tk.Checkbutton(
            frame,
            text="Copier les fichiers illisibles vers _PhotoSelect/Illisibles",
            variable=var_copy_unreadable,
        )
        cb_unreadable_hard = tk.Checkbutton(
            frame,
            text="Supprimer définitivement les originaux illisibles après copie (sans corbeille, non persistant)",
            variable=var_hard_delete_unreadable,
        )
        def _reconcile_sync_direction_exclusive(direction: str) -> None:
            if direction == "exif_to_db" and var_sync_db_from_exif.get():
                var_sync_exif_from_db.set(False)
            elif direction == "db_to_exif" and var_sync_exif_from_db.get():
                var_sync_db_from_exif.set(False)

        cb3 = tk.Checkbutton(
            frame,
            text="Copier GPS du fichier (EXIF) vers la base",
            variable=var_sync_db_from_exif,
            command=lambda: _reconcile_sync_direction_exclusive("exif_to_db"),
        )
        cb4 = tk.Checkbutton(
            frame,
            text="Écrire GPS de la base vers le fichier (EXIF)",
            variable=var_sync_exif_from_db,
            command=lambda: _reconcile_sync_direction_exclusive("db_to_exif"),
        )
        cb5 = tk.Checkbutton(frame, text="Compléter Pays/Région/Ville depuis base locale (offline)", variable=var_fill_geo_from_local)
        cb6 = tk.Checkbutton(frame, text="Écrire aussi l'EXIF GPS/lieu pour ce complément", variable=var_fill_geo_exif_write)
        cb7 = tk.Checkbutton(frame, text="Effacer les dossiers cachés sélectionnés ci-dessous", variable=var_remove_hidden_dirs)
        cb1.pack(anchor="w")
        cb2.pack(anchor="w")
        cb2_hard.pack(anchor="w", padx=(24, 0))
        cb_unreadable.pack(anchor="w")
        cb_unreadable_hard.pack(anchor="w", padx=(24, 0))
        cb3.pack(anchor="w")
        cb4.pack(anchor="w")
        cb5.pack(anchor="w")
        cb6.pack(anchor="w", padx=(24, 0))
        cb7.pack(anchor="w")

        hidden_dir_selections: list[tuple[tk.BooleanVar, dict[str, Any], tk.Checkbutton]] = []
        if hidden_dirs:
            grouped_hidden_dirs: dict[str, list[dict[str, Any]]] = {}
            for item in hidden_dirs:
                group_name = str(item.get("name") or "Sans nom")
                grouped_hidden_dirs.setdefault(group_name, []).append(item)

            hidden_dirs_frame = tk.LabelFrame(
                frame,
                text="Dossiers cachés détectés (groupés par nom)",
                padx=8,
                pady=8,
            )
            hidden_dirs_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
            tk.Label(
                hidden_dirs_frame,
                text="Les dossiers sont pré-cochés par défaut. Décochez ceux à conserver.",
                justify="left",
                anchor="w",
            ).pack(fill=tk.X, pady=(0, 6))

            hidden_dirs_body = tk.Frame(hidden_dirs_frame)
            hidden_dirs_body.pack(fill=tk.BOTH, expand=True)
            hidden_dirs_canvas = tk.Canvas(hidden_dirs_body, highlightthickness=0, borderwidth=0, height=180)
            hidden_dirs_scroll = ttk.Scrollbar(hidden_dirs_body, orient=tk.VERTICAL, command=hidden_dirs_canvas.yview)
            hidden_dirs_canvas.configure(yscrollcommand=hidden_dirs_scroll.set)
            hidden_dirs_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            hidden_dirs_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            hidden_dirs_list = tk.Frame(hidden_dirs_canvas)
            hidden_dirs_window_id = hidden_dirs_canvas.create_window((0, 0), window=hidden_dirs_list, anchor="nw")

            def _on_hidden_dirs_list_configure(_event: tk.Event | None = None) -> None:
                hidden_dirs_canvas.configure(scrollregion=hidden_dirs_canvas.bbox("all"))

            def _on_hidden_dirs_canvas_configure(event: tk.Event) -> None:
                hidden_dirs_canvas.itemconfigure(hidden_dirs_window_id, width=max(1, int(event.width)))

            hidden_dirs_list.bind("<Configure>", _on_hidden_dirs_list_configure)
            hidden_dirs_canvas.bind("<Configure>", _on_hidden_dirs_canvas_configure)

            for group_name in sorted(grouped_hidden_dirs.keys(), key=lambda s: _remove_accents(str(s)).lower()):
                group_items = grouped_hidden_dirs[group_name]
                tk.Label(
                    hidden_dirs_list,
                    text=f"{group_name} ({len(group_items)})",
                    anchor="w",
                    justify="left",
                    fg="#1f2937",
                    font=("Segoe UI", 10, "bold"),
                ).pack(fill=tk.X, pady=(8, 2))
                for item in sorted(group_items, key=lambda d: _remove_accents(str(d.get("path") or "")).lower()):
                    var = tk.BooleanVar(value=True)
                    linked = int(len(item.get("photo_ids") or []))
                    checkbox = tk.Checkbutton(
                        hidden_dirs_list,
                        text=f"{str(item.get('path') or '')} (photos liées: {linked})",
                        variable=var,
                        anchor="w",
                        justify="left",
                        wraplength=760,
                    )
                    checkbox.pack(fill=tk.X, padx=(12, 0))
                    hidden_dir_selections.append((var, item, checkbox))

        if (not check_missing) or (not missing_ids):
            cb1.config(state="disabled")
        if (not cleanup_duplicates_from_scan):
            cb2.config(state="disabled")
            cb2_hard.config(state="disabled")
        elif (not signature_drop_plan):
            cb2.config(state="disabled")
        if (not check_unreadable_viewer) or (not unreadable_entries):
            cb_unreadable.config(state="disabled")
            cb_unreadable_hard.config(state="disabled")
        if (not check_exif_to_db) or (not exif_to_db):
            cb3.config(state="disabled")
        if (not check_db_to_exif) or (not db_to_exif):
            cb4.config(state="disabled")
        if (not check_gps_without_place) or (not local_geo_fill):
            cb5.config(state="disabled")
            cb6.config(state="disabled")
        elif not var_fill_geo_from_local.get():
            cb6.config(state="disabled")
        if (not check_hidden_dirs) or (not hidden_dirs):
            cb7.config(state="disabled")

        def _toggle_local_exif_state() -> None:
            if not local_geo_fill:
                cb6.config(state="disabled")
                return
            cb6.config(state="normal" if var_fill_geo_from_local.get() else "disabled")

        def _toggle_hard_delete_duplicates_state() -> None:
            if (not cleanup_duplicates_from_scan):
                cb2_hard.config(state="disabled")
                return
            cb2_hard.config(state="normal")

        def _toggle_hard_delete_unreadable_state() -> None:
            if (not check_unreadable_viewer) or (not unreadable_entries) or (not var_copy_unreadable.get()):
                cb_unreadable_hard.config(state="disabled")
                return
            cb_unreadable_hard.config(state="normal")

        def _toggle_hidden_dirs_selection_state() -> None:
            state = "normal" if (var_remove_hidden_dirs.get() and hidden_dir_selections) else "disabled"
            for _, _, checkbox in hidden_dir_selections:
                checkbox.config(state=state)

        cb5.config(command=_toggle_local_exif_state)
        cb2.config(command=_toggle_hard_delete_duplicates_state)
        cb_unreadable.config(command=_toggle_hard_delete_unreadable_state)
        cb7.config(command=_toggle_hidden_dirs_selection_state)
        _toggle_hard_delete_duplicates_state()
        _toggle_hard_delete_unreadable_state()
        _toggle_hidden_dirs_selection_state()

        result = {"ok": False}

        def _confirm(_event: tk.Event | None = None) -> None:
            self._save_audit_reconcile_preferences({
                "remove_missing": bool(var_remove_missing.get()),
                "remove_duplicates": bool(var_remove_duplicates.get()),
                "sync_db_from_exif": bool(var_sync_db_from_exif.get()),
                "sync_exif_from_db": bool(var_sync_exif_from_db.get()),
                "fill_geo_from_local": bool(var_fill_geo_from_local.get()),
                "fill_geo_exif_write": bool(var_fill_geo_exif_write.get()),
                "remove_hidden_dirs": bool(var_remove_hidden_dirs.get()),
                "copy_unreadable": bool(var_copy_unreadable.get()),
            })
            result["ok"] = True
            dlg.destroy()

        def _cancel(_event: tk.Event | None = None) -> None:
            dlg.destroy()

        btns = tk.Frame(frame)
        btns.pack(fill=tk.X, pady=(10, 0))
        tk.Button(btns, text="Appliquer", width=14, command=_confirm, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btns, text="Fermer", width=14, command=_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT)

        dlg.bind("<Return>", _confirm)
        dlg.bind("<Escape>", _cancel)
        self._center_dialog_on_main(dlg)
        dlg.after(10, lambda: self._center_dialog_on_main(dlg))
        if auto_fix_defects:
            _confirm()
        else:
            self.wait_window(dlg)

        if not result["ok"]:
            self._set_last_integrity_audit_stats(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | Audit analysé: {len(rows)} | Corrections non appliquées"
            )
            self._audit_event("integrity_audit", {
                "rows": len(rows),
                "missing": len(missing_ids),
                "duplicates": len(duplicate_ids),
                "unreadable": len(unreadable_entries),
                "signature_duplicates": len(signature_duplicate_ids),
                "signature_groups": signature_group_count,
                "local_geo_candidates": len(local_geo_fill),
                "options": options,
                "exif_to_db": len(exif_to_db),
                "db_to_exif": len(db_to_exif),
                "reconciled": False,
            })
            self._end_long_action()
            self._resume_face_worker_after_audit()
            return

        removed_missing = 0
        removed_duplicates = 0
        moved_duplicates = 0
        hard_deleted_duplicates = 0
        hard_delete_duplicates_selected = bool(var_hard_delete_duplicates.get())
        copied_unreadable = 0
        hard_deleted_unreadable = 0
        removed_unreadable_db = 0
        failed_unreadable_copies = 0
        failed_unreadable_deletes = 0
        duplicates_dir_for_purge = self._get_faces_duplicates_dir() if hard_delete_duplicates_selected else None
        duplicates_staging_purge_count = 0
        if duplicates_dir_for_purge and duplicates_dir_for_purge.exists():
            try:
                duplicates_staging_purge_count = sum(1 for _ in duplicates_dir_for_purge.iterdir())
            except Exception:
                duplicates_staging_purge_count = 0
        trashed_existing_duplicates_staging = 0
        failed_duplicate_moves = 0
        failed_duplicate_deletes = 0
        updated_db = 0
        updated_exif = 0
        failed_exif = 0
        updated_geo_local = 0
        updated_geo_local_exif = 0
        failed_geo_local_exif = 0
        skipped_geo_local_exif = 0

        selected_hidden_dirs: list[dict[str, Any]] = []
        if var_remove_hidden_dirs.get() and hidden_dir_selections:
            selected_hidden_dirs = [item for var, item, _checkbox in hidden_dir_selections if bool(var.get())]
        removed_hidden_dirs = 0
        removed_hidden_dir_photos = 0
        failed_hidden_dirs = 0

        selected_unreadable_ids = {
            int(item.get("photo_id") or 0)
            for item in unreadable_entries
            if int(item.get("photo_id") or 0) > 0
        } if var_copy_unreadable.get() else set()
        effective_signature_drop_plan = [
            (photo_id, source_path)
            for photo_id, source_path in signature_drop_plan
            if photo_id not in selected_unreadable_ids
        ]

        reconcile_total = (
            (len(missing_ids) if var_remove_missing.get() else 0)
            + (duplicates_staging_purge_count if hard_delete_duplicates_selected else 0)
            + (len(unreadable_entries) if var_copy_unreadable.get() else 0)
            + (len(effective_signature_drop_plan) if var_remove_duplicates.get() else 0)
            + (len(exif_to_db) if var_sync_db_from_exif.get() else 0)
            + (len(local_geo_fill) if var_fill_geo_from_local.get() else 0)
            + (len(db_to_exif) if var_sync_exif_from_db.get() else 0)
            + (len(selected_hidden_dirs) if var_remove_hidden_dirs.get() else 0)
        )
        processed = 0
        if reconcile_total > 0:
            self._begin_photo_progress(reconcile_total, "Réconciliation")

        if var_remove_missing.get():
            for photo_id in missing_ids:
                if self._stop_event.is_set():
                    break
                self.db.delete_photo(photo_id)
                removed_missing += 1
                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if not self._stop_event.is_set() and var_copy_unreadable.get():
            unreadable_dir = self._ensure_unreadable_audit_dir()
            if unreadable_dir is None:
                self._end_long_action()
                messagebox.showerror(
                    "Audit",
                    "Impossible de créer le dossier _PhotoSelect/Illisibles. Vérifiez le dossier source puis relancez l'audit.",
                )
                self._resume_face_worker_after_audit()
                return

            for item in unreadable_entries:
                if self._stop_event.is_set():
                    break
                photo_id = int(item.get("photo_id") or 0)
                source_path = Path(str(item.get("path") or "").strip())
                copy_ok = False
                try:
                    if photo_id > 0 and source_path.exists():
                        target_path = self._build_unreadable_copy_target(source_path, unreadable_dir)
                        shutil.copy2(source_path, target_path)
                        copied_unreadable += 1
                        copy_ok = True
                    else:
                        failed_unreadable_copies += 1
                except Exception:
                    failed_unreadable_copies += 1

                if copy_ok and var_hard_delete_unreadable.get() and photo_id > 0:
                    try:
                        if source_path.exists() and not self._delete_path_permanently(source_path):
                            raise OSError(f"Suppression définitive impossible: {source_path}")
                        self.db.delete_photo(photo_id)
                        hard_deleted_unreadable += 1
                        removed_unreadable_db += 1
                    except Exception:
                        failed_unreadable_deletes += 1

                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if not self._stop_event.is_set() and hard_delete_duplicates_selected:
            duplicates_dir = duplicates_dir_for_purge
            if duplicates_dir and duplicates_dir.exists():
                try:
                    for child in duplicates_dir.iterdir():
                        if self._stop_event.is_set():
                            break
                        if self._delete_path_permanently(child):
                            trashed_existing_duplicates_staging += 1
                        else:
                            failed_duplicate_deletes += 1
                        processed += 1
                        self._tick_photo_progress(processed, reconcile_total, "Réconciliation")
                except Exception:
                    pass

        if not self._stop_event.is_set() and var_remove_duplicates.get():
            hard_delete_duplicates = bool(var_hard_delete_duplicates.get())
            duplicates_dir = self._get_faces_duplicates_dir()
            if not hard_delete_duplicates:
                duplicates_dir = self._ensure_faces_duplicates_dir()
                if duplicates_dir is None:
                    self._end_long_action()
                    messagebox.showerror(
                        "Audit",
                        "Impossible de créer le dossier Visages/Doublons. Vérifiez le dossier Visages puis relancez l'audit.",
                    )
                    self._resume_face_worker_after_audit()
                    return

            for photo_id, source_path in effective_signature_drop_plan:
                if self._stop_event.is_set():
                    break
                try:
                    if hard_delete_duplicates:
                        if source_path.exists():
                            if not self._delete_path_permanently(source_path):
                                raise OSError(f"Suppression définitive impossible: {source_path}")
                        hard_deleted_duplicates += 1
                    else:
                        target_name = self._build_incremented_duplicate_name(duplicates_dir, source_path.name)
                        target_path = duplicates_dir / target_name
                        shutil.move(str(source_path), str(target_path))
                        moved_duplicates += 1
                    self.db.delete_photo(photo_id)
                    removed_duplicates += 1
                except Exception:
                    if hard_delete_duplicates:
                        failed_duplicate_deletes += 1
                    else:
                        failed_duplicate_moves += 1
                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if not self._stop_event.is_set() and var_sync_db_from_exif.get():
            for photo_id, lat, lon in exif_to_db:
                if self._stop_event.is_set():
                    break
                row = self._get_photo_by_id(photo_id)
                if not row:
                    continue
                self.db.update_photo_location(
                    photo_id,
                    lat,
                    lon,
                    str(row.get("country") or ""),
                    str(row.get("region") or ""),
                    str(row.get("city") or ""),
                )
                updated_db += 1
                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if not self._stop_event.is_set() and var_fill_geo_from_local.get():
            for photo_id, lat, lon, country, region, city, path in local_geo_fill:
                if self._stop_event.is_set():
                    break
                self.db.update_photo_location(photo_id, lat, lon, country, region, city)
                updated_geo_local += 1
                if var_fill_geo_exif_write.get():
                    if path.exists() and self._supports_exif_write(path):
                        if self._update_exif_with_viewer_sync(path, lat, lon, country, region, city):
                            updated_geo_local_exif += 1
                        else:
                            failed_geo_local_exif += 1
                    else:
                        skipped_geo_local_exif += 1
                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if not self._stop_event.is_set() and var_sync_exif_from_db.get():
            for photo_id, lat, lon, country, region, city, path in db_to_exif:
                if self._stop_event.is_set():
                    break
                if self._update_exif_with_viewer_sync(path, lat, lon, country, region, city):
                    updated_exif += 1
                else:
                    failed_exif += 1
                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if not self._stop_event.is_set() and var_remove_hidden_dirs.get():
            deleted_photo_ids: set[int] = set()
            for item in selected_hidden_dirs:
                if self._stop_event.is_set():
                    break
                dir_path = Path(str(item.get("path") or "").strip())
                try:
                    if dir_path.exists() and not self._delete_path_permanently(dir_path):
                        raise OSError(f"Suppression définitive impossible: {dir_path}")
                    removed_hidden_dirs += 1
                    for pid in [int(v) for v in (item.get("photo_ids") or []) if int(v) > 0]:
                        if pid in deleted_photo_ids:
                            continue
                        self.db.delete_photo(pid)
                        deleted_photo_ids.add(pid)
                    removed_hidden_dir_photos = len(deleted_photo_ids)
                except Exception:
                    failed_hidden_dirs += 1
                processed += 1
                self._tick_photo_progress(processed, reconcile_total, "Réconciliation")

        if reconcile_total > 0 and not self._stop_event.is_set():
            self._end_photo_progress(reconcile_total, "Réconciliation")

        if self.db and not self._stop_event.is_set():
            self.db.build_duplicate_index()
            self._invalidate_duplicate_cache()
            self._refresh_duplicate_cache(force=True)

        self._load_data_to_treeview()
        self._update_statistics()
        details = {
            "rows": len(rows),
            "removed_missing": removed_missing,
            "removed_duplicates": removed_duplicates,
            "moved_duplicates": moved_duplicates,
            "copied_unreadable": copied_unreadable,
            "hard_deleted_unreadable": hard_deleted_unreadable,
            "removed_unreadable_db": removed_unreadable_db,
            "failed_unreadable_copies": failed_unreadable_copies,
            "failed_unreadable_deletes": failed_unreadable_deletes,
            "hard_deleted_duplicates": hard_deleted_duplicates,
            "trashed_existing_duplicates_staging": trashed_existing_duplicates_staging,
            "failed_duplicate_moves": failed_duplicate_moves,
            "failed_duplicate_deletes": failed_duplicate_deletes,
            "signature_duplicates": len(signature_duplicate_ids),
            "signature_groups": signature_group_count_plan,
            "updated_db_from_exif": updated_db,
            "updated_geo_local": updated_geo_local,
            "updated_geo_local_exif": updated_geo_local_exif,
            "failed_geo_local_exif": failed_geo_local_exif,
            "skipped_geo_local_exif": skipped_geo_local_exif,
            "updated_exif_from_db": updated_exif,
            "failed_exif": failed_exif,
            "removed_hidden_dirs": removed_hidden_dirs,
            "removed_hidden_dir_photos": removed_hidden_dir_photos,
            "failed_hidden_dirs": failed_hidden_dirs,
            "cancelled": bool(self._stop_event.is_set()),
        }
        self._audit_event("integrity_reconcile", details)
        self._set_last_integrity_audit_stats(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | Analysées:{len(rows)} | "
            f"corrigées manquants={removed_missing}, illisibles={copied_unreadable}, doublons={removed_duplicates}, dossiers_caches={removed_hidden_dirs}, gps_local={updated_geo_local}, exif_db={updated_db}, db_exif={updated_exif}"
        )
        self._end_long_action()

        if self._stop_event.is_set():
            messagebox.showwarning("Réconciliation interrompue", "Opération interrompue par l'utilisateur.")
            self._resume_face_worker_after_audit()
            return

        duplicate_lines: list[str] = []
        duplicate_lines.append(f"Doublons signature traités (DB): {removed_duplicates}")
        if var_remove_duplicates.get() and not var_hard_delete_duplicates.get():
            duplicate_lines.append(f"Doublons déplacés vers Visages/Doublons: {moved_duplicates}")
            duplicate_lines.append(f"Échecs déplacement doublons: {failed_duplicate_moves}")
        if var_hard_delete_duplicates.get():
            duplicate_lines.append(f"Doublons supprimés définitivement: {hard_deleted_duplicates}")
            duplicate_lines.append(f"Éléments existants supprimés définitivement depuis Visages/Doublons: {trashed_existing_duplicates_staging}")
            duplicate_lines.append(f"Échecs suppression définitive doublons: {failed_duplicate_deletes}")

        unreadable_lines: list[str] = []
        unreadable_lines.append(f"Fichiers illisibles détectés: {len(unreadable_entries)}")
        unreadable_lines.append(f"Illisibles copiés vers _PhotoSelect/Illisibles: {copied_unreadable}")
        unreadable_lines.append(f"Échecs copie illisibles: {failed_unreadable_copies}")
        unreadable_lines.append(f"Illisibles supprimés définitivement: {hard_deleted_unreadable}")
        unreadable_lines.append(f"Entrées DB supprimées pour illisibles: {removed_unreadable_db}")
        unreadable_lines.append(f"Échecs suppression définitive illisibles: {failed_unreadable_deletes}")

        messagebox.showinfo(
            "Réconciliation terminée",
            f"Fichiers manquants supprimés: {removed_missing}\n"
            + "\n".join(unreadable_lines)
            + "\n"
            + "\n".join(duplicate_lines)
            + "\n"
            f"Dossiers cachés supprimés définitivement: {removed_hidden_dirs}\n"
            f"Photos DB supprimées via dossiers cachés: {removed_hidden_dir_photos}\n"
            f"Échecs suppression dossiers cachés: {failed_hidden_dirs}\n"
            f"DB synchronisée depuis EXIF: {updated_db}\n"
            f"Lieu complété depuis base locale: {updated_geo_local}\n"
            f"EXIF écrits (complément local): {updated_geo_local_exif}\n"
            f"Échecs EXIF (complément local): {failed_geo_local_exif}\n"
            f"EXIF non supporté (complément local): {skipped_geo_local_exif}\n"
            f"EXIF synchronisé depuis DB: {updated_exif}\n"
            f"Échecs EXIF: {failed_exif}"
        )
        self._resume_face_worker_after_audit()

    def _run_album_integrity_audit(self) -> None:
        """Audit d'intégrité dédié au mode Sélection (index + lisibilité fichiers)."""
        root = self._normalize_album_root()
        if root is None:
            messagebox.showinfo("Audit sélection", "Sélectionnez un dossier de sélection valide.")
            return

        indexed_rows = self._sync_album_index()
        files = [Path(str(r.get("filepath") or "")) for r in indexed_rows]
        total = len(files)
        if total == 0:
            messagebox.showinfo("Audit sélection", "Aucune image de sélection à auditer.")
            return

        unreadable: list[str] = []
        missing: list[str] = []
        final_rows = indexed_rows
        self._begin_long_action()
        self._begin_photo_progress(total, "Audit sélection")

        try:
            for idx, path in enumerate(files, start=1):
                if self._stop_event.is_set():
                    break
                if not path.exists():
                    missing.append(str(path))
                    self._tick_photo_progress(idx, total, "Audit sélection")
                    continue
                try:
                    with Image.open(path) as img:
                        img.verify()
                except Exception:
                    unreadable.append(str(path))
                self._tick_photo_progress(idx, total, "Audit sélection")

            if not self._stop_event.is_set():
                self._end_photo_progress(total, "Audit sélection")

            final_rows = self._sync_album_index()
            self._load_data_to_treeview()
            self._update_statistics()

            self._audit_event("integrity_audit_album", {
                "album_root": str(root),
                "indexed": len(final_rows),
                "missing": len(missing),
                "unreadable": len(unreadable),
                "cancelled": bool(self._stop_event.is_set()),
            })
            if not self._stop_event.is_set():
                self._set_album_audit_state(root, len(unreadable), len(missing))
        finally:
            self._end_long_action()

        if self._stop_event.is_set():
            messagebox.showwarning("Audit sélection", "Audit sélection interrompu.")
            return

        messagebox.showinfo(
            "Audit sélection terminé",
            f"Dossier de sélection: {root}\n"
            f"Fichiers indexés: {len(final_rows)}\n"
            f"Fichiers manquants: {len(missing)}\n"
            f"Images illisibles: {len(unreadable)}"
        )

    def _build_ui(self) -> None:
        # --- DOSSIERS ---
        frame_config = ttk.Frame(self)
        frame_config.pack(fill=tk.X, padx=(10, 0), pady=(10, 10))
        logo_column_width = 0

        # --- LOGO MCh ---
        try:
            # Recherche du logo dans plusieurs emplacements possibles
            logo_path = None
            search_paths = [
                get_resource_path("src/photoselect/assets/mch_logo.png"),
                Path(__file__).parent / "assets" / "mch_logo.png",
                Path("src/photoselect/assets/mch_logo.png")
            ]
            for p in search_paths:
                if p.exists():
                    logo_path = p
                    break
            
            if logo_path:
                img = Image.open(logo_path)
                # Agrandissement significatif
                img.thumbnail((250, 250), Image.Resampling.LANCZOS)
                self.logo_photo = ImageTk.PhotoImage(img)
                self._logo_default_photo = self.logo_photo
                self._logo_slot_size = (self.logo_photo.width(), self.logo_photo.height())
                logo_column_width = self.logo_photo.width() + 20
                logger.info("Logo agrandi chargé.")
            else:
                logger.warning("Fichier logo non trouvé dans les chemins : %s", search_paths)
        except Exception as e:
            logger.error("Erreur lors de l'insertion du logo : %s", e)

        
        # Style bouton uniforme (même rendu que "Parcourir")
        uniform_btn_style = {
            "bg": "lightgray",
            "fg": "black",
            "activebackground": "#d9d9d9",
            "activeforeground": "black",
            "relief": "raised",
            "bd": 1,
        }

        frame_selectors_host = tk.Frame(frame_config, bg="#f0f2f5")
        frame_selectors_host.grid(row=0, column=0, columnspan=3, rowspan=3, sticky="nw", pady=(4, 0))
        frame_selectors = tk.Frame(frame_selectors_host, bg="#f0f2f5")
        frame_selectors.pack(anchor="nw")

        # Entrée
        lbl_in = tk.Label(frame_selectors, text="Dossier Source :", cursor="hand2", fg="#3498db", anchor="w", bg="#f0f2f5")
        lbl_in.grid(row=0, column=0, padx=(5, 2), pady=(0, 3), sticky="w")
        lbl_in.bind("<Button-1>", lambda e: self._open_folder_from_var(self.var_in))
        
        self.var_in = tk.StringVar(value=self._normalize_saved_path(str(self.config_data.get("last_in", ""))))
        self.selector_in = ttk.Combobox(
            frame_selectors,
            textvariable=self.var_in,
            values=self._saved_source_paths,
            state="readonly",
            width=33,
        )
        self.selector_in.grid(row=0, column=1, pady=(0, 3), sticky="w")
        self.selector_in.bind("<<ComboboxSelected>>", self._on_source_selector_changed)
        tk.Button(frame_selectors, text="Parcourir", width=12, command=self._ask_in, **uniform_btn_style).grid(row=0, column=2, padx=(0, 2), pady=(0, 3), sticky="w")


        # Recherche
        self.lbl_search = tk.Label(frame_selectors, text="Rechercher :", anchor="w", bg="#f0f2f5")
        self.lbl_search.grid(row=3, column=0, padx=(5, 2), pady=0, sticky="w")
        self.entry_search = tk.Entry(frame_selectors, textvariable=self.var_search, width=35)
        self.entry_search.grid(row=3, column=1, pady=0, sticky="w")
        self.var_search.trace_add("write", lambda *_: self._on_search_changed())
        self.btn_search_clear = tk.Button(frame_selectors, text="Effacer", width=12, command=lambda: self.var_search.set(""), **uniform_btn_style)
        self.btn_search_clear.grid(row=3, column=2, padx=(0, 2), pady=0, sticky="w")

        # Raccourcis pays (cliquables)
        self.lbl_country_quick = tk.Label(frame_selectors, text="Pays :", anchor="w", bg="#f0f2f5")
        self.lbl_country_quick.grid(row=4, column=0, padx=(5, 2), pady=(4, 0), sticky="nw")
        country_width = int(self.entry_search.cget("width")) if hasattr(self, "entry_search") else 35
        self.country_quick_text = tk.Text(
            frame_selectors,
            width=country_width,
            height=4,
            wrap="word",
            relief="solid",
            bd=1,
            bg="white",
            fg="#2c3e50",
            font=self.entry_search.cget("font"),
            cursor="arrow",
        )
        self.country_quick_text.grid(row=4, column=1, pady=(4, 0), sticky="w")
        self.country_quick_text.configure(state="disabled")
        self._set_country_quick_filters([])

        # Boutons Action
        frame_btns_host = tk.Frame(frame_config, bg="#f0f2f5")
        frame_btns_host.grid(row=0, column=3, rowspan=3, padx=15, pady=0, sticky="nw")
        frame_btns = tk.Frame(frame_btns_host, bg="#f0f2f5")
        frame_btns.pack(anchor="nw")
        album_sidebar_width = tkfont.nametofont("TkDefaultFont").measure("M" * 25) + 34

        # Colonnes dediees: logo puis albums puis planisphere.
        frame_config.grid_columnconfigure(4, minsize=max(20, logo_column_width))
        frame_config.grid_columnconfigure(5, minsize=max(220, album_sidebar_width), weight=0)
        frame_config.grid_columnconfigure(6, minsize=300, weight=1)
        # Reserve vertical slack above bottom controls to keep them glued above the table.
        frame_config.grid_rowconfigure(4, weight=1)
        frame_config.grid_rowconfigure(5, weight=0)

        frame_virtual_albums = tk.Frame(frame_config, bg="#eef3f8", width=max(220, album_sidebar_width), bd=1, relief="solid")
        frame_virtual_albums.grid(row=0, column=5, rowspan=6, padx=(0, 10), pady=(0, 0), sticky="ns")
        frame_virtual_albums.grid_propagate(False)
        frame_virtual_albums.grid_columnconfigure(0, weight=1)
        frame_virtual_albums.grid_rowconfigure(1, weight=1)

        self.var_virtual_album_header = tk.StringVar(value="Albums (0)")
        tk.Label(
            frame_virtual_albums,
            textvariable=self.var_virtual_album_header,
            bg="#eef3f8",
            fg="#1f2937",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))

        frame_virtual_album_list = tk.Frame(frame_virtual_albums, bg="#eef3f8")
        frame_virtual_album_list.grid(row=1, column=0, sticky="nsew", padx=(8, 6), pady=(0, 8))
        frame_virtual_album_list.grid_columnconfigure(0, weight=1)
        frame_virtual_album_list.grid_rowconfigure(0, weight=1)

        self.list_virtual_albums = tk.Listbox(
            frame_virtual_album_list,
            width=25,
            exportselection=False,
            activestyle="none",
            takefocus=1,
            state="disabled",
            bg="white",
            fg="#1f2937",
            relief="flat",
            highlightthickness=0,
            selectbackground="#bfdbfe",
            selectforeground="#000000",
        )
        self.list_virtual_albums.grid(row=0, column=0, sticky="nsew")
        self.list_virtual_albums.bind("<<ListboxSelect>>", self._on_virtual_album_listbox_select)
        self.list_virtual_albums.bind("<MouseWheel>", self._on_virtual_album_list_mousewheel)
        self.list_virtual_albums.bind("<Button-4>", self._on_virtual_album_list_mousewheel)
        self.list_virtual_albums.bind("<Button-5>", self._on_virtual_album_list_mousewheel)
        self.list_virtual_albums.bind("<ButtonRelease-3>", self._show_virtual_album_sidebar_ctx_menu)
        self.list_virtual_albums.bind("<Delete>", self._on_virtual_album_list_delete_request)
        self.list_virtual_albums.bind("<F2>", self._on_virtual_album_list_rename_request)

        self.scroll_virtual_albums = tk.Scrollbar(frame_virtual_album_list, orient="vertical", command=self.list_virtual_albums.yview)
        self.scroll_virtual_albums.grid(row=0, column=1, sticky="ns")
        self.list_virtual_albums.configure(yscrollcommand=self.scroll_virtual_albums.set)

        self._virtual_album_ctx_menu = tk.Menu(self, tearoff=0)
        self._virtual_album_ctx_menu.add_command(label="Renommer l'album (F2)", command=self._rename_selected_virtual_album)
        self._virtual_album_ctx_menu.add_command(label="Supprimer l'album", command=self._delete_selected_virtual_album)

        frame_map = tk.Frame(frame_config, bg="#e9eef5", width=358, bd=1, relief="solid")
        frame_map.grid(row=0, column=6, rowspan=6, padx=(0, 16), pady=(0, 0), sticky="nsew")
        frame_map.grid_propagate(False)
        frame_map.grid_columnconfigure(0, weight=1)
        frame_map.grid_rowconfigure(1, weight=1)

        tk.Label(
            frame_map,
            text="Planisphere GPS",
            bg="#e9eef5",
            fg="#1f2937",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

        self.map_canvas = tk.Canvas(
            frame_map,
            bg="#cfe9ff",
            highlightthickness=0,
            width=1,
            height=1,
            cursor="crosshair",
        )
        self.map_canvas.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 0))
        self.map_canvas.bind("<Configure>", self._on_map_canvas_configure)
        self.map_canvas.bind("<ButtonPress-1>", self._on_map_click_stop_slideshow, add="+")
        self.map_canvas.bind("<ButtonPress-1>", self._on_map_mouse_down)
        self.map_canvas.bind("<B1-Motion>", self._on_map_mouse_drag)
        self.map_canvas.bind("<ButtonRelease-1>", self._on_map_mouse_up)
        self.map_canvas.bind("<Double-Button-1>", self._on_map_reset_request)
        self.map_canvas.bind("<MouseWheel>", self._on_map_mousewheel)
        self.map_canvas.bind("<Button-4>", self._on_map_mousewheel)
        self.map_canvas.bind("<Button-5>", self._on_map_mousewheel)

        self.var_map_status = tk.StringVar(value="Points visibles : 0")
        tk.Label(
            frame_map,
            textvariable=self.var_map_status,
            bg="#e9eef5",
            fg="#334155",
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 3))

        if logo_column_width > 0 and hasattr(self, "logo_photo"):
            frame_logo = tk.Frame(frame_config, bg="#f0f2f5", width=logo_column_width)
            frame_logo.grid(row=0, column=4, rowspan=6, padx=(0, 8), pady=(0, 0), sticky="ns")
            frame_logo.grid_propagate(False)
            self._logo_label = tk.Label(frame_logo, image=self.logo_photo, borderwidth=0, highlightthickness=0, bg="#f0f2f5")
            self._logo_label.image = self.logo_photo
            self._logo_label.pack(side=tk.TOP, padx=10, pady=(4, 0))
            self._logo_label.bind("<Button-1>", self._on_map_reset_request)
            self._logo_label.bind("<Button-3>", self._show_logo_photo_ctx_menu)
            self._logo_label.bind("<Enter>", self._on_logo_hover_enter)
            self._logo_label.bind("<Motion>", self._on_logo_hover_motion)
            self._logo_label.bind("<Leave>", self._on_logo_hover_leave)

        for col in range(3):
            frame_btns.columnconfigure(col, weight=1)

        self.btn_open = tk.Button(frame_btns, text="Scanner / Reprendre", command=lambda: self._start_scan(resume=True), **uniform_btn_style)
        self.btn_open.grid(row=0, column=0, padx=2, pady=1, sticky="ew")

        self.btn_refresh = tk.Button(frame_btns, text="Vider / Re-scanner", command=lambda: self._start_scan(resume=False), **uniform_btn_style)
        self.btn_refresh.grid(row=1, column=0, padx=2, pady=1, sticky="ew")

        self.btn_purge = tk.Button(frame_btns, text="Purger data", command=self._purge_data, **uniform_btn_style)
        self.btn_purge.grid(row=2, column=0, padx=2, pady=1, sticky="ew")

        self.btn_theme_classify = tk.Button(
            frame_btns,
            text="Classifier themes IA",
            command=self._start_theme_album_classification,
            **uniform_btn_style,
        )
        self.btn_theme_classify.grid(row=3, column=0, padx=2, pady=1, sticky="ew")

        self.btn_rename = tk.Button(frame_btns, text="Renommer originaux", command=self._start_rename, **uniform_btn_style)
        self.btn_rename.grid(row=0, column=1, padx=2, pady=1, sticky="ew")

        self.btn_normalize_geo = tk.Button(frame_btns, text="Normaliser lieux", command=self._normalize_geo_labels, **uniform_btn_style)
        self.btn_normalize_geo.grid(row=1, column=1, padx=2, pady=1, sticky="ew")

        self.btn_reindex_album = tk.Button(frame_btns, text="Réindexer sélection", command=self._reindex_album_now, **uniform_btn_style)
        self.btn_reindex_album.grid(row=2, column=1, padx=2, pady=1, sticky="ew")

        self.btn_face_relaunch = tk.Button(frame_btns, text="Relancer faciale", command=self._relaunch_face_worker_manually, **uniform_btn_style)
        self.btn_face_relaunch.grid(row=3, column=1, padx=2, pady=1, sticky="ew")


        self.btn_integrity = tk.Button(frame_btns, text="Audit intégrité", command=self._run_integrity_audit_and_reconcile, **uniform_btn_style)
        self.btn_integrity.grid(row=0, column=2, padx=2, pady=1, sticky="ew")

        self.btn_export_log = tk.Button(frame_btns, text="Exporter journal", command=self._export_operations_log, **uniform_btn_style)
        self.btn_export_log.grid(row=1, column=2, padx=2, pady=1, sticky="ew")

        self.btn_export_stats = tk.Button(frame_btns, text="Exporter statistique", command=self._export_statistics_markdown, **uniform_btn_style)
        self.btn_export_stats.grid(row=2, column=2, padx=2, pady=1, sticky="ew")

        self.btn_stop = tk.Button(frame_btns, text="Stopper l'action", command=self._stop_action, state="disabled", **uniform_btn_style)
        self.btn_stop.grid(row=3, column=2, padx=2, pady=1, sticky="ew")

        # Visionneuse / Affichage + Géo-localisation regroupés en bas, juste au-dessus du tableau.
        frame_bottom_controls = tk.Frame(frame_config, bg="#f0f2f5")
        frame_bottom_controls.grid(row=5, column=0, columnspan=11, pady=0, sticky="sw")

        frame_face_auth = tk.Frame(frame_bottom_controls, bg="#f0f2f5")
        frame_face_auth.pack(side=tk.TOP, anchor="w", pady=0)
        self.chk_face_recognition = tk.Checkbutton(
            frame_face_auth,
            text="Autorisation de reconnaissance faciale",
            variable=self.var_face_recognition_enabled,
            bg="#f0f2f5",
            activebackground="#f0f2f5",
            command=self._on_face_recognition_toggle,
        )
        self.chk_face_recognition.pack(side=tk.LEFT, padx=5)

        self.chk_auto_sync = tk.Checkbutton(
            frame_face_auth,
            text="Sync disque temps réel",
            variable=self.var_auto_sync_enabled,
            bg="#f0f2f5",
            activebackground="#f0f2f5",
            command=self._on_auto_sync_toggle,
        )
        self.chk_auto_sync.pack(side=tk.LEFT, padx=(10, 5))

        tk.Label(frame_face_auth, text="Seuil :", bg="#f0f2f5").pack(side=tk.LEFT, padx=(12, 4))
        default_match_threshold = float(self.config_data.get("face_match_threshold", 0.48) or 0.48)
        default_match_threshold = max(0.00, min(0.95, default_match_threshold))
        self.var_face_match_threshold = tk.DoubleVar(value=default_match_threshold)
        self.spn_face_match_threshold = tk.Spinbox(
            frame_face_auth,
            from_=0.00,
            to=0.95,
            increment=0.01,
            width=5,
            format="%.2f",
            textvariable=self.var_face_match_threshold,
            command=self._on_face_match_threshold_changed,
        )
        self.spn_face_match_threshold.pack(side=tk.LEFT, padx=(0, 2))
        tk.Label(frame_face_auth, text="(Bas = strict)", bg="#f0f2f5").pack(side=tk.LEFT, padx=(0, 8))
        self.spn_face_match_threshold.bind("<FocusOut>", self._on_face_match_threshold_changed)
        self.spn_face_match_threshold.bind("<Return>", self._on_face_match_threshold_changed)

        self.btn_face_restart = tk.Button(
            frame_face_auth,
            text="Reset",
            command=self._restart_face_index_from_scratch,
            **uniform_btn_style,
        )
        self.btn_face_restart.pack(side=tk.LEFT, padx=(8, 2))

        frame_viewer = tk.Frame(frame_bottom_controls, bg="#f0f2f5")
        frame_viewer.pack(side=tk.TOP, anchor="w", pady=0)
        tk.Label(frame_viewer, text="Visionneuse :", bg="#f0f2f5").pack(side=tk.LEFT, padx=5)
        self.var_viewer_mode = tk.StringVar(value=self.config_data.get("viewer_mode", "photo"))
        tk.Radiobutton(frame_viewer, text="Photo", variable=self.var_viewer_mode, value="photo", bg="#f0f2f5", command=self._on_viewer_mode_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(frame_viewer, text="Mosaïc", variable=self.var_viewer_mode, value="multiple", bg="#f0f2f5", command=self._on_viewer_mode_changed).pack(side=tk.LEFT, padx=5)
        tk.Label(frame_viewer, text="    Affichage :", bg="#f0f2f5").pack(side=tk.LEFT, padx=(20, 5))
        self.var_viewer_scope = tk.StringVar(value=self.var_viewer_scope.get() if hasattr(self, 'var_viewer_scope') else self.config_data.get("viewer_scope", "folder"))
        tk.Radiobutton(frame_viewer, text="Par dossier", variable=self.var_viewer_scope, value="folder", bg="#f0f2f5", command=self._on_viewer_scope_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(frame_viewer, text="Continu", variable=self.var_viewer_scope, value="continuous", bg="#f0f2f5", command=self._on_viewer_scope_changed).pack(side=tk.LEFT, padx=5)
        self.rb_scope_album = tk.Radiobutton(
            frame_viewer,
            text="Sélection",
            variable=self.var_viewer_scope,
            value="album",
            bg="#f0f2f5",
            command=self._on_viewer_scope_changed,
        )
        tk.Radiobutton(frame_viewer, text="Albums", variable=self.var_viewer_scope, value="virtual_album", bg="#f0f2f5", command=self._on_viewer_scope_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(frame_viewer, text="Doublons", variable=self.var_viewer_scope, value="duplicates", bg="#f0f2f5", command=self._on_viewer_scope_changed).pack(side=tk.LEFT, padx=5)
        self.rb_scope_faces = tk.Radiobutton(frame_viewer, text="Visages", variable=self.var_viewer_scope, value="faces", bg="#f0f2f5", command=self._on_viewer_scope_changed)
        self.rb_scope_faces.pack(side=tk.LEFT, padx=5)
        self._set_album_scope_radio_enabled(str(self.var_viewer_scope.get() or "") == "album")

        # Sélecteur Géo (juste sous Visionneuse/Affichage)
        frame_geo = tk.Frame(frame_bottom_controls, bg="#f0f2f5")
        frame_geo.pack(side=tk.TOP, anchor="w", pady=0)
        tk.Label(frame_geo, text="Géo-localisation :", bg="#f0f2f5").pack(side=tk.LEFT, padx=5)
        self.var_geo_mode = tk.StringVar(value=self.config_data.get("geo_mode", "local"))
        tk.Radiobutton(frame_geo, text="En local (Rapide, 35k+)", variable=self.var_geo_mode, value="local", bg="#f0f2f5", command=self._save_config).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(frame_geo, text="En ligne (Économe en RAM)", variable=self.var_geo_mode, value="online", bg="#f0f2f5", command=self._save_config).pack(side=tk.LEFT, padx=5)

        # --- ONGLETS (TABLEAU + STATISTIQUES) ---
        frame_table = tk.Frame(self)
        frame_table.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 5))

        self.notebook = ttk.Notebook(frame_table)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tab_table = tk.Frame(self.notebook)
        tab_stats = tk.Frame(self.notebook, bg="#f8fafc")
        self.tab_faces = tk.Frame(self.notebook, bg="#f8fafc")
        self.tab_manual = tk.Frame(self.notebook, bg="#fafafa")
        self.tab_notes = tk.Frame(self.notebook, bg="#f8fafc")
        self.tab_stats = tab_stats

        self.notebook.add(tab_table, text="Tableau")
        self.notebook.add(self.tab_faces, text="Visages")
        self.notebook.add(self.tab_notes, text="Notes")
        self.notebook.add(self.tab_manual, text="Manuel")
        self.notebook.add(tab_stats, text="Statistiques")
        
        self._faces_tab_visible = True
        
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)
        self._last_notebook_tab_id = str(self.notebook.select() or "")
        
        self._setup_manual_tab()
        self._setup_notes_tab()

        columns = {
            "copied": ("Copié", 60),
            "directory_name": ("Répertoire", 150),
            "collection_name": ("Nom", 220),
            "filename": ("Nom Fichier", 150),
            "date_taken": ("Date", 150),
            "gps_coords": ("Coordonnées GPS", 160),
            "country": ("Pays", 80),
            "region": ("Région", 120),
            "city": ("Ville", 120),
            "filesize_str": ("Taille", 80),
            "camera": ("Appareil", 120)
        }
        
        self.tree = ttk.Treeview(tab_table, columns=list(columns.keys()), show="headings", selectmode="extended")
        for col, (title, width) in columns.items():
            self.tree.heading(col, text=title, command=lambda c=col: self._sort_by(c))
            if col in ("date_taken", "country", "copied"):
                anchor = "center"
            elif col in ("filesize_str", "gps_coords", "region", "city", "camera"):
                anchor = "e"
            else:
                anchor = "w"
            self.tree.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tab_table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<ButtonPress-1>", self._on_tree_click_stop_slideshow, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_geo_click)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Delete>", lambda e: self.delete_current())
        self.tree.bind("<space>", lambda e: self.copy_current())
        self.tree.bind("<F2>", lambda e: self._rename_single())
        self.tree.bind("<Prior>", self._on_tree_page_up)
        self.tree.bind("<Next>", self._on_tree_page_down)
        self.tree.bind("<Page_Up>", self._on_tree_page_up)
        self.tree.bind("<Page_Down>", self._on_tree_page_down)
        self.tree.bind("<KeyPress-d>", self._on_tree_start_slideshow_shortcut)
        self.tree.bind("<KeyPress-D>", self._on_tree_start_slideshow_shortcut)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        
        # Menu contextuel sur le tableau (clic droit)
        self._tree_ctx_menu = tk.Menu(self, tearoff=0)
        self._tree_ctx_menu.add_command(label="Ouvrir le répertoire", command=self._open_selected_directory)
        self._tree_ctx_menu.add_command(label="Localiser sur le disque", command=self._locate_selected_on_disk)
        self._tree_ctx_menu.add_command(label="Voir sur Google Maps", command=self._open_selected_in_maps)
        self._tree_ctx_menu.add_command(label="Vérifier GPS / Géographique", command=self._verify_selected_gps_geo)
        self._tree_ctx_menu.add_separator()
        self._tree_ctx_menu.add_command(label="Copier géolocalisation", command=self._copy_selected_geo)
        self._tree_ctx_menu.add_command(label="Coller géolocalisation (sélection)...", command=self._paste_geo_to_selection)
        self._tree_ctx_menu.add_separator()
        self._tree_ctx_menu.add_command(label="Renommer / Éditer (F2)", command=self._rename_single)
        self._tree_ctx_menu.add_command(label="Éditer par lots (Sélection)", command=self._rename_batch)
        self._tree_ctx_menu.add_command(label="Ajouter à l'album...", command=self._add_selected_table_photos_to_virtual_album)
        self._tree_ctx_menu.add_separator()
        self._tree_ctx_menu.add_command(label="Diaporama", command=self._start_slideshow_from_table_selection)
        self._tree_ctx_menu.add_separator()
        self._tree_ctx_menu.add_command(label="Copier la photo", command=self._copy_selected_photo)
        self._tree_ctx_menu.add_command(label="Supprimer (Corbeille)", command=self.delete_current)
        self._tree_ctx_delete_index = int(self._tree_ctx_menu.index("end") or 0)
        self._tree_ctx_menu.add_command(label="Copier la ligne", command=self._copy_selected_row)
        self.tree.bind("<Button-3>", self._show_tree_ctx_menu)

        # Onglet Statistiques
        self._stats_vars: dict[str, tk.StringVar] = {}
        stats_fields = [
            ("photos", "Photos en base"),
            ("dirs", "Répertoires sources"),
            ("size_total", "Taille totale"),
            ("size_avg", "Taille moyenne"),
            ("localized", "% fichiers localisés (GPS)"),
            ("geocoded", "% fichiers géocodés (Pays/Région/Ville)"),
            ("copied", "% photos exportées"),
            ("cameras", "Appareils distincts"),
            ("top_camera", "Appareil principal"),
            ("date_range", "Période de prise de vue"),
            ("duplicate_health", "Doublons signature"),
            ("faces_health", "Visages (traitées / photos / visages / personnes)"),
            ("face_perf", "Temps moyen / fichier (Face)"),
            ("face_runtime_status", "Reconnaissance faciale"),
            ("theme_runtime_status", "Classification thèmes"),
            ("theme_last_summary", "Dernier bilan thèmes"),
            ("album_health", "Santé sélection"),
            ("last_integrity_audit", "Dernier audit intégrité"),
        ]
        self._stats_fields = list(stats_fields)

        self._stats_ctx_menu = tk.Menu(self, tearoff=0)
        self._stats_ctx_menu.add_command(label="Copier dans le presse-papier", command=self._copy_statistics_page_to_clipboard)

        stats_grid = tk.Frame(tab_stats, bg="#f8fafc")
        stats_grid.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        stats_grid.columnconfigure(1, weight=1)
        self._face_status_dot_canvas: tk.Canvas | None = None
        self._face_status_dot_item: int | None = None
        self._theme_status_dot_canvas: tk.Canvas | None = None
        self._theme_status_dot_item: int | None = None
        for i, (key, label) in enumerate(stats_fields):
            var = tk.StringVar(value="-")
            self._stats_vars[key] = var
            tk.Label(stats_grid, text=label + " :", bg="#f8fafc", fg="#2c3e50", font=("Segoe UI", 10, "bold"), anchor="w").grid(row=i, column=0, sticky="w", pady=3)
            if key in ("face_runtime_status", "theme_runtime_status"):
                status_frame = tk.Frame(stats_grid, bg="#f8fafc")
                status_frame.grid(row=i, column=1, sticky="ew", padx=(12, 0), pady=3)
                status_frame.columnconfigure(1, weight=1)
                dot_canvas = tk.Canvas(status_frame, width=12, height=12, bg="#f8fafc", highlightthickness=0, borderwidth=0)
                dot_canvas.grid(row=0, column=0, sticky="w", padx=(0, 6))
                dot_item = dot_canvas.create_oval(2, 2, 10, 10, fill="#9ca3af", outline="#9ca3af")
                if key == "face_runtime_status":
                    self._face_status_dot_canvas = dot_canvas
                    self._face_status_dot_item = dot_item
                else:
                    self._theme_status_dot_canvas = dot_canvas
                    self._theme_status_dot_item = dot_item
                tk.Label(
                    status_frame,
                    textvariable=var,
                    bg="#f8fafc",
                    fg="#1f2937",
                    font=("Segoe UI", 10),
                    anchor="w",
                    justify="left",
                    wraplength=620,
                ).grid(row=0, column=1, sticky="ew")
            else:
                tk.Label(stats_grid, textvariable=var, bg="#f8fafc", fg="#1f2937", font=("Segoe UI", 10), anchor="w").grid(row=i, column=1, sticky="ew", padx=(12, 0), pady=3)

        self.tab_stats.bind("<Button-3>", self._show_stats_ctx_menu)
        self._bind_stats_context_menu_recursive(stats_grid)

        self._update_face_status_indicator(self._face_runtime_status)
        self._update_theme_status_indicator(self._theme_runtime_status)

        faces_tab_container = tk.Frame(self.tab_faces, bg="#f8fafc")
        faces_tab_container.pack(fill=tk.BOTH, expand=True)

        self.faces_tab_paned = tk.PanedWindow(faces_tab_container, orient=tk.HORIZONTAL, sashwidth=4, bg="#f8fafc", borderwidth=0)
        self.faces_tab_paned.pack(fill=tk.BOTH, expand=True)

        # Zone de GAUCHE : Barres de contrôle + Grille
        self.faces_left_pane = tk.Frame(self.faces_tab_paned, bg="#f8fafc")
        self.faces_tab_paned.add(self.faces_left_pane, stretch="always")

        faces_batch_bar = tk.Frame(self.faces_left_pane, bg="#e2e8f0", bd=1, relief="flat") # Task 11: Conteneur séparé (gris léger)
        faces_batch_bar.pack(fill=tk.X, padx=0, pady=(0, 4))
        inner_batch = tk.Frame(faces_batch_bar, bg="#e2e8f0")
        inner_batch.pack(fill=tk.X, padx=8, pady=6)
        self.btn_faces_batch_run = tk.Button(
            inner_batch,
            text="Exécuter batch",
            width=15,
            command=self._run_faces_batch_requested,
            bg="lightgray",
            fg="black",
        )
        self.btn_faces_batch_run.pack(side=tk.LEFT)
        self.btn_faces_batch_clear = tk.Button(
            inner_batch,
            text="Vider marquages",
            width=15,
            command=self._clear_face_edit_queue,
            bg="lightgray",
            fg="black",
        )
        self.btn_faces_batch_clear.pack(side=tk.LEFT, padx=(8, 0))
        self.lbl_faces_batch_summary = tk.Label(
            inner_batch,
            text="Batch visages: 0 action",
            anchor="w",
            justify="left",
            bg="#e2e8f0",
            fg="#334155",
            font=("Segoe UI", 9, "bold"),
        )
        self.lbl_faces_batch_summary.pack(side=tk.LEFT, padx=(12, 0))
        tk.Checkbutton(
            inner_batch,
            text="Masquer groupes nommés",
            variable=self.var_faces_hide_named,
            bg="#e2e8f0",
            fg="#1f2937",
            activebackground="#e2e8f0",
            font=("Segoe UI", 9),
            command=self._on_faces_hide_named_toggled,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        self._refresh_faces_batch_summary()

        self.faces_inline_editor_frame = tk.Frame(faces_tab_container, bg="#f8fafc")
        self.faces_inline_editor_frame.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.faces_inline_editor_frame.pack_forget()

        self.faces_inline_name_var = tk.StringVar(value="")
        self.faces_inline_name_var.trace_add("write", lambda *_args: self._refresh_faces_inline_suggestions())
        tk.Label(
            self.faces_inline_editor_frame,
            text="Renommage rapide :",
            bg="#f8fafc",
            fg="#0f172a",
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT)
        self.faces_inline_entry = tk.Entry(self.faces_inline_editor_frame, textvariable=self.faces_inline_name_var, width=34)
        self.faces_inline_entry.pack(side=tk.LEFT, padx=(8, 6))
        self.faces_inline_entry.bind("<Return>", self._on_faces_inline_entry_apply)
        self.faces_inline_entry.bind("<Escape>", self._on_faces_inline_entry_cancel)

        self.btn_faces_inline_apply = tk.Button(
            self.faces_inline_editor_frame,
            text="Marquer",
            width=10,
            command=self._apply_faces_inline_rename_mark,
            bg="lightgray",
            fg="black",
        )
        self.btn_faces_inline_apply.pack(side=tk.LEFT)
        self.btn_faces_inline_cancel = tk.Button(
            self.faces_inline_editor_frame,
            text="Annuler",
            width=10,
            command=self._cancel_faces_inline_editor,
            bg="lightgray",
            fg="black",
        )
        self.btn_faces_inline_cancel.pack(side=tk.LEFT, padx=(6, 0))

        self.faces_inline_listbox = tk.Listbox(faces_tab_container, height=6, activestyle="dotbox")
        self.faces_inline_listbox.bind("<ButtonRelease-1>", self._on_faces_inline_listbox_click)
        self.faces_inline_listbox.bind("<Double-Button-1>", self._on_faces_inline_listbox_apply)
        self.faces_inline_listbox.bind("<Return>", self._on_faces_inline_listbox_apply)

        self.faces_grid_container = tk.Frame(self.faces_left_pane, bg="#f8fafc")
        self.faces_grid_container.pack(fill=tk.BOTH, expand=True)

        self.faces_tab_canvas = tk.Canvas(
            self.faces_grid_container,
            bg="#f8fafc",
            highlightthickness=0,
            borderwidth=0,
        )
        self.faces_tab_scrollbar = ttk.Scrollbar(
            self.faces_grid_container,
            orient=tk.VERTICAL,
            command=self.faces_tab_canvas.yview,
        )
        self.faces_tab_canvas.configure(yscrollcommand=self.faces_tab_scrollbar.set)
        self.faces_tab_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.faces_tab_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.faces_tab_grid = tk.Frame(self.faces_tab_canvas, bg="#f8fafc")
        self._faces_tab_canvas_window = self.faces_tab_canvas.create_window(
            (0, 0),
            window=self.faces_tab_grid,
            anchor="nw",
        )

        # Zone de droite : Sidebar des personnes
        self.faces_sidebar_frame = tk.Frame(self.faces_tab_paned, bg="#f1f5f9", padx=1, pady=1)
        self.faces_tab_paned.add(self.faces_sidebar_frame, width=60)        
        btn_clear_sid = tk.Button(self.faces_sidebar_frame, text="Tout effacer", command=self._clear_faces_sidebar_selection, font=("Segoe UI", 8, "bold"), bg="#ef4444", fg="white")
        btn_clear_sid.pack(fill=tk.X, pady=(1, 1))
        
        chk_filter_marked = tk.Checkbutton(
            self.faces_sidebar_frame,
            text="Uniquement marqués (bons/faux)",
            variable=self.var_faces_show_only_marked,
            bg="#f1f5f9",
            anchor="w",
            font=("Segoe UI", 8),
            command=self._refresh_faces_tab
        )
        chk_filter_marked.pack(fill=tk.X, pady=(0, 2))
        
        self.faces_sidebar_list = ttk.Treeview(self.faces_sidebar_frame, show="tree", selectmode="browse")
        self.faces_sidebar_scrollbar = ttk.Scrollbar(self.faces_sidebar_frame, orient=tk.VERTICAL, command=self.faces_sidebar_list.yview)
        self.faces_sidebar_list.configure(yscrollcommand=self.faces_sidebar_scrollbar.set)
        
        self.faces_sidebar_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.faces_sidebar_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.faces_sidebar_list.bind("<<TreeviewSelect>>", self._on_faces_sidebar_select)
        self.faces_tab_grid.bind("<Configure>", self._on_faces_grid_configure)
        self.faces_tab_grid.bind("<Map>", self._on_faces_tab_mapped)
        self.faces_tab_canvas.bind("<Configure>", self._on_faces_canvas_configure)
        self.faces_tab_canvas.bind("<MouseWheel>", self._on_faces_tab_mousewheel)
        self.faces_tab_canvas.bind("<Button-4>", self._on_faces_tab_mousewheel)
        self.faces_tab_canvas.bind("<Button-5>", self._on_faces_tab_mousewheel)
        # Route aussi la molette au niveau global pour fiabiliser le scroll vertical
        # quand le focus clavier est ailleurs mais que le pointeur est dans l'onglet Visages.
        self.bind_all("<MouseWheel>", self._on_faces_tab_mousewheel_global, add="+")
        self.bind_all("<Button-4>", self._on_faces_tab_mousewheel_global, add="+")
        self.bind_all("<Button-5>", self._on_faces_tab_mousewheel_global, add="+")
        self.bind_all("<KeyPress>", self._on_faces_tab_keypress_global, add="+")

        self._reset_stats_display()

        # --- PROGRESSION ---
        frame_prog = tk.Frame(self, bg="#f0f2f5")
        frame_prog.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.progress = ttk.Progressbar(frame_prog, orient="horizontal", mode="determinate")
        self.progress.pack(fill=tk.X)

        # --- COMPTEURS ---
        frame_stats = tk.Frame(self, bg="#f0f2f5")
        frame_stats.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        self.var_counter = tk.StringVar(value="Prêt. Sélectionnez les dossiers.")
        self._counter_last_non_hover_text = self.var_counter.get()
        self.var_counter.trace_add("write", self._on_counter_text_changed)
        lbl_counter = tk.Label(frame_stats, textvariable=self.var_counter, font=("Segoe UI", 10, "italic"), fg="#2c3e50", bg="#f0f2f5")
        lbl_counter.pack(side=tk.LEFT)
        
        self.var_copies = tk.StringVar(value="Exportées : 0")
        lbl_copies = tk.Label(frame_stats, textvariable=self.var_copies, font=("Segoe UI", 10, "bold"), fg="#e94560", bg="#f0f2f5")
        lbl_copies.pack(side=tk.RIGHT)

        # Message centre cliquable (coffee tip / donation BTC)
        self.var_coffee_tip = tk.StringVar(value=self._get_random_coffee_prompt())
        self.lbl_coffee_tip = tk.Label(
            frame_stats,
            textvariable=self.var_coffee_tip,
            font=("Segoe UI", 9, "underline"),
            fg="#1d4ed8",
            bg="#f0f2f5",
            cursor="hand2",
            anchor="center",
            justify="center",
        )
        self.lbl_coffee_tip.bind("<Button-1>", self._on_coffee_tip_click)
        self._bind_coffee_tip_interactions()
        
        # Binding global de l'app vers viewer
        self.bind("<space>", lambda e: self.copy_current())
        self.bind("<Delete>", self._on_delete_shortcut)
        self.bind("<F1>", lambda e: self._show_help())

    # ── ACTIONS ET CONTROLES ─────────────────────────────────────────────

    def _stop_action(self) -> None:
        self._stop_event.set()
        self.btn_stop.config(state="disabled")
        self.var_counter.set("Interruption demandée, veuillez patienter pour un arrêt propre...")

    def _theme_classification_threshold(self) -> float:
        raw = self.config_data.get("theme_classify_threshold", 0.55)
        try:
            value = float(raw)
        except Exception:
            value = 0.55
        return max(0.05, min(0.95, value))

    @staticmethod
    def _build_theme_search_text(row: dict[str, Any]) -> str:
        filepath = str(row.get("filepath") or "")
        directory_name = str(row.get("directory_name") or "").strip()
        if not directory_name and filepath:
            try:
                directory_name = Path(filepath).parent.name or ""
            except Exception:
                directory_name = ""

        chunks = [
            str(row.get("filename") or ""),
            str(row.get("collection_name") or ""),
            directory_name,
            str(row.get("country") or ""),
            str(row.get("region") or ""),
            str(row.get("city") or ""),
        ]
        combined = " ".join(chunks).strip().lower()
        return _remove_accents(combined)

    @staticmethod
    def _score_theme_from_text(text: str, keywords: list[str]) -> tuple[float, int]:
        if not text:
            return (0.0, 0)
        hits = 0
        for keyword in keywords:
            token = _remove_accents(str(keyword or "").strip().lower())
            if token and token in text:
                hits += 1
        if hits <= 0:
            return (0.0, 0)
        score = min(0.98, 0.42 + (0.13 * float(hits)))
        return (score, hits)

    def _start_theme_album_classification(self) -> None:
        if self._theme_classify_running:
            messagebox.showinfo("Classification thèmes", "Une classification est déjà en cours.")
            return
        if self._theme_classify_waiting_for_face_stop:
            messagebox.showinfo("Classification thèmes", "Préparation déjà en cours (attente arrêt moteur visages).")
            return
        if not self.db:
            messagebox.showinfo("Classification thèmes", "Ouvrez d'abord une base source.")
            return

        threshold = self._theme_classification_threshold()
        engine_status = get_theme_engine_status()
        engine_ready = str(engine_status.get("ready") or "no") == "yes"
        backend_reason = str(engine_status.get("reason") or "")
        
        warning_msg = ""
        if not engine_ready:
            warning_msg = (
                "\n\n⚠️ MOTEUR VISION INDISPONIBLE\n"
                "L'analyse des images sera désactivée. Seule l'analyse textuelle sera utilisée.\n"
                f"Raison: {backend_reason}\n\n"
            )

        theme_list_str = "\n".join([f"- {data.get('label', k)}" for k, data in THEME_ALBUMS.items()])
        answer = messagebox.askyesno(
            "Classification thèmes",
            "Créer/mettre à jour les albums virtuels IA suivants ?\n\n"
            f"{theme_list_str}\n\n"
            f"{warning_msg}"
            f"Seuil de confiance actuel: {threshold:.2f}\n"
            "(les albums IA seront remplacés par les nouveaux résultats)",
        )
        if not answer:
            return

        worker_was_running = bool(self._face_worker_thread and self._face_worker_thread.is_alive())
        self._begin_long_action()
        if worker_was_running:
            logger.info("[theme] Demande pause moteur visages avant classification.")
            self._theme_classify_waiting_for_face_stop = True
            self._set_face_runtime_status("Pause demandée (classification thèmes)")
            self.var_counter.set("Classification thèmes: attente arrêt moteur visages...")
            self.progress.configure(mode="indeterminate")
            self.progress.start(10)
            self._stop_face_worker(wait_timeout=0.05)
            deadline = time.time() + 8.0
            self.after(120, lambda d=deadline: self._poll_face_worker_stop_then_start_theme(d))
            return

        self._launch_theme_classification_worker(restart_face_worker=False)

    def _poll_face_worker_stop_then_start_theme(self, deadline_ts: float) -> None:
        if not self._theme_classify_waiting_for_face_stop:
            return

        alive = bool(self._face_worker_thread and self._face_worker_thread.is_alive())
        if alive:
            self._stop_face_worker(wait_timeout=0.05)
            if time.time() < float(deadline_ts):
                self.after(120, lambda d=deadline_ts: self._poll_face_worker_stop_then_start_theme(d))
                return

            logger.warning("[theme] Timeout attente arrêt moteur visages avant classification.")
            self._theme_classify_waiting_for_face_stop = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self._set_theme_stats(runtime_status="Interrompu")
            self.var_counter.set("Classification thèmes annulée: moteur visages ne s'arrête pas.")
            self._end_long_action()
            messagebox.showwarning(
                "Classification thèmes",
                "Le moteur visages n'a pas pu s'arrêter à temps.\n"
                "Classification annulée (aucun lancement).",
            )
            return

        logger.info("[theme] Moteur visages arrêté, lancement classification.")
        self._theme_classify_waiting_for_face_stop = False
        self.progress.stop()
        self._set_face_runtime_status("En pause (classification thèmes)")
        self._launch_theme_classification_worker(restart_face_worker=True)

    def _launch_theme_classification_worker(self, restart_face_worker: bool) -> None:
        self._theme_classify_running = True
        self._set_theme_stats(runtime_status="En cours")
        self.var_counter.set("Classification thèmes: préparation...")
        self.progress.configure(mode="determinate", maximum=1, value=0)

        self._theme_classify_thread = threading.Thread(
            target=self._run_theme_album_classification_worker,
            args=(restart_face_worker,),
            daemon=True,
        )
        self._theme_classify_thread.start()

    def _run_theme_album_classification_worker(self, restart_face_worker: bool) -> None:
        interrupted = False
        summary: dict[str, int] = {theme_key: 0 for theme_key in THEME_ALBUMS.keys()}
        total_rows = 0
        image_inference_rows = 0
        engine_status = get_theme_engine_status()
        backend_name = str(engine_status.get("backend") or "none")
        backend_reason = str(engine_status.get("reason") or "")
        engine_ready = str(engine_status.get("ready") or "no") == "yes"
        logger.info("[theme] Lancement worker: engine_ready=%s backend=%s reason=%s", engine_ready, backend_name, backend_reason)
        try:
            rows = self.db.get_all_photos(order_by="id") if self.db else []
            total_rows = len(rows)
            if total_rows <= 0:
                self.after(
                    0,
                    lambda: self._on_theme_album_classification_done(
                        summary,
                        0,
                        0,
                        False,
                        restart_face_worker,
                        backend_name,
                        engine_ready,
                        backend_reason,
                    ),
                )
                return

            threshold = self._theme_classification_threshold()
            if self.db:
                self.db.clear_theme_tags(source=THEME_ALBUM_SOURCE)

            self.after(0, lambda t=total_rows: self.progress.configure(mode="determinate", maximum=max(1, t), value=0))

            for idx, row in enumerate(rows, start=1):
                if self._stop_event.is_set():
                    interrupted = True
                    break

                photo_id = int(row.get("id") or 0)
                if photo_id <= 0:
                    continue

                search_text = self._build_theme_search_text(row)
                image_scores: dict[str, float] = {k: 0.0 for k in THEME_ALBUMS.keys()}
                filepath = str(row.get("filepath") or "").strip()
                if engine_ready and filepath:
                    image_inference_rows += 1
                    # Utilisation du chemin absolu pour l'IA
                    abs_p = self._abs_path(filepath)
                    image_scores = classify_themes_from_image(abs_p, list(THEME_ALBUMS.keys()), topk=5)

                for theme_key, theme_data in THEME_ALBUMS.items():
                    score_text, _hits = self._score_theme_from_text(search_text, list(theme_data.get("keywords") or []))
                    score_image = float(image_scores.get(theme_key) or 0.0)
                    # Le modèle vision est prioritaire, le texte reste un filet de sécurité.
                    score = max(score_image, score_text * 0.72)
                    if score < threshold:
                        continue
                    if self.db and self.db.upsert_theme_tag(photo_id, theme_key, score, source=THEME_ALBUM_SOURCE):
                        summary[theme_key] = summary.get(theme_key, 0) + 1

                self._tick_photo_progress_threadsafe(idx, total_rows, "Classification thèmes")
            
            logger.info("[theme] Fin boucle classification: base=%d vision=%d", total_rows, image_inference_rows)

            # Synchronise les albums IA à partir des tags persistés.
            if self.db:
                for theme_key, theme_data in THEME_ALBUMS.items():
                    album_name = str(theme_data.get("label") or "").strip()
                    if not album_name:
                        continue
                    album = self.db.create_virtual_album(album_name)
                    if not album:
                        continue
                    album_id = int(album.get("id") or 0)
                    if album_id <= 0:
                        continue
                    ids = self.db.get_photo_ids_for_theme(theme_key, min_confidence=threshold, source=THEME_ALBUM_SOURCE)
                    synced_count = self.db.replace_virtual_album_photos(album_id, ids)
                    summary[theme_key] = int(synced_count)

        except Exception as e:
            logger.exception("Classification thèmes échouée: %s", e)
            interrupted = True

        self.after(
            0,
            lambda: self._on_theme_album_classification_done(
                summary,
                total_rows,
                image_inference_rows,
                interrupted,
                restart_face_worker,
                backend_name,
                engine_ready,
                backend_reason,
            ),
        )

    def _on_theme_album_classification_done(
        self,
        summary: dict[str, int],
        total_rows: int,
        image_inference_rows: int,
        interrupted: bool,
        restart_face_worker: bool,
        backend_name: str,
        engine_ready: bool,
        backend_reason: str,
    ) -> None:
        self._theme_classify_running = False
        self._theme_classify_thread = None
        self._end_long_action()

        self._refresh_sidebar()
        if self._is_virtual_album_scope_active() or str(self.var_viewer_scope.get()) == "faces":
            self._on_viewer_scope_changed()
        else:
            self._update_statistics()

        if restart_face_worker and (bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False):
            self._restart_face_recognition_after_cleanup()

        labels = {
            "animals": "Animaux",
            "insects": "Insectes",
            "flowers": "Fleurs",
            "temples": "Temples",
            "churches": "Eglises",
        }
        lines = [f"Photos parcourues (base): {int(total_rows or 0)}"]
        lines.append(f"Images analysées (vision): {int(image_inference_rows or 0)}")
        lines.append(f"Backend vision: {backend_name if backend_name else 'none'}")
        if not engine_ready and backend_reason:
            lines.append("Mode actuel: analyse textuelle uniquement (pas d'inférence image).")
            lines.append(f"Note backend: {backend_reason}")
        for key in ("animals", "insects", "flowers", "temples", "churches"):
            lines.append(f"- {labels[key]}: {int(summary.get(key, 0) or 0)}")

        theme_short = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | "
            f"base:{int(total_rows or 0)} vision:{int(image_inference_rows or 0)} "
            f"backend:{backend_name if backend_name else 'none'} "
            f"A:{int(summary.get('animals', 0) or 0)} I:{int(summary.get('insects', 0) or 0)} "
            f"F:{int(summary.get('flowers', 0) or 0)} T:{int(summary.get('temples', 0) or 0)} "
            f"E:{int(summary.get('churches', 0) or 0)}"
        )

        if interrupted:
            self._set_theme_stats(runtime_status="Interrompu", summary=theme_short)
            self.var_counter.set("Classification thèmes interrompue.")
            messagebox.showwarning("Classification thèmes", "Traitement interrompu.\n\n" + "\n".join(lines))
            return

        self._set_theme_stats(runtime_status="Terminé", summary=theme_short)
        self.var_counter.set("Classification thèmes terminée.")
        messagebox.showinfo("Classification thèmes", "Albums IA mis à jour.\n\n" + "\n".join(lines))

    def _get_faces_root_dir(self) -> Path | None:
        if not self._current_source_dir:
            return None
        root = self._current_source_dir / META_DIR_NAME / "Visages"
        ensure_directory(root)
        return root.resolve()

    def _set_face_runtime_status(self, text: str) -> None:
        status = str(text or "").strip() or "Désactivée"
        if getattr(self, "_face_runtime_status", "") == status:
            return
        self._face_runtime_status = status
        if hasattr(self, "_stats_vars") and "face_runtime_status" in self._stats_vars:
            self._stats_vars["face_runtime_status"].set(status)
        self._update_face_status_indicator(status)

    def _face_status_color(self, status: str) -> str:
        status_norm = str(status or "").strip().lower()
        if "désactiv" in status_norm or "desactiv" in status_norm:
            return "#9ca3af"
        if "indisponible" in status_norm or "annul" in status_norm or "erreur" in status_norm:
            return "#dc2626"
        if "traitement" in status_norm or "à jour" in status_norm or "a jour" in status_norm or "arrière-plan" in status_norm or "arriere-plan" in status_norm:
            return "#16a34a"
        return "#f59e0b"

    def _update_face_status_indicator(self, status: str) -> None:
        if not self._face_status_dot_canvas or self._face_status_dot_item is None:
            return
        color = self._face_status_color(status)
        try:
            self._face_status_dot_canvas.itemconfigure(self._face_status_dot_item, fill=color, outline=color)
        except Exception:
            pass

    def _theme_status_color(self, status: str) -> str:
        status_norm = str(status or "").strip().lower()
        if "inactif" in status_norm:
            return "#9ca3af"
        if "interrompu" in status_norm:
            return "#f59e0b"
        if "en cours" in status_norm:
            return "#2563eb"
        if "termin" in status_norm:
            return "#16a34a"
        if "erreur" in status_norm:
            return "#dc2626"
        return "#9ca3af"

    def _update_theme_status_indicator(self, status: str) -> None:
        if not self._theme_status_dot_canvas or self._theme_status_dot_item is None:
            return
        color = self._theme_status_color(status)
        try:
            self._theme_status_dot_canvas.itemconfigure(self._theme_status_dot_item, fill=color, outline=color)
        except Exception:
            pass

    def _set_face_runtime_status_threadsafe(self, text: str) -> None:
        self.after(0, lambda t=str(text): self._set_face_runtime_status(t))

    def _is_notebook_tab_focused(self, tab_widget: tk.Widget) -> bool:
        if not hasattr(self, "notebook"):
            return False
        try:
            return str(self.notebook.select() or "") == str(tab_widget)
        except Exception:
            return False

    def _update_face_stats_line_only(self) -> None:
        """Mise à jour légère de la ligne stats visages sans recalcul global."""
        if not hasattr(self, "_stats_vars") or "faces_health" not in self._stats_vars:
            return
        if not self.db:
            return
        face_stats = self.db.get_face_stats()
        self._stats_vars["faces_health"].set(
            f"{int(face_stats.get('processed') or 0)} / {int(face_stats.get('photos_with_faces') or 0)} / {int(face_stats.get('faces_total') or 0)} / {int(face_stats.get('people_total') or 0)}"
        )
        self._update_face_engine_perf_stats_lines()

    def _record_face_engine_timing(self, backend: str, duration_sec: float) -> None:
        key = str(backend or "").strip().lower()
        if key not in self._face_engine_perf:
            return
        try:
            sec = max(0.0, float(duration_sec))
        except Exception:
            sec = 0.0
        bucket = self._face_engine_perf[key]
        bucket["count"] = float(bucket.get("count") or 0.0) + 1.0
        bucket["total_sec"] = float(bucket.get("total_sec") or 0.0) + sec

    def _format_face_engine_perf_line(self, backend: str) -> str:
        bucket = self._face_engine_perf.get(str(backend), {})
        count = int(float(bucket.get("count") or 0.0))
        total_sec = float(bucket.get("total_sec") or 0.0)
        if count <= 0:
            return "-"
        avg_ms = (total_sec * 1000.0) / float(max(1, count))
        return f"{avg_ms:.1f} ms ({count} fichier(s))"

    def _update_face_engine_perf_stats_lines(self) -> None:
        if not hasattr(self, "_stats_vars"):
            return
        mapping = {
            "face_perf": "opencv",
        }
        for stat_key, backend in mapping.items():
            if stat_key in self._stats_vars:
                self._stats_vars[stat_key].set(self._format_face_engine_perf_line(backend))

    def _reset_face_engine_perf_stats(self) -> None:
        self._face_engine_perf = {
            "opencv": {"count": 0.0, "total_sec": 0.0},
        }
        self._update_face_engine_perf_stats_lines()

    def _refresh_face_background_ui(self) -> None:
        """Réduit les rafraîchissements UI en tâche de fond aux onglets visibles."""
        if self._is_notebook_tab_focused(self.tab_stats):
            self._update_face_stats_line_only()
        # L'onglet Visages ne doit pas se repeindre en tâche de fond.
        self._faces_tab_needs_refresh = True

    def _on_notebook_tab_changed(self, _event: tk.Event | None = None) -> None:
        """Rafraîchit uniquement l'onglet sélectionné pour limiter les repaints."""
        if self._notebook_tab_change_guard:
            return
        current_tab_id = str(self.notebook.select() or "")
        previous_tab_id = str(getattr(self, "_last_notebook_tab_id", "") or "")
        faces_tab_id = str(self.tab_faces or "")

        if previous_tab_id == faces_tab_id and current_tab_id != faces_tab_id:
            can_leave = self._confirm_pending_face_edits_before_leave("l'onglet Visages")
            if not can_leave:
                self._notebook_tab_change_guard = True
                try:
                    self.notebook.select(self.tab_faces)
                finally:
                    self._notebook_tab_change_guard = False
                self._last_notebook_tab_id = faces_tab_id
                return

        self._last_notebook_tab_id = current_tab_id
        if self._is_notebook_tab_focused(self.tab_stats):
            self._update_statistics()
            return
        if self._is_notebook_tab_focused(self.tab_faces):
            if previous_tab_id != faces_tab_id:
                self._faces_tab_needs_refresh = True
            if self._faces_tab_needs_refresh:
                self._refresh_faces_tab()
            elif self._active_face_person_id:
                # Le <Map> event va se déclencher automatiquement après le changement d'onglet.
                # On mémorise la cible ; le handler <Map> fera le scroll.
                self._pending_scroll_person_id = self._active_face_person_id
            return
        if self._is_notebook_tab_focused(self.tab_manual):
            self._setup_manual_tab() # Rafraîchir le rendu si besoin
            return

    def _sync_face_selection_from_active_person(self) -> None:
        """Synchronise le nom sélectionné pour la mosaïque avec l'ID de personne actif."""
        if not self.db or not self._active_face_person_id:
            return
        name = self.db.get_face_person_display_name(int(self._active_face_person_id))
        if name:
            self._selected_face_target_person_name = name

    def _sync_faces_tab_to_photo(self, photo_id: int) -> None:
        """Fait défiler la mosaïque vers la première personne détectée sur cette photo."""
        if not self.db or photo_id <= 0:
            return
        if not self._is_faces_scope_active():
            return
        
        # Récupérer les personnes associées à cette photo
        people = self.db.get_people_for_photo(photo_id)
        if not people:
            return

        person_ids: list[int] = []
        for p in people:
            try:
                person_ids.append(int(p.get("id") or 0))
            except Exception:
                continue
        person_ids = [pid for pid in person_ids if pid > 0]
        if not person_ids:
            return

        # Si la personne active est déjà sur la photo, on la garde pour éviter les oscillations.
        active_id = int(self._active_face_person_id or 0)
        if active_id > 0 and active_id in person_ids:
            p_id = active_id
        else:
            p_id = person_ids[0]

        logger.info("[sync] Synchronisation mosaïque vers personne %s via photo %s", p_id, photo_id)

        if p_id != int(self._active_face_person_id or 0):
            self._active_face_person_id = p_id
            self._active_virtual_album_id = None
            self._sync_face_selection_from_active_person()
            self._refresh_sidebar(select_active=True)
            self._grid_scope_dirty = True
            self._save_config()

        # Utiliser le système de scroll différé (<Map>) si l'onglet est masqué
        self._scroll_to_face_person_block(p_id)

    def _request_faces_tab_refresh(self) -> None:
        """Planifie et exécute un rafraîchissement immédiat de la mosaïque."""
        self._faces_tab_needs_refresh = True
        self._refresh_faces_tab()

    def _scroll_to_face_person_block(self, person_id: int) -> None:
        """Fait défiler la mosaïque pour afficher le groupe de la personne spécifiée."""
        if not hasattr(self, "_face_person_blocks") or not hasattr(self, "faces_tab_canvas") or not self.faces_tab_canvas.winfo_exists():
            return

        # Guard : si le canvas n'est pas mappé (onglet masqué), winfo_y() retournera 0.
        # On mémorise la cible et on laisse le handler <Map> faire le scroll.
        if not self.faces_tab_canvas.winfo_ismapped():
            self._pending_scroll_person_id = int(person_id)
            logger.debug("[mosaic] Canvas non mappé, scroll différé pour personne %s", person_id)
            return

        block = self._face_person_blocks.get(int(person_id or 0))
        if not block or not block.winfo_exists():
            logger.debug("[mosaic] Bloc non trouvé pour la personne %s", person_id)
            return
            
        self.update_idletasks()
        y = block.winfo_y()
        h_grid = self.faces_tab_grid.winfo_height()

        # Guard : coordonnées invalides (layout pas encore calculé)
        if h_grid <= 0:
            self._pending_scroll_person_id = int(person_id)
            logger.debug("[mosaic] h_grid=0, scroll différé pour personne %s", person_id)
            return

        logger.info("[mosaic] Défilement vers personne %s (y=%s, h=%s)", person_id, y, h_grid)
        # Calcul de la position relative (0.0 à 1.0)
        pos = float(y) / float(h_grid)
        # Petit ajustement pour ne pas coller en haut
        self.faces_tab_canvas.yview_moveto(max(0.0, pos - 0.01))
        # Scroll effectué avec succès, on efface le pending
        self._pending_scroll_person_id = None

    def _on_faces_tab_mapped(self, _event: tk.Event | None = None) -> None:
        """Appelé quand l'onglet Visages devient physiquement visible (<Map>).
        
        Déclenche le scroll différé si une cible était en attente.
        """
        pid = self._pending_scroll_person_id
        if pid:
            # after_idle pour laisser le geometry manager finir le layout
            self.after_idle(lambda p=pid: self._scroll_to_face_person_block(p))

    def _bind_ui_activity_tracking(self) -> None:
        """Suit l'activité utilisateur pour éviter les préchargements pendant interaction."""
        try:
            self.bind_all("<KeyPress>", self._register_ui_activity, add="+")
            self.bind_all("<ButtonPress>", self._register_ui_activity, add="+")
            self.bind_all("<MouseWheel>", self._register_ui_activity, add="+")
            self.bind_all("<Button-4>", self._register_ui_activity, add="+")
            self.bind_all("<Button-5>", self._register_ui_activity, add="+")
        except Exception:
            pass

    def _register_ui_activity(self, _event: tk.Event | None = None) -> None:
        self._last_ui_activity_ts = time.time()

    def _start_faces_background_preload_scheduler(self) -> None:
        if self._faces_preload_job is not None:
            try:
                self.after_cancel(self._faces_preload_job)
            except Exception:
                pass
            self._faces_preload_job = None
        self._faces_preload_job = self.after(self._faces_preload_interval_ms, self._tick_faces_background_preload)

    def _can_preload_faces_tab_in_background(self) -> bool:
        if not self.db:
            return False
        if not self._faces_tab_visible:
            return False
        if not self._faces_tab_needs_refresh:
            return False
        if self._is_notebook_tab_focused(self.tab_faces):
            return False
        if not bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else True:
            return False
        if self._is_searching:
            return False
        if (self._scan_thread and self._scan_thread.is_alive()) or (self._audit_thread and self._audit_thread.is_alive()):
            return False
        if self._duplicate_prepare_in_progress:
            return False
        if (time.time() - float(self._last_ui_activity_ts or 0.0)) < 1.8:
            return False
        try:
            if str(self.progress.cget("mode") or "") == "indeterminate":
                return False
        except Exception:
            pass
        return True

    def _tick_faces_background_preload(self) -> None:
        self._faces_preload_job = None
        try:
            if self._can_preload_faces_tab_in_background():
                self._refresh_faces_tab()
        except Exception as exc:
            logger.debug("[faces] Préchargement arrière-plan ignoré: %s", exc)
        finally:
            if self.winfo_exists():
                self._faces_preload_job = self.after(self._faces_preload_interval_ms, self._tick_faces_background_preload)

    def _on_faces_grid_configure(self, _event: tk.Event | None = None) -> None:
        if hasattr(self, "faces_tab_canvas"):
            self.faces_tab_canvas.configure(scrollregion=self.faces_tab_canvas.bbox("all"))

    def _on_faces_canvas_configure(self, event: tk.Event) -> None:
        if hasattr(self, "faces_tab_canvas") and hasattr(self, "_faces_tab_canvas_window"):
            self.faces_tab_canvas.itemconfigure(self._faces_tab_canvas_window, width=max(1, int(event.width)))

    def _bind_faces_mousewheel_recursive(self, widget: tk.Misc) -> None:
        """Attache la molette verticalement sur les widgets de l'onglet Visages."""
        try:
            widget.bind("<MouseWheel>", self._on_faces_tab_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_faces_tab_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_faces_tab_mousewheel, add="+")
        except Exception:
            return
        for child in widget.winfo_children() if hasattr(widget, "winfo_children") else []:
            self._bind_faces_mousewheel_recursive(child)

    def _on_faces_tab_mousewheel(self, event: tk.Event) -> str:
        if not self._is_notebook_tab_focused(self.tab_faces):
            return "break"
        delta = 0
        try:
            delta = int(getattr(event, "delta", 0) or 0)
        except Exception:
            delta = 0

        try:
            num = int(getattr(event, "num", 0) or 0)
        except Exception:
            num = 0
        if num == 4:
            step = -1
        elif num == 5:
            step = 1
        elif delta != 0:
            step = -1 if delta > 0 else 1
        else:
            step = 0

        if step != 0 and hasattr(self, "faces_tab_canvas"):
            self.faces_tab_canvas.yview_scroll(step, "units")
        return "break"

    @staticmethod
    def _is_descendant_widget(widget: tk.Misc | None, ancestor: tk.Misc) -> bool:
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    def _on_faces_tab_mousewheel_global(self, event: tk.Event) -> str | None:
        """Intercepte la molette globalement et la route vers l'onglet Visages actif."""
        if not self._is_notebook_tab_focused(self.tab_faces):
            return None

        return self._on_faces_tab_mousewheel(event)

    def _set_faces_selector_visibility(self, visible: bool) -> None:
        if not hasattr(self, "lbl_faces"):
            return
        widgets = [self.lbl_faces, self.selector_faces, self.btn_faces_browse]
        if visible:
            self.lbl_faces.grid(row=2, column=0, padx=(5, 2), pady=(0, 3), sticky="w")
            self.selector_faces.grid(row=2, column=1, pady=(0, 3), sticky="w")
            self.btn_faces_browse.grid(row=2, column=2, padx=(0, 2), pady=(0, 3), sticky="w")
            target_search_row = 3
        else:
            for widget in widgets:
                widget.grid_remove()
            target_search_row = 2
        self.lbl_search.grid_configure(row=target_search_row)
        self.entry_search.grid_configure(row=target_search_row)
        self.btn_search_clear.grid_configure(row=target_search_row)

    def _set_faces_scope_visibility(self, visible: bool) -> None:
        if not hasattr(self, "rb_scope_faces"):
            return
        if visible:
            if str(self.rb_scope_faces.winfo_manager() or "") != "pack":
                self.rb_scope_faces.pack(side=tk.LEFT, padx=5)
        else:
            if str(self.rb_scope_faces.winfo_manager() or "") == "pack":
                self.rb_scope_faces.pack_forget()

    def _set_album_scope_radio_enabled(self, enabled: bool) -> None:
        if not hasattr(self, "rb_scope_album"):
            return
        try:
            self.rb_scope_album.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass


    def _on_face_recognition_toggle(self, initialize_only: bool = False) -> None:
        enabled = bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False
        selected_backend = self._get_face_engine_backend()

        if hasattr(self, "btn_face_restart"):
            self.btn_face_restart.config(state="normal")
        if hasattr(self, "cmb_face_engine"):
            self.cmb_face_engine.configure(state="readonly")

        if enabled and not is_face_engine_available(selected_backend):
            reason = get_face_engine_unavailability_reason(selected_backend)
            if not initialize_only:
                messagebox.showwarning(
                    "Reconnaissance faciale",
                    f"Le moteur sélectionné '{selected_backend}' n'est pas disponible dans cet environnement.\n"
                    f"Détail: {reason or 'dépendance manquante ou incompatible'}.\n"
                    "Changez de moteur ou installez une version compatible.",
                )
            enabled = False
            if hasattr(self, "var_face_recognition_enabled"):
                self.var_face_recognition_enabled.set(False)
            self._set_face_runtime_status(f"Moteur indisponible ({selected_backend})")

        if enabled and self._get_faces_root_dir() is None:
            if initialize_only:
                # Ne pas forcer le décochage au démarrage si le chemin n'est pas encore prêt
                self._set_face_runtime_status("Dossier Visages requis")
                # On ne change pas enabled pour garder la cohérence visuelle
            else:
                messagebox.showinfo(
                    "Reconnaissance faciale",
                    "Sélectionnez d'abord le dossier racine des visages."
                )
                if not self._ask_faces_out():
                    enabled = False
                    if hasattr(self, "var_face_recognition_enabled"):
                        self.var_face_recognition_enabled.set(False)
                    self._set_face_runtime_status("Activation annulée (dossier manquant)")

        self._set_faces_selector_visibility(enabled)
        self._set_faces_scope_visibility(enabled)

        if self.db:
            self.db.set_face_index_enabled(enabled, self._get_face_engine_backend())

        if enabled and not initialize_only:
            self._ensure_face_worker_running()
            messagebox.showinfo(
                "Reconnaissance faciale",
                "La reconnaissance faciale est activée.\n"
                f"Le traitement tourne en tâche de fond légère (moteur: {selected_backend})."
            )
        if not enabled:
            self._stop_face_worker()
            self._set_face_runtime_status("Désactivée")
            self._face_zero_result_reindex_done = False

        if enabled and self._get_faces_root_dir() is not None:
            self._set_face_runtime_status(f"Active (initialisation - {selected_backend})")

        self._save_config()
        self._load_data_to_treeview()
        self._update_statistics()
        self._request_faces_tab_refresh()

    @staticmethod
    def _face_edit_action_key(person_id: int, thumb_relpath: str) -> str:
        rel = str(thumb_relpath or "").strip()
        return f"{int(person_id)}::{rel}"

    def _get_queued_face_edit(self, person_id: int, thumb_relpath: str) -> dict[str, Any] | None:
        key = self._face_edit_action_key(person_id, thumb_relpath)
        with self._face_edit_queue_lock:
            action = self._face_edit_queue.get(key)
            return dict(action) if action else None

    def _queue_face_edit_action(self, action: dict[str, Any], refresh_ui: bool = True) -> None:
        key = self._face_edit_action_key(int(action.get("person_id") or 0), str(action.get("thumb_relpath") or ""))
        if key.endswith("::"):
            return
        now_ts = time.time()
        action_copy = dict(action)
        action_copy["key"] = key
        action_copy["queued_at"] = now_ts
        action_copy["status"] = "pending"
        action_copy["last_error"] = ""
        with self._face_edit_queue_lock:
            self._face_edit_queue[key] = action_copy
        self._refresh_faces_batch_summary()
        if refresh_ui:
            self._request_faces_tab_refresh()

    def _queue_face_delete_mark(self, person_id: int, thumb_relpath: str, thumb_widget: tk.Misc | None = None) -> None:
        rel = str(thumb_relpath or "").strip()
        if int(person_id) <= 0 or not rel:
            return

        if thumb_widget is not None:
            self._set_selected_face_thumb(int(person_id), rel, thumb_widget)

        self._queue_face_edit_action(
            {
                "action_type": "delete",
                "person_id": int(person_id),
                "thumb_relpath": rel,
            },
            refresh_ui=False,
        )

        target_widget = thumb_widget if thumb_widget is not None else self._faces_selected_thumb_widget
        if target_widget is not None:
            try:
                self._apply_visual_badge_to_widget(target_widget, -1)
            except Exception:
                pass

    def _queue_face_rename_mark(self, person_id: int, thumb_relpath: str, target_name: str, refresh_ui: bool = True) -> None:
        rel = str(thumb_relpath or "").strip()
        clean_name = str(target_name or "").strip()
        if int(person_id) <= 0 or not rel or not clean_name:
            return
        previous_validated = 0
        if self.db:
            previous_validated = int(self.db.get_face_validation_status(int(person_id), rel) or 0)
        self._queue_face_edit_action(
            {
                "action_type": "rename",
                "person_id": int(person_id),
                "thumb_relpath": rel,
                "target_name": clean_name,
                "previous_validated": previous_validated,
            },
            refresh_ui=refresh_ui
        )

    def _restore_face_edit_action_state(self, action: dict[str, Any] | None) -> None:
        if not action or not self.db:
            return
        if str(action.get("action_type") or "") != "rename":
            return
        person_id = int(action.get("person_id") or 0)
        rel = str(action.get("thumb_relpath") or "").strip()
        if person_id > 0 and rel:
            self.db.set_face_validated(person_id, rel, 0)

    def _clear_queued_face_edit(self, person_id: int, thumb_relpath: str, refresh_ui: bool = True) -> None:
        key = self._face_edit_action_key(person_id, thumb_relpath)
        removed_action: dict[str, Any] | None = None
        with self._face_edit_queue_lock:
            if key in self._face_edit_queue:
                removed_action = dict(self._face_edit_queue.pop(key, None) or {})
        self._restore_face_edit_action_state(removed_action)
        self._refresh_faces_batch_summary()
        if refresh_ui:
            self._request_faces_tab_refresh()

    def _clear_face_edit_queue(self) -> None:
        if self._face_batch_running:
            messagebox.showinfo("Batch visages", "Batch en cours: vidage des marquages indisponible.")
            return
        removed_actions: list[dict[str, Any]] = []
        with self._face_edit_queue_lock:
            removed_actions = [dict(action) for action in self._face_edit_queue.values()]
            self._face_edit_queue.clear()
        for action in removed_actions:
            self._restore_face_edit_action_state(action)
        if self.db:
            self.db.reset_all_face_validations()
        self._clear_all_face_thumb_badges()
        self._refresh_faces_batch_summary()
        self._request_faces_tab_refresh()

    def _has_pending_face_edits(self) -> bool:
        with self._face_edit_queue_lock:
            return any(True for _ in self._face_edit_queue.values())

    def _clear_visual_badge_from_widget(self, thumb_box: tk.Misc | None) -> None:
        if not thumb_box or not getattr(thumb_box, "winfo_exists", lambda: False)():
            return
        thumb_lbl = None
        for child in thumb_box.winfo_children():
            if isinstance(child, tk.Label) and not getattr(child, "_is_batch_text", False):
                thumb_lbl = child
                break
        if thumb_lbl is not None:
            for child in thumb_lbl.winfo_children():
                child.destroy()
            try:
                thumb_lbl.configure(highlightthickness=0, highlightbackground="#f8fafc", highlightcolor="#f8fafc")
            except Exception:
                pass
        for child in list(thumb_box.winfo_children()):
            if getattr(child, "_is_batch_text", False):
                try:
                    child.destroy()
                except Exception:
                    pass

    def _clear_all_face_thumb_badges(self) -> None:
        for thumb_box in list(getattr(self, "_face_thumb_boxes", [])):
            self._clear_visual_badge_from_widget(thumb_box)

    def _on_faces_hide_named_toggled(self) -> None:
        self._save_config()
        self._request_faces_tab_refresh()

    def _refresh_faces_batch_summary(self) -> None:
        with self._face_edit_queue_lock:
            actions = list(self._face_edit_queue.values())
        rename_count = sum(1 for a in actions if str(a.get("action_type") or "") == "rename")
        delete_count = sum(1 for a in actions if str(a.get("action_type") or "") == "delete")
        total = len(actions)
        if hasattr(self, "lbl_faces_batch_summary"):
            self.lbl_faces_batch_summary.configure(
                text=(
                    f"Batch visages: {total} action{'s' if total != 1 else ''} "
                    f"({rename_count} renommage{'s' if rename_count != 1 else ''}, "
                    f"{delete_count} suppression{'s' if delete_count != 1 else ''})"
                )
            )

    def _collect_pending_face_actions(self) -> list[dict[str, Any]]:
        with self._face_edit_queue_lock:
            return [dict(action) for action in self._face_edit_queue.values()]

    def _start_faces_batch(self, pending_actions: list[dict[str, Any]], faces_root: Path) -> bool:
        if self._face_batch_running:
            messagebox.showinfo("Batch visages", "Un batch est déjà en cours.")
            return False
        if not self.db:
            messagebox.showinfo("Batch visages", "Base source non initialisée.")
            return False
        if not pending_actions:
            messagebox.showinfo("Batch visages", "Aucune action marquée.")
            return False

        worker_was_running = bool(self._face_worker_thread and self._face_worker_thread.is_alive())
        if worker_was_running:
            stopped = self._stop_face_worker(reason="Batch visages en cours", wait_timeout=1.5)
            if not stopped:
                messagebox.showwarning(
                    "Batch visages",
                    "Impossible de mettre en pause le moteur visages. Réessayez.",
                )
                return False

        self._face_batch_running = True
        self._face_batch_stop.clear()
        self._face_batch_last_progress_ts = 0.0
        if hasattr(self, "btn_faces_batch_run"):
            self.btn_faces_batch_run.config(state="disabled")
        if hasattr(self, "btn_faces_batch_clear"):
            self.btn_faces_batch_clear.config(state="disabled")
        self.progress.configure(mode="determinate", maximum=max(1, len(pending_actions)), value=0)
        self.var_counter.set(f"Batch visages: 0/{len(pending_actions)}")

        self._face_batch_thread = threading.Thread(
            target=self._run_faces_batch_worker,
            args=(pending_actions, faces_root),
            daemon=True,
        )
        self._face_batch_thread.start()
        return True

    def _confirm_pending_face_edits_before_leave(self, leave_target: str) -> bool:
        if not self._has_pending_face_edits() or self._face_batch_running:
            return True

        answer = messagebox.askyesnocancel(
            "Batch visages",
            "Des marquages visages sont en attente.\n\n"
            "Oui : lancer le batch\n"
            "Non : vider les marquages\n"
            f"Annuler : rester sur {leave_target}",
        )
        if answer is None:
            return False
        if answer is False:
            self._clear_face_edit_queue()
            return True

        faces_root = self._get_faces_root_dir()
        pending_actions = self._collect_pending_face_actions()
        self._start_faces_batch(pending_actions, faces_root)
        return False

    def _run_faces_batch_requested(self) -> None:
        faces_root = self._get_faces_root_dir()
        pending_actions = self._collect_pending_face_actions()

        if not pending_actions:
            messagebox.showinfo("Batch visages", "Aucune action marquée.")
            return

        rename_count = sum(1 for a in pending_actions if str(a.get("action_type") or "") == "rename")
        delete_count = sum(1 for a in pending_actions if str(a.get("action_type") or "") == "delete")
        answer = messagebox.askyesno(
            "Batch visages",
            f"Lancer le batch de modifications visages ?\n\n"
            f"Actions: {len(pending_actions)}\n"
            f"Renommages: {rename_count}\n"
            f"Suppressions vignettes: {delete_count}",
        )
        if not answer:
            return
        self._start_faces_batch(pending_actions, faces_root)

    def _run_faces_batch_worker(self, actions: list[dict[str, Any]], faces_root: Path) -> None:
        total = len(actions)
        done = 0
        success_keys: list[str] = []
        failed: list[tuple[str, str]] = []

        for action in actions:
            if self._face_batch_stop.is_set():
                break
            key = str(action.get("key") or "")
            ok, err = self._apply_face_batch_action(action, faces_root)
            done += 1
            if ok:
                if key:
                    success_keys.append(key)
            else:
                failed.append((key, err or "Erreur inconnue"))
            now_ts = time.time()
            if (now_ts - self._face_batch_last_progress_ts) >= 0.25 or done >= total:
                self._face_batch_last_progress_ts = now_ts
                self._faces_batch_progress_threadsafe(done, total)

        self.after(0, lambda: self._on_faces_batch_completed(done, total, success_keys, failed))

    def _faces_batch_progress_threadsafe(self, done: int, total: int) -> None:
        def _apply() -> None:
            self.progress.configure(mode="determinate", maximum=max(1, total), value=min(done, max(1, total)))
            self.var_counter.set(f"Batch visages: {done}/{total}")

        self.after(0, _apply)

    def _apply_face_batch_action(self, action: dict[str, Any], faces_root: Path) -> tuple[bool, str]:
        if not self.db:
            return (False, "Base non disponible")

        action_type = str(action.get("action_type") or "").strip().lower()
        person_id = int(action.get("person_id") or 0)
        rel = str(action.get("thumb_relpath") or "").strip()
        if person_id <= 0 or not rel:
            return (False, "Action invalide")

        if action_type == "delete":
            thumb_path = faces_root / rel
            try:
                if thumb_path.exists():
                    thumb_path.unlink()
            except Exception as exc:
                return (False, f"Suppression vignette impossible: {exc}")
            removed_faces, _removed_person = self.db.delete_face_by_thumb(person_id, rel)
            if int(removed_faces or 0) <= 0:
                return (False, "Référence vignette introuvable en base")
            return (True, "")

        if action_type == "rename":
            target_name = str(action.get("target_name") or "").strip()
            if not target_name:
                return (False, "Nom cible vide")
            ok, msg, _target_existing_id, clean_target_name, target_folder = self.db.resolve_face_person_target(target_name)
            if not ok or not target_folder:
                return (False, str(msg or "Résolution cible impossible"))

            source_path = faces_root / rel
            source_filename = source_path.name or Path(rel).name
            if not source_filename:
                return (False, "Nom de vignette invalide")

            target_dir = faces_root / target_folder
            ensure_directory(target_dir)
            target_path = target_dir / source_filename
            if source_path != target_path and target_path.exists():
                target_name_unique = self._build_incremented_duplicate_name(target_dir, source_filename)
                target_path = target_dir / target_name_unique

            moved_on_disk = False
            if source_path.exists() and source_path != target_path:
                try:
                    source_path.rename(target_path)
                    moved_on_disk = True
                except Exception as exc:
                    return (False, f"Déplacement vignette impossible: {exc}")

            new_rel = str(target_path.relative_to(faces_root)).replace("\\", "/")
            ok_reassign, msg_reassign, _target_person_id, _source_removed = self.db.reassign_face_by_thumb(
                person_id,
                rel,
                clean_target_name,
                new_rel,
            )
            if not ok_reassign:
                if moved_on_disk:
                    try:
                        ensure_directory(source_path.parent)
                        target_path.rename(source_path)
                    except Exception:
                        pass
                return (False, str(msg_reassign or "Réaffectation DB impossible"))

            return (True, "")

        return (False, "Type d'action inconnu")

    def _on_faces_batch_completed(
        self,
        done: int,
        total: int,
        success_keys: list[str],
        failed: list[tuple[str, str]],
    ) -> None:
        with self._face_edit_queue_lock:
            for key in success_keys:
                self._face_edit_queue.pop(str(key), None)
            for key, err in failed:
                if key in self._face_edit_queue:
                    self._face_edit_queue[key]["status"] = "error"
                    self._face_edit_queue[key]["last_error"] = str(err or "")

        self._face_batch_running = False
        if hasattr(self, "btn_faces_batch_run"):
            self.btn_faces_batch_run.config(state="normal")
        if hasattr(self, "btn_faces_batch_clear"):
            self.btn_faces_batch_clear.config(state="normal")
        self.progress.configure(mode="determinate", maximum=max(1, total), value=min(done, max(1, total)))
        self._refresh_faces_batch_summary()
        self._load_data_to_treeview()
        self._update_statistics()
        self._request_faces_tab_refresh()

        if bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False:
            self._restart_face_recognition_after_cleanup()

        if failed:
            details = "\n".join(f"- {msg}" for _key, msg in failed[:8])
            if len(failed) > 8:
                details += f"\n... {len(failed) - 8} autre(s)"
            messagebox.showwarning(
                "Batch visages",
                f"Batch terminé avec erreurs.\n"
                f"Réussies: {len(success_keys)}\n"
                f"Échecs: {len(failed)}\n\n"
                f"Détails:\n{details}",
            )
        else:
            messagebox.showinfo(
                "Batch visages",
                f"Batch terminé.\nActions appliquées: {len(success_keys)}",
            )

    @staticmethod
    def _is_default_face_name(name: str) -> bool:
        return bool(re.match(r"^personne\s+\d+$", str(name or "").strip(), flags=re.IGNORECASE))

    def _get_face_name_suggestions(self) -> list[str]:
        if not self.db:
            return []
        people = self.db.get_face_people()
        names: list[str] = []
        for person in people:
            display = str(person.get("display_name") or "").strip()
            if not display:
                continue
            if self._is_default_face_name(display):
                continue
            names.append(display)
        unique = sorted(set(names), key=lambda s: _remove_accents(s).lower())
        return unique

    def _refresh_faces_inline_suggestions(self) -> None:
        if not hasattr(self, "faces_inline_listbox"):
            return
        query = str(self.faces_inline_name_var.get() or "").strip()
        query_norm = _remove_accents(query).lower()
        all_names = self._get_face_name_suggestions()
        suggestions = [
            name for name in all_names
            if (not query_norm) or (query_norm in _remove_accents(name).lower())
        ]

        self.faces_inline_listbox.delete(0, tk.END)
        for name in suggestions[:30]:
            self.faces_inline_listbox.insert(tk.END, name)

        if self._faces_inline_edit_open and suggestions:
            if str(self.faces_inline_listbox.winfo_manager() or "") != "pack":
                self.faces_inline_listbox.pack(fill=tk.X, padx=8, pady=(0, 4), before=self.faces_tab_canvas)
        else:
            if str(self.faces_inline_listbox.winfo_manager() or "") == "pack":
                self.faces_inline_listbox.pack_forget()

    def _open_faces_inline_editor(self, initial_text: str = "") -> None:
        if not hasattr(self, "faces_inline_editor_frame"):
            return
        if str(self.faces_inline_editor_frame.winfo_manager() or "") != "pack":
            self.faces_inline_editor_frame.pack(fill=tk.X, padx=8, pady=(0, 4), before=self.faces_tab_canvas)
        self._faces_inline_edit_open = True
        self._faces_inline_selected_target = ""
        self.faces_inline_name_var.set(str(initial_text or ""))
        self._refresh_faces_inline_suggestions()
        self.faces_inline_entry.focus_set()
        self.faces_inline_entry.icursor(tk.END)

    def _cancel_faces_inline_editor(self) -> None:
        self._faces_inline_edit_open = False
        self._faces_inline_selected_target = ""
        if hasattr(self, "faces_inline_name_var"):
            self.faces_inline_name_var.set("")
        if hasattr(self, "faces_inline_editor_frame"):
            if str(self.faces_inline_editor_frame.winfo_manager() or "") == "pack":
                self.faces_inline_editor_frame.pack_forget()
        if hasattr(self, "faces_inline_listbox"):
            if str(self.faces_inline_listbox.winfo_manager() or "") == "pack":
                self.faces_inline_listbox.pack_forget()

    def _apply_faces_inline_rename_mark(self) -> None:
        selected = self._faces_selected_thumb
        if not selected:
            self._cancel_faces_inline_editor()
            return
        person_id, rel = selected
        target_name = str(self.faces_inline_name_var.get() or "").strip()
        if not target_name:
            messagebox.showinfo(
                "Marquer renommage",
                "Choisissez un nom de personne, soit en le saisissant, soit en le sélectionnant dans la liste.",
            )
            try:
                self.faces_inline_entry.focus_set()
            except Exception:
                pass
            return
        self._queue_face_rename_mark(int(person_id), str(rel), target_name)
        self._cancel_faces_inline_editor()

    def _on_faces_inline_entry_apply(self, _event: tk.Event | None = None) -> str:
        self._apply_faces_inline_rename_mark()
        return "break"

    def _on_faces_inline_entry_cancel(self, _event: tk.Event | None = None) -> str:
        self._cancel_faces_inline_editor()
        return "break"

    def _on_faces_inline_listbox_click(self, _event: tk.Event | None = None) -> str:
        if not hasattr(self, "faces_inline_listbox"):
            return "break"
        sel = self.faces_inline_listbox.curselection()
        if not sel:
            return "break"
        value = str(self.faces_inline_listbox.get(sel[0]) or "").strip()
        if value:
            self._faces_inline_selected_target = value
            self.faces_inline_name_var.set(value)
            self.faces_inline_entry.focus_set()
            self.faces_inline_entry.icursor(tk.END)
        return "break"

    def _on_faces_inline_listbox_apply(self, _event: tk.Event | None = None) -> str:
        self._on_faces_inline_listbox_click(_event)
        self._apply_faces_inline_rename_mark()
        return "break"

    def _on_faces_tab_keypress_global(self, event: tk.Event) -> str | None:
        if not self._is_notebook_tab_focused(self.tab_faces):
            return None
        if self._face_batch_running:
            return None
        selected = self._faces_selected_thumb
        if not selected:
            return None

        widget = getattr(event, "widget", None)
        if widget in (getattr(self, "faces_inline_entry", None), getattr(self, "faces_inline_listbox", None)):
            return None

        state = int(getattr(event, "state", 0) or 0)
        if state & 0x000C:
            return None

        keysym = str(getattr(event, "keysym", "") or "")
        char = str(getattr(event, "char", "") or "")

        if keysym == "Escape" and self._faces_inline_edit_open:
            self._cancel_faces_inline_editor()
            return "break"
        if keysym == "Return" and self._faces_inline_edit_open:
            self._apply_faces_inline_rename_mark()
            return "break"

        # Raccourcis de validation (Task 1, 2, 4)
        if not self._faces_inline_edit_open:
            if char.lower() == "v":
                self._on_shortcut_mark_face(1)
                return "break"
            if char.lower() == "f":
                self._on_shortcut_mark_face(-1)
                return "break"
            if char.lower() == "d" or keysym == "Delete":
                self._delete_selected_face_thumb()
                return "break"

        if len(char) == 1 and char.isalnum():
            if not self._faces_inline_edit_open:
                self._open_faces_inline_editor(char)
            else:
                current = str(self.faces_inline_name_var.get() or "")
                self.faces_inline_name_var.set(current + char)
                self.faces_inline_entry.focus_set()
                self.faces_inline_entry.icursor(tk.END)
            return "break"

        return None

    def _refresh_faces_tab(self) -> None:
        """Rafraîchit la mosaïque des visages (reconstruction ou défilement)."""
        if not hasattr(self, "faces_tab_grid"):
            return

        logger.info("[mosaic] Refresh demandé. needs_refresh=%s, active_person=%s", 
                    self._faces_tab_needs_refresh, self._active_face_person_id)

        # Si pas besoin de reconstruction et qu'on a juste besoin de scroller
        if not self._faces_tab_needs_refresh:
            if self._active_face_person_id and self._is_notebook_tab_focused(self.tab_faces):
                self._scroll_to_face_person_block(self._active_face_person_id)
            return

        # On reconstruit même si en arrière-plan (plus gourmand mais assure la synchro demandée)
        self._refresh_faces_batch_summary()
        self._cancel_faces_inline_editor()
        self._faces_tab_needs_refresh = False
        for child in self.faces_tab_grid.winfo_children():
            child.destroy()
        self._face_thumb_refs = []
        self._face_thumb_boxes: list[tk.Frame] = []
        self._faces_selected_thumb = None
        self._faces_selected_thumb_widget = None
        self._face_person_blocks = {}

        enabled = bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False
        if not enabled:
            return
        if not self.db:
            return

        face_stats = self.db.get_face_stats()
        processed = int(face_stats.get("processed") or 0)
        total_photos = int(face_stats.get("total_photos") or 0)
        faces_total = int(face_stats.get("faces_total") or 0)
        
        only_marked = bool(self.var_faces_show_only_marked.get())
        hide_named = bool(self.var_faces_hide_named.get()) if hasattr(self, "var_faces_hide_named") else True
        all_people = self.db.get_face_people(show_only_marked=only_marked)
        _is_unnamed = lambda p: bool(re.match(r"^Personne\s+\d+$", str(p.get("display_name") or ""), re.IGNORECASE))
        if hide_named:
            people = [p for p in all_people if _is_unnamed(p)]   # cochée : masque les nommés
        else:
            people = [p for p in all_people if not _is_unnamed(p)]  # décochée : masque les non-nommés

        # Mise à jour de la sidebar (avec guard contre les événements parasites)
        if hasattr(self, "faces_sidebar_list"):
            self._faces_sidebar_syncing = True
            try:
                self.faces_sidebar_list.delete(*self.faces_sidebar_list.get_children())
                for p in all_people:
                    p_name = str(p.get("display_name") or "").strip()
                    if not p_name: continue
                    # Masquer les noms génériques type 'Personne 123' dans la barre latérale
                    if re.match(r"^Personne\s+\d+$", p_name, re.IGNORECASE):
                        continue
                    self.faces_sidebar_list.insert("", tk.END, text=p_name)

                # Restaure strictement la sélection via la personne active.
                self._sync_faces_tab_sidebar_to_active_person()
            finally:
                self._faces_sidebar_syncing = False

        if not people:
            details = f"Photos traitées: {processed}/{total_photos} | Visages détectés: {faces_total}"
            tk.Label(
                self.faces_tab_grid,
                text=f"Aucun visage regroupé pour le moment. {details}",
                bg="#f8fafc",
                fg="#334155",
                anchor="w",
                font=("Segoe UI", 10, "italic"),
            ).pack(anchor="w")
            self._bind_faces_mousewheel_recursive(self.faces_tab_grid)
            return

        faces_root = self._get_faces_root_dir()

        for person in people:
            person_id = int(person.get("id") or 0)
            if person_id <= 0:
                continue
            person_folder = str(person.get("folder_name") or "").strip()
            if person_folder:
                self.db.normalize_face_thumb_folder(person_id, person_folder)

            block = tk.Frame(self.faces_tab_grid, bg="#f8fafc", bd=1, relief="solid")
            block.pack(fill=tk.X, expand=True, padx=8, pady=4)
            self._face_person_blocks[person_id] = block

            header = tk.Frame(block, bg="#f8fafc")
            header.pack(fill=tk.X, padx=8, pady=(6, 4))

            name_var = tk.StringVar(value=str(person.get("display_name") or ""))
            tk.Label(header, text="Nom :", bg="#f8fafc", fg="#1f2937", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            entry = tk.Entry(header, textvariable=name_var, width=34)
            entry.pack(side=tk.LEFT, padx=(8, 8))
            entry.bind("<Return>", lambda _e, pid=person_id, nv=name_var: self._rename_face_person_ui(pid, nv.get()))
            entry.bind("<KP_Enter>", lambda _e, pid=person_id, nv=name_var: self._rename_face_person_ui(pid, nv.get()))
            tk.Label(
                header,
                text=f"Photos: {int(person.get('photo_count') or 0)} | Visages: {int(person.get('face_count') or 0)}",
                bg="#f8fafc",
                fg="#475569",
                font=("Segoe UI", 9),
            ).pack(side=tk.RIGHT)

            thumbs_wrap = tk.Frame(block, bg="#f8fafc")
            thumbs_wrap.pack(fill=tk.X, padx=8, pady=(0, 8))

            thumbs_canvas = tk.Canvas(
                thumbs_wrap,
                bg="#f8fafc",
                highlightthickness=0,
                borderwidth=0,
                height=96,
            )
            thumbs_hscroll = ttk.Scrollbar(
                thumbs_wrap,
                orient=tk.HORIZONTAL,
                command=thumbs_canvas.xview,
            )
            thumbs_canvas.configure(xscrollcommand=thumbs_hscroll.set)
            thumbs_canvas.pack(fill=tk.X, expand=True)
            thumbs_hscroll.pack(fill=tk.X)

            thumbs_row = tk.Frame(thumbs_canvas, bg="#f8fafc")
            thumbs_window = thumbs_canvas.create_window((0, 0), window=thumbs_row, anchor="nw")
            thumbs_row.bind(
                "<Configure>",
                lambda _e, c=thumbs_canvas: c.configure(scrollregion=c.bbox("all")),
            )
            thumbs_canvas.bind(
                "<Configure>",
                lambda e, c=thumbs_canvas, w=thumbs_window: c.itemconfigure(w, height=max(1, int(e.height))),
            )

            thumbs = self.db.get_face_person_sample_thumbs(
                person_id, 
                limit=max(1, int(person.get("face_count") or 1)),
                show_only_marked=only_marked
            )
            if not thumbs:
                tk.Label(thumbs_row, text="(Aucune vignette)", bg="#f8fafc", fg="#94a3b8").pack(side=tk.LEFT, padx=4)
                continue

            for rel in thumbs:
                queued_action = self._get_queued_face_edit(person_id, str(rel))
                thumb_path: Path | None = None
                if faces_root is not None:
                    thumb_path = faces_root / rel
                if not thumb_path or not thumb_path.exists():
                    tk.Label(thumbs_row, text="x", width=10, bg="#e2e8f0", fg="#64748b").pack(side=tk.LEFT, padx=4)
                    continue
                try:
                    with Image.open(thumb_path) as opened:
                        img = ImageOps.exif_transpose(opened.copy())
                    img.thumbnail((88, 88), Image.Resampling.LANCZOS)
                    tk_img = ImageTk.PhotoImage(img)
                    self._face_thumb_refs.append(tk_img)
                    thumb_box = tk.Frame(thumbs_row, bg="#f8fafc")
                    self._face_thumb_boxes.append(thumb_box)
                    thumb_box.pack(side=tk.LEFT, padx=4)
                    thumb_lbl = tk.Label(
                        thumb_box,
                        image=tk_img,
                        bg="#f8fafc",
                        cursor="hand2",
                        highlightthickness=0,
                    )
                    thumb_lbl.pack(side=tk.TOP)
                    
                    # Task 3: Clic simple -> visionneuse, Ctrl+Clic -> Valider
                    cb = lambda e, pid=person_id, r=str(rel), b=thumb_box: self._on_face_thumb_click(e, pid, r, b)
                    thumb_lbl.bind("<Button-1>", cb)
                    thumb_box.bind("<Button-1>", cb)
                    
                    ctx_cb = lambda e, pid=person_id, r=str(rel), b=thumb_box: self._show_face_thumb_context_menu(e, pid, r, b)
                    thumb_lbl.bind("<Button-3>", ctx_cb)
                    thumb_box.bind("<Button-3>", ctx_cb)

                    badge_text = ""
                    if queued_action:
                        action_type = str(queued_action.get("action_type") or "")
                        if action_type == "delete":
                            badge_text = "SUPPRIMER"
                            try:
                                thumb_lbl.configure(highlightthickness=2, highlightbackground="#b91c1c", highlightcolor="#b91c1c")
                            except Exception: pass
                        elif action_type == "rename":
                            target = str(queued_action.get("target_name") or "").strip()
                            badge_text = f"RENOMMER -> {target}" if target else "RENOMMER"
                            try:
                                thumb_lbl.configure(highlightthickness=2, highlightbackground="#0369a1", highlightcolor="#0369a1")
                            except Exception: pass
                        
                    # Ajout badge de validation (Task 8: Croix verte/rouge overlay)
                    val_status = self.db.get_face_validation_status(person_id, str(rel))
                    badge_overlay = None
                    if val_status == 1:
                        badge_text = "CORRECT"
                        thumb_lbl.configure(highlightthickness=2, highlightbackground="#22c55e", highlightcolor="#22c55e")
                        badge_overlay = tk.Label(thumb_lbl, text="✓", fg="#22c55e", bg="white", font=("Segoe UI", 12, "bold"), bd=1, relief="solid")
                    elif val_status == -1:
                        badge_text = "FAUX / PAS LUI"
                        thumb_lbl.configure(highlightthickness=2, highlightbackground="#ef4444", highlightcolor="#ef4444")
                        badge_overlay = tk.Label(thumb_lbl, text="✗", fg="#ef4444", bg="white", font=("Segoe UI", 12, "bold"), bd=1, relief="solid")
                    
                    if badge_overlay:
                        badge_overlay.place(relx=1.0, rely=0.0, anchor="ne", x=-2, y=2)

                    if badge_text:
                        tk.Label(
                            thumb_box,
                            text=badge_text,
                            bg="#f8fafc",
                            fg="#0f172a",
                            font=("Segoe UI", 8, "bold"),
                            anchor="center",
                            justify="center",
                            wraplength=110,
                        ).pack(side=tk.TOP, pady=(2, 0))
                except Exception:
                    tk.Label(thumbs_row, text="x", width=10, bg="#e2e8f0", fg="#64748b").pack(side=tk.LEFT, padx=4)

        self._bind_faces_mousewheel_recursive(self.faces_tab_grid)
        if self._active_face_person_id:
            # Utilise le système <Map> pour différer le scroll si l'onglet est masqué,
            # ou after_idle si l'onglet est déjà visible.
            if self._is_notebook_tab_focused(self.tab_faces):
                self.after_idle(lambda pid=self._active_face_person_id: self._scroll_to_face_person_block(pid))
            else:
                self._pending_scroll_person_id = self._active_face_person_id

    def _rename_face_person_ui(self, person_id: int, new_name: str) -> None:
        if not self.db:
            return
        worker_was_running = bool(self._face_worker_thread and self._face_worker_thread.is_alive())
        if worker_was_running:
            self._stop_face_worker(reason="Mise à jour visages", wait_timeout=1.5)

        ok, message, old_folder, new_folder = self.db.rename_face_person(int(person_id), str(new_name or ""))
        if not ok:
            messagebox.showwarning("Renommer visage", message)
            self._refresh_faces_tab()
            if worker_was_running and (bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False):
                self._restart_face_recognition_after_cleanup()
            return

        faces_root = self._get_faces_root_dir()
        if faces_root is not None and old_folder and new_folder and old_folder != new_folder:
            old_dir = faces_root / old_folder
            new_dir = faces_root / new_folder
            if old_dir.exists():
                if new_dir.exists():
                    messagebox.showwarning("Renommer visage", "Le dossier cible existe déjà.")
                else:
                    try:
                        old_dir.rename(new_dir)
                    except Exception as e:
                        messagebox.showwarning("Renommer visage", f"Renommage dossier impossible: {e}")

        # Sélectionner la personne renommée dans la sidebar après le refresh
        self._active_face_person_id = int(person_id)
        self._last_faces_sidebar_structure = None  # invalide le cache pour forcer la reconstruction
        self._faces_tab_needs_refresh = True
        self._refresh_faces_tab()
        self._refresh_faces_sidebar(select_active=True)
        self._load_data_to_treeview()
        if worker_was_running and (bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False):
            self._restart_face_recognition_after_cleanup()

    def _on_face_thumb_click(self, event: tk.Event, person_id: int, thumb_relpath: str, thumb_box: tk.Frame) -> None:
        """Gère le clic sur une miniature de visage."""
        # Désélection de l'ancienne vignette (retire le cadre bleu)
        if self._faces_selected_thumb_widget and self._faces_selected_thumb_widget.winfo_exists():
            try:
                self._faces_selected_thumb_widget.configure(highlightthickness=0)
            except Exception: pass

        self._faces_selected_thumb = (int(person_id), str(thumb_relpath))
        self._faces_selected_thumb_widget = thumb_box
        try:
            thumb_box.configure(highlightthickness=2, highlightbackground="#2563eb", highlightcolor="#2563eb")
        except Exception: pass

        # Ctrl+Clic -> coche verte directe, sans rafraîchissement
        ctrl = (event.state & 0x0004) != 0
        if ctrl:
            self._on_shortcut_mark_face(1)
        else:
            self._open_face_thumb_origin_photo(person_id, thumb_relpath)

    def _on_shortcut_mark_face(self, status: int) -> None:
        """Marque le visage sélectionné comme bon (1) ou faux (-1) via raccourci (Task 2, 4)."""
        if not self._faces_selected_thumb or not self.db:
            return
        person_id, rel = self._faces_selected_thumb
        
        # Task 2: Si Correct (1) et personne sélectionnée en sidebar -> Ajouter au Batch
        if status == 1 and hasattr(self, "_selected_face_target_person_name") and self._selected_face_target_person_name:
            target = self._selected_face_target_person_name
            queued = self._get_queued_face_edit(person_id, rel)
            
            # Bascule si déjà dans le batch pour cette personne
            if queued and queued.get("action_type") == "rename" and queued.get("target_name") == target:
                self._clear_queued_face_edit(person_id, rel, refresh_ui=False)
                self._clear_visual_badge_from_widget(self._faces_selected_thumb_widget)
                return
            else:
                self._queue_face_rename_mark(person_id, rel, target, refresh_ui=False)
                self._apply_visual_badge_to_widget(self._faces_selected_thumb_widget, 1)
                self._set_face_runtime_status(f"Visage marqué pour renommage: {target}")
                return

        if self.db.set_face_validated(person_id, rel, status):
            # Mise à jour visuelle directe (Task 8: Croix verte/rouge overlay) sans refresh complet
            self._apply_visual_badge_to_widget(self._faces_selected_thumb_widget, status)
            self._set_face_runtime_status(f"Visage marqué: {'OK' if status==1 else 'FAUX'}")

    def _apply_visual_badge_to_widget(self, thumb_box: tk.Frame, status: int) -> None:
        """Ajoute un badge ✓ ou ✗ sur le widget de miniature sans recharger toute la page."""
        if not thumb_box:
            return
        # On cherche le Label de l'image (premier enfant)
        thumb_lbl = None
        for child in thumb_box.winfo_children():
            if isinstance(child, tk.Label) and not getattr(child, "_is_badge", False) and not getattr(child, "_is_batch_text", False):
                thumb_lbl = child
                break
        
        if not thumb_lbl:
            return

        # Nettoyage anciens badges sur ce Label (✓ ou ✗)
        for child in thumb_lbl.winfo_children():
            child.destroy()
            
        color = "#22c55e" if status == 1 else "#ef4444"
        char = "✓" if status == 1 else "✗"
        
        # Overlay ✓/✗
        badge = tk.Label(thumb_lbl, text=char, fg=color, bg="white", font=("Segoe UI", 12, "bold"), bd=1, relief="solid")
        badge._is_badge = True
        badge.place(relx=1.0, rely=0.0, anchor="ne", x=-2, y=2)
        
        # Bordure de couleur sur l'image
        thumb_lbl.configure(highlightthickness=2, highlightbackground=color, highlightcolor=color)

        # Task 2: Mise à jour du texte de batch sous l'image si nécessaire
        # On cherche s'il y a déjà un label de texte de batch (dernier enfant du thumb_box)
        batch_lbl = None
        for child in thumb_box.winfo_children():
            if getattr(child, "_is_batch_text", False):
                batch_lbl = child
                break
        
        # Récupérer l'action de batch actuelle pour ce visage
        person_id, rel = self._faces_selected_thumb if self._faces_selected_thumb else (0, "")
        queued_action = self._get_queued_face_edit(person_id, rel)
        badge_text = ""
        if queued_action:
            action_type = str(queued_action.get("action_type") or "")
            if action_type == "delete":
                badge_text = "SUPPRIMER"
            elif action_type == "rename":
                target = str(queued_action.get("target_name") or "").strip()
                badge_text = f"RENOMMER -> {target}" if target else "RENOMMER"

        if badge_text:
            if not batch_lbl:
                batch_lbl = tk.Label(
                    thumb_box,
                    text=badge_text,
                    bg="#f8fafc",
                    fg="#0f172a",
                    font=("Segoe UI", 8, "bold"),
                    anchor="center",
                    justify="center",
                    wraplength=110,
                )
                batch_lbl._is_batch_text = True
                batch_lbl.pack(side=tk.TOP, pady=(2, 0))
            else:
                batch_lbl.configure(text=badge_text)
        elif batch_lbl:
            batch_lbl.destroy()

    def _open_face_thumb_origin_photo(self, person_id: int, thumb_relpath: str) -> None:
        if not self.db:
            return
        info = self.db.get_face_thumb_origin_photo(int(person_id), str(thumb_relpath or ""))
        if not info:
            logger.warning("[faces] Impossible de trouver la photo d'origine en base pour person_id=%s rel=%s", person_id, thumb_relpath)
            self._set_face_runtime_status("Erreur: Photo introuvable en base")
            return

        source_path = self._abs_path(str(info.get("filepath") or ""))
        if not source_path or not source_path.exists():
            logger.warning("[faces] Le fichier source n'existe pas sur le disque : %s", source_path)
            self._set_face_runtime_status("Erreur: Fichier source absent du disque")
            return

        if hasattr(self, "var_viewer_mode"):
            self.var_viewer_mode.set("photo")
        self.config_data["viewer_mode"] = "photo"
        self._save_config()
        self._display_on_viewers(source_path, grab_focus=True)

    def _set_selected_face_thumb(self, person_id: int, thumb_relpath: str, widget: tk.Misc | None = None) -> None:
        rel = str(thumb_relpath or "").strip()
        if int(person_id) <= 0 or not rel:
            return

        if self._faces_selected_thumb_widget is not None:
            try:
                self._faces_selected_thumb_widget.configure(highlightthickness=0)
            except Exception:
                pass

        self._faces_selected_thumb = (int(person_id), rel)
        self._faces_selected_thumb_widget = widget

        if widget is not None:
            try:
                widget.configure(highlightthickness=2, highlightbackground="#1d4ed8", highlightcolor="#1d4ed8")
            except Exception:
                pass

    def _on_faces_sidebar_select(self, _event: tk.Event) -> None:
        # Guard : ignorer les événements pendant le rebuild de la sidebar
        if self._faces_sidebar_syncing:
            return

        selection = self.faces_sidebar_list.selection()
        if not selection:
            self._selected_face_target_person_name = None
            return
        
        node_id = selection[0]
        person_name = self.faces_sidebar_list.item(node_id, "text")
        self._selected_face_target_person_name = person_name
        logger.info("[sync] Personne sélectionnée (sidebar Visages): %s", person_name)

        # Résoudre l'ID et déclencher la synchronisation complète
        # (tableau + visionneuse mosaïque + sidebar principale)
        if self.db:
            person = self.db.get_face_person_by_name(person_name)
            if person:
                p_id = int(person["id"])
                if p_id != self._active_face_person_id:
                    self._active_face_person_id = p_id
                    self._active_virtual_album_id = None
                    # S'assurer que le scope est bien "faces"
                    if hasattr(self, "var_viewer_scope"):
                        self.var_viewer_scope.set("faces")
                    # Mise à jour tableau + stats, mais PAS de rebuild de la mosaïque
                    self._load_data_to_treeview()
                    self._save_config()
                    self._update_statistics()
                # Synchroniser la liste gauche (list_virtual_albums) sur la personne active
                self._focus_active_face_person_in_sidebar()
                # Dans tous les cas, juste scroller vers le bloc
                self._scroll_to_face_person_block(p_id)

    def _clear_faces_sidebar_selection(self) -> None:
        self.faces_sidebar_list.selection_remove(self.faces_sidebar_list.selection())
        self._selected_face_target_person_name = None
        self._refresh_faces_tab()

    def _delete_selected_face_thumb(self) -> bool:
        selected = self._faces_selected_thumb
        if not selected:
            return False
        person_id, rel = selected
        self._queue_face_delete_mark(person_id, rel, self._faces_selected_thumb_widget)
        return True

    def _mark_selected_face_validation(self, status: int) -> bool:
        """Legacy helper, redirige vers le nouveau système."""
        self._on_shortcut_mark_face(status)
        return True

    def _mark_face_validation_ui(self, person_id: int, rel: str, status: int) -> None:
        """Marque un visage spécifique (par clic menu ou raccourci)."""
        if self.db and self.db.set_face_validated(person_id, rel, status):
            if self._faces_selected_thumb == (int(person_id), str(rel)):
                self._apply_visual_badge_to_widget(self._faces_selected_thumb_widget, status)
            else:
                self._refresh_faces_tab()

    def _show_face_thumb_context_menu(
        self,
        event: tk.Event,
        person_id: int,
        thumb_relpath: str,
        thumb_widget: tk.Misc | None = None,
    ) -> str:
        if thumb_widget is not None:
            self._set_selected_face_thumb(person_id, thumb_relpath, thumb_widget)

        menu = tk.Menu(self, tearoff=0)
        queued = self._get_queued_face_edit(person_id, thumb_relpath)
        menu.add_command(
            label="Marquer renommage...",
            command=lambda pid=person_id, rel=thumb_relpath: self._rename_face_thumb_ui(pid, rel),
        )
        menu.add_command(
            label="Marquer suppression vignette (Delete)",
            command=lambda pid=person_id, rel=thumb_relpath, w=thumb_widget: self._queue_face_delete_mark(pid, rel, w),
        )
        menu.add_separator()
        menu.add_command(
            label="Marquer comme CORRECT (V)",
            command=lambda pid=person_id, rel=thumb_relpath: self._mark_face_validation_ui(pid, rel, 1),
        )
        menu.add_command(
            label="Marquer comme FAUX / PAS LUI (F)",
            command=lambda pid=person_id, rel=thumb_relpath: self._mark_face_validation_ui(pid, rel, -1),
        )
        if queued is not None:
            menu.add_separator()
            menu.add_command(
                label="Retirer du batch",
                command=lambda pid=person_id, rel=thumb_relpath: self._clear_queued_face_edit(pid, rel),
            )
        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            menu.grab_release()
        return "break"

    def _rename_face_thumb_ui(self, person_id: int, thumb_relpath: str) -> None:
        if not self.db:
            return

        rel = str(thumb_relpath or "").strip()
        if int(person_id) <= 0 or not rel:
            return

        self._open_faces_inline_editor("")

    def _delete_face_thumb_ui(self, person_id: int, thumb_relpath: str) -> None:
        if not self.db:
            return

        rel = str(thumb_relpath or "").strip()
        if not rel:
            return

        faces_root = self._get_faces_root_dir()
        if faces_root is not None:
            thumb_path = faces_root / rel
            if thumb_path.exists():
                try:
                    thumb_path.unlink()
                except Exception as exc:
                    logger.debug("[faces] Suppression miniature impossible: %s (%s)", thumb_path, exc)

        removed_faces, _removed_person = self.db.delete_face_by_thumb(int(person_id), rel)
        if removed_faces <= 0:
            logger.debug("[faces] Aucune entrée supprimée pour la miniature rel=%s person_id=%s", rel, person_id)
            return

        if self._faces_selected_thumb == (int(person_id), rel):
            self._faces_selected_thumb = None
            self._faces_selected_thumb_widget = None

        self._load_data_to_treeview()
        self._update_statistics()
        self._request_faces_tab_refresh()

    def _on_delete_shortcut(self, _event: tk.Event | None = None) -> str:
        """Routage de la touche Suppr: visages quand onglet Visages actif, sinon suppression photo."""
        if hasattr(self, "tab_faces") and self._is_notebook_tab_focused(self.tab_faces):
            self._delete_selected_face_thumb()
            return "break"

        self.delete_current()
        return "break"

    def _ask_faces_out(self) -> bool:
        initial = self.config_data.get("last_faces_out", "")
        folder = filedialog.askdirectory(title="Dossier racine des visages", initialdir=initial if initial else None)
        if folder:
            normalized = self._add_saved_path("faces_output", folder)
            if not normalized:
                return False
            self.var_faces.set(normalized)
            self._on_faces_selector_changed()
            return True
        return False

    def _restart_face_index_from_scratch(self) -> None:
        """Réinitialise l'index visages puis relance le traitement depuis zéro."""
        if not self.db:
            messagebox.showinfo("Recommencer visages", "Base source non initialisée.")
            return

        faces_root = self._get_faces_root_dir()
        if faces_root is None:
            messagebox.showinfo("Recommencer visages", "Sélectionnez d'abord un dossier Visages.")
            return

        # Exigence UX: désactiver immédiatement l'autorisation faciale au clic sur Reset.
        if hasattr(self, "var_face_recognition_enabled") and bool(self.var_face_recognition_enabled.get()):
            self.var_face_recognition_enabled.set(False)
            self._on_face_recognition_toggle(initialize_only=True)

        answer = messagebox.askyesno(
            "Recommencer visages",
            "Cette action va supprimer l'index visages actuel et relancer une indexation complète depuis zéro.\n\n"
            "Continuer ?",
        )
        if not answer:
            return

        stopped = self._stop_face_worker(reason="Réinitialisation en cours", wait_timeout=8.0)
        if not stopped:
            messagebox.showwarning(
                "Recommencer visages",
                "Le traitement de reconnaissance est encore en cours d'arrêt.\n"
                "Patientez quelques secondes puis relancez le reset.",
            )
            return

        removed_dirs = 0
        removed_files = 0
        fs_errors: list[str] = []

        # Nettoyage disque complet des artefacts visages (hors dossier Doublons dédié à l'audit).
        try:
            for target in faces_root.iterdir():
                if target.name.lower() == "doublons":
                    continue
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                        removed_dirs += 1
                    elif target.is_file():
                        target.unlink()
                        removed_files += 1
                except Exception as exc:
                    fs_errors.append(f"{target}: {exc}")
        except Exception as exc:
            fs_errors.append(f"{faces_root}: {exc}")

        self.db.reset_face_index_data()
        self._reset_face_engine_perf_stats()
        self._face_zero_result_reindex_done = False
        self._set_face_runtime_status("Réindexation visages depuis zéro")
        self._load_data_to_treeview()
        self._update_statistics()
        self._request_faces_tab_refresh()

        summary_text = (
            f"Dossiers supprimés: {removed_dirs}.\n"
            f"Fichiers supprimés: {removed_files}."
        )

        if fs_errors:
            logger.debug("[faces] Reprise index visages: erreurs nettoyage=%d", len(fs_errors))
            restart_now = messagebox.askyesno(
                "Recommencer visages",
                "Index visages réinitialisé sur disque et en base.\n"
                f"{summary_text}\n"
                f"Erreurs nettoyage: {len(fs_errors)} (voir logs).\n\n"
                "Relancer immédiatement la réindexation ?",
            )
        else:
            restart_now = messagebox.askyesno(
                "Recommencer visages",
                "Index visages réinitialisé sur disque et en base.\n"
                f"{summary_text}\n\n"
                "Relancer immédiatement la réindexation ?",
            )

        if restart_now:
            self._set_face_runtime_status("Réindexation visages depuis zéro")
            self._restart_face_recognition_after_cleanup()
        else:
            self._set_face_runtime_status("Index visages vidé (en pause)")

    def _stop_face_worker(self, reason: str | None = None, wait_timeout: float = 0.5) -> bool:
        logger.debug("[faces] Stop demandé (reason=%s, timeout=%.2fs)", reason, float(wait_timeout))
        self._face_worker_shutdown_requested = True
        self._face_worker_stop.set()
        thread = self._face_worker_thread
        is_alive = False
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, float(wait_timeout)))
            is_alive = thread.is_alive()
        if not is_alive:
            self._face_worker_thread = None
            self._face_worker_running = False
        if reason:
            status = str(reason) if not is_alive else f"{str(reason)} (arrêt en cours)"
            self._set_face_runtime_status(status)
        elif bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False:
            self._set_face_runtime_status("Arrêt en cours" if is_alive else "En pause")
        logger.debug("[faces] Stop résultat: alive_apres_attente=%s", is_alive)
        return not is_alive

    def _is_faces_background_sensitive_context(self) -> bool:
        """Retourne True si le contexte UI impose de geler les actions de fond liées aux visages."""
        if bool(getattr(self, "_face_batch_running", False)):
            return True
        if bool(getattr(self, "_editing_dialog_open", False)):
            return True
        if hasattr(self, "tab_faces") and self._is_notebook_tab_focused(self.tab_faces):
            return True
        if self._is_faces_scope_active():
            return True
        return False

    def _get_faces_background_pause_reason(self) -> str | None:
        """Donne la raison de pause des traitements de fond visages, sinon None."""
        if bool(getattr(self, "_face_batch_running", False)):
            return "En pause (batch visages)"
        if bool(getattr(self, "_editing_dialog_open", False)):
            return "En pause (édition batch)"
        if hasattr(self, "tab_faces") and self._is_notebook_tab_focused(self.tab_faces):
            return "En pause (onglet Visages actif)"
        if self._is_faces_scope_active():
            return "En pause (mode Visages)"
        return None

    def _ensure_face_worker_running(self) -> None:
        enabled = bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False
        if not enabled:
            return
        if not self.db:
            self._set_face_runtime_status("En attente de base source")
            return
        if self._get_faces_root_dir() is None:
            self._set_face_runtime_status("Dossier Visages requis")
            return
        if self._face_worker_thread and self._face_worker_thread.is_alive():
            pause_reason = self._get_faces_background_pause_reason()
            self._set_face_runtime_status(pause_reason or "Active (en arrière-plan)")
            return
        self._face_worker_shutdown_requested = False
        self._face_worker_stop.clear()
        self._face_zero_result_reindex_done = False
        pause_reason = self._get_faces_background_pause_reason()
        self._set_face_runtime_status(pause_reason or "Active (en arrière-plan)")
        logger.debug("[faces] Worker démarré (enabled=%s, faces_root=%s)", enabled, self._get_faces_root_dir())
        self._face_worker_thread = threading.Thread(target=self._face_worker_loop, daemon=True)
        self._face_worker_thread.start()

    def _relaunch_face_worker_manually(self) -> None:
        """Déclenche manuellement la reprise du traitement des visages sans tout effacer."""
        if not self.db: return
        count = self.db.clear_errored_face_photos()
        if not self.var_face_recognition_enabled.get():
             if messagebox.askyesno("Reconnaissance faciale", "La reconnaissance faciale est désactivée. Voulez-vous l'activer ?"):
                 self.var_face_recognition_enabled.set(True)
                 self._on_face_recognition_toggle()
        self._ensure_face_worker_running()
        messagebox.showinfo("Relance faciale", f"Worker relancé. {count} erreurs réinitialisées.\nLe traitement des visages manquants va reprendre en tâche de fond.")

    def _ensure_theme_worker_running(self) -> None:
        """S'assure que le worker thématique en arrière-plan tourne."""
        if not self.db: return
        if self._theme_worker_thread and self._theme_worker_thread.is_alive():
            return
        self._theme_worker_stop.clear()
        self._theme_worker_running = True
        self._theme_worker_thread = threading.Thread(target=self._theme_worker_loop, daemon=True)
        self._theme_worker_thread.start()

    def _theme_worker_loop(self) -> None:
        """Boucle de fond pour la classification thématique incrémentale."""
        while not self._theme_worker_stop.is_set():
            if not self.db: break
            if self._is_faces_background_sensitive_context():
                time.sleep(1.0)
                continue
            if (self._scan_thread and self._scan_thread.is_alive()) or self._duplicate_prepare_in_progress:
                time.sleep(2.0)
                continue

            batch = self.db.get_unprocessed_theme_photos(limit=3)
            if not batch:
                time.sleep(10.0)
                continue

            engine_status = get_theme_engine_status()
            engine_ready = str(engine_status.get("ready") or "no") == "yes"
            threshold = self._theme_classification_threshold()

            for row in batch:
                if self._theme_worker_stop.is_set(): break
                try:
                    photo_id = int(row.get("id") or 0)
                    self._process_single_theme_photo(row, engine_ready, threshold)
                    self.db.mark_theme_photo_processed(photo_id)
                except Exception as e:
                    logger.error("[theme-worker] Erreur photo %s: %s", row.get("id"), e)
                    self.db.mark_theme_photo_processed(int(row.get("id")), error_text=str(e))
            
            # On ne rafraîchit la sidebar que de temps en temps ou si un changement majeur est détecté
            # pour éviter de perturber l'utilisateur pendant sa navigation.
            # IMPORTANT : select_active=False pour ne pas voler la sélection à l'utilisateur.
            now = time.time()
            if (
                batch
                and not self._is_faces_background_sensitive_context()
                and (now - getattr(self, "_last_theme_sidebar_refresh", 0) > 10.0)
            ):
                self._last_theme_sidebar_refresh = now
                self.after(0, lambda: self._refresh_sidebar(select_active=False))
            
            time.sleep(2.0)

    def _process_single_theme_photo(self, row: dict[str, Any], engine_ready: bool, threshold: float) -> None:
        """Analyse et tague une seule photo pour les thèmes IA."""
        if not self.db: return
        photo_id = int(row.get("id") or 0)
        search_text = self._build_theme_search_text(row)
        image_scores: dict[str, float] = {k: 0.0 for k in THEME_ALBUMS.keys()}
        filepath = str(row.get("filepath") or "").strip()
        
        if engine_ready and filepath:
            abs_p = self._abs_path(filepath)
            image_scores = classify_themes_from_image(abs_p, list(THEME_ALBUMS.keys()), topk=5)

        for theme_key, theme_data in THEME_ALBUMS.items():
            score_text, _hits = self._score_theme_from_text(search_text, list(theme_data.get("keywords") or []))
            score_image = float(image_scores.get(theme_key) or 0.0)
            score = max(score_image, score_text * 0.72)
            
            if score >= threshold:
                if self.db.upsert_theme_tag(photo_id, theme_key, score, source=THEME_ALBUM_SOURCE):
                    # Ajouter également à l'album virtuel thématique
                    album_name = str(theme_data.get("label") or "").strip()
                    if album_name:
                        album = self.db.create_virtual_album(album_name)
                        if album:
                            self.db.add_photo_to_virtual_album(int(album["id"]), photo_id)

    def _restart_face_recognition_after_cleanup(self) -> None:
        """Relance explicitement le worker après nettoyage/reset d'index."""
        if not (bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False):
            return
        self._face_worker_shutdown_requested = False
        self._face_worker_stop.clear()
        self._ensure_face_worker_running()

        # Vérification différée: couvre les cas où l'arrêt précédent se termine juste après le reset.
        def _retry_start_if_needed() -> None:
            alive = bool(self._face_worker_thread and self._face_worker_thread.is_alive())
            if not alive:
                self._face_worker_stop.clear()
                self._ensure_face_worker_running()

        self.after(700, _retry_start_if_needed)

    def _pause_face_worker_for_audit(self) -> None:
        """Met en pause le worker facial pendant tout le cycle d'audit."""
        enabled = bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False
        if not enabled:
            self._face_worker_paused_for_audit = False
            return

        self._face_worker_paused_for_audit = True
        # Evite de figer l'UI: arrêt demandé immédiatement, terminaison effective en arrière-plan.
        self._stop_face_worker(reason="Reconnaissance faciale en pause (audit)", wait_timeout=0.05)
        if hasattr(self, "var_counter"):
            self.var_counter.set("Audit intégrité: reconnaissance faciale en pause temporaire")

    def _resume_face_worker_after_audit(self) -> None:
        """Relance le worker facial s'il a été mis en pause par l'audit."""
        if not self._face_worker_paused_for_audit:
            return
        self._face_worker_paused_for_audit = False
        if hasattr(self, "var_counter"):
            self.var_counter.set("Audit intégrité terminé: relance reconnaissance faciale")
        self._restart_face_recognition_after_cleanup()

    def _save_face_thumbnail(self, person_id: int, photo_id: int, face_index: int, face_img: Image.Image) -> str:
        faces_root = self._get_faces_root_dir()
        if faces_root is None or not self.db:
            logger.debug("[faces] Thumbnail non sauvegardée: root/db indisponible (photo_id=%s)", photo_id)
            return ""

        folder_name = self.db.get_face_person_folder_name(person_id)
        if not folder_name:
            logger.debug("[faces] Thumbnail non sauvegardée: folder_name vide (person_id=%s)", person_id)
            return ""

        person_dir = faces_root / folder_name
        ensure_directory(person_dir)
        filename = f"{int(photo_id)}_{int(face_index)}.jpg"
        target = person_dir / filename
        try:
            save_img = face_img.convert("RGB")
            save_img.save(target, "JPEG", quality=92)
            rel = target.relative_to(faces_root)
            return str(rel).replace("\\", "/")
        except Exception as exc:
            logger.debug("[faces] Echec sauvegarde vignette photo_id=%s person_id=%s target=%s: %s", photo_id, person_id, target, exc)
            return ""

    def _process_single_face_photo(self, photo_id: int, filepath: str) -> None:
        if not self.db:
            return
        path = self._abs_path(filepath)
        if not path or not path.exists() or not is_supported_image(path):
            logger.debug("[faces] Photo ignorée (invalide): id=%s path=%s", photo_id, path)
            self.db.replace_photo_faces(photo_id, [])
            self.db.mark_face_photo_processed(photo_id, "unsupported")
            return

        selected_backend = self._get_face_engine_backend()
        detections = detect_faces_from_image(path, backend=selected_backend)
        logger.debug(
            "[faces] Analyse photo id=%s path=%s backend=%s detections=%s",
            photo_id,
            path.name,
            selected_backend,
            len(detections),
        )
        payload: list[dict[str, Any]] = []
        match_threshold = self._get_face_match_threshold()
        for idx, detection in enumerate(detections, start=1):
            embedding = [float(v) for v in detection.get("embedding") or []]
            person_id = self.db.assign_face_person(embedding, threshold=match_threshold)
            thumb_rel = self._save_face_thumbnail(person_id, int(photo_id), idx, detection.get("thumb_image"))
            payload.append(
                {
                    "person_id": person_id,
                    "confidence": float(detection.get("confidence") or 0.0),
                    "bbox_left": float(detection.get("bbox_left") or 0.0),
                    "bbox_top": float(detection.get("bbox_top") or 0.0),
                    "bbox_width": float(detection.get("bbox_width") or 0.0),
                    "bbox_height": float(detection.get("bbox_height") or 0.0),
                    "thumb_relpath": thumb_rel,
                    "embedding": embedding,
                }
            )
        self.db.replace_photo_faces(photo_id, payload)
        self.db.mark_face_photo_processed(photo_id)
        logger.debug("[faces] Photo traitée id=%s faces=%s", photo_id, len(payload))

    def _face_worker_loop(self) -> None:
        self._face_worker_running = True
        crashed = False
        try:
            while not self._face_worker_stop.is_set():
                if not self.db or not bool(self.var_face_recognition_enabled.get()):
                    break

                if (
                    (self._scan_thread and self._scan_thread.is_alive())
                    or (self._audit_thread and self._audit_thread.is_alive())
                    or self._duplicate_prepare_in_progress
                ):
                    self._set_face_runtime_status_threadsafe("En pause (scan/audit en cours)")
                    time.sleep(1.0)
                    continue

                pause_reason = self._get_faces_background_pause_reason()
                if pause_reason:
                    self._set_face_runtime_status_threadsafe(pause_reason)
                    time.sleep(1.0)
                    continue

                batch = self.db.get_unprocessed_face_photos(limit=2)
                if not batch:
                    stats = self.db.get_face_stats() if self.db else {}
                    processed = int(stats.get("processed") or 0)
                    total_photos = int(stats.get("total_photos") or 0)
                    faces_total = int(stats.get("faces_total") or 0)

                    # Protection anti-run vide: si tout est traité mais 0 visage,
                    # relance automatique une seule fois une indexation complète.
                    if (
                        total_photos > 0
                        and processed >= total_photos
                        and faces_total == 0
                        and not self._face_zero_result_reindex_done
                        and self.db
                    ):
                        logger.debug("[faces] 0 visage détecté après traitement complet: relance automatique de l'indexation.")
                        self._face_zero_result_reindex_done = True
                        self.db.reset_face_index_data()
                        self._set_face_runtime_status_threadsafe("Relance indexation (0 résultat)")
                        self.after(0, self._refresh_face_background_ui)
                        continue

                    self._set_face_runtime_status_threadsafe(
                        f"À jour | T:{processed}/{total_photos} V:{faces_total}"
                    )
                    time.sleep(2.0)
                    continue

                self._set_face_runtime_status_threadsafe("Traitement en tâche de fond")
                for row in batch:
                    if self._face_worker_stop.is_set() or not bool(self.var_face_recognition_enabled.get()):
                        break
                    backend = self._get_face_engine_backend()
                    t0 = time.perf_counter()
                    try:
                        self._process_single_face_photo(int(row.get("id") or 0), str(row.get("filepath") or ""))
                    except Exception as e:
                        try:
                            self.db.mark_face_photo_processed(int(row.get("id") or 0), str(e))
                        except Exception:
                            pass
                    finally:
                        self._record_face_engine_timing(backend, time.perf_counter() - t0)

                stats = self.db.get_face_stats() if self.db else {}
                processed = int(stats.get("processed") or 0)
                total_photos = int(stats.get("total_photos") or 0)
                faces_total = int(stats.get("faces_total") or 0)
                errors = int(stats.get("processed_errors") or 0)
                logger.debug(
                    "[faces] Batch terminé: traitées=%s/%s visages=%s erreurs=%s",
                    processed,
                    total_photos,
                    faces_total,
                    errors,
                )
                self._set_face_runtime_status_threadsafe(
                    f"Traitement | T:{processed}/{total_photos} V:{faces_total} E:{errors}"
                )

                self.after(0, self._refresh_face_background_ui)
        except Exception as exc:
            crashed = True
            self._face_worker_running = False
            self._face_worker_thread = None
            if bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False:
                if self._face_worker_shutdown_requested or self._face_worker_stop.is_set():
                    self._set_face_runtime_status_threadsafe("En pause")
                else:
                    self._set_face_runtime_status_threadsafe("Active (arrêté)")
            logger.debug("[faces] Worker arrêté")

            should_restart = (
                (bool(self.var_face_recognition_enabled.get()) if hasattr(self, "var_face_recognition_enabled") else False)
                and not self._face_worker_shutdown_requested
                and not self._face_worker_stop.is_set()
            )
            if should_restart:
                reason = "exception inattendue" if crashed else "arrêt non demandé"
                logger.debug("[faces] Relance automatique du worker (%s).", reason)
                self._set_face_runtime_status_threadsafe("Relance automatique du worker")
                try:
                    self.after(500, self._ensure_face_worker_running)
                except Exception:
                    pass

    def _get_face_engine_backend(self) -> str:
        """Retourne le moteur de reconnaissance faciale configuré (OpenCV par défaut)."""
        return "opencv"

    def _get_face_match_threshold(self) -> float:
        raw: Any = 0.48
        if hasattr(self, "var_face_match_threshold"):
            raw = self.var_face_match_threshold.get()
        elif isinstance(self.config_data, dict):
            raw = self.config_data.get("face_match_threshold", 0.48)
        try:
            value = float(raw)
        except Exception:
            value = 0.48
        value = max(0.00, min(0.95, value))
        if hasattr(self, "var_face_match_threshold"):
            try:
                current = float(self.var_face_match_threshold.get())
            except Exception:
                current = -1.0
            if abs(current - value) > 1e-9:
                self.var_face_match_threshold.set(value)
        return value

    def _on_face_match_threshold_changed(self, _event: tk.Event | None = None) -> None:
        threshold = self._get_face_match_threshold()
        self.config_data["face_match_threshold"] = threshold
        self._save_config()
        self._set_face_runtime_status(f"Seuil ressemblance: {threshold:.2f}")
        self._request_faces_tab_refresh()
        self._restart_face_recognition_after_cleanup()

    def _on_counter_text_changed(self, *_args: Any) -> None:
        """Conserve le dernier texte compteur hors survol pour le restaurer proprement."""
        if self._counter_hover_internal_set:
            return
        if hasattr(self, "var_counter"):
            self._counter_last_non_hover_text = str(self.var_counter.get() or "")

    def _set_counter_hover_directory(self, source: str, photo_path: Path) -> None:
        """Affiche temporairement le répertoire d'une photo dans la zone compteur."""
        if not hasattr(self, "var_counter"):
            return
        try:
            folder = photo_path.parent.resolve()
        except Exception:
            folder = photo_path.parent
        msg = f"Répertoire : {folder}"
        self._counter_hover_source = source
        self._counter_hover_internal_set = True
        try:
            self.var_counter.set(msg)
        finally:
            self._counter_hover_internal_set = False

    def _clear_counter_hover_directory(self, source: str) -> None:
        """Restaure le message compteur après sortie de survol."""
        if self._counter_hover_source != source:
            return
        self._counter_hover_source = None
        self._counter_hover_internal_set = True
        try:
            self.var_counter.set(self._counter_last_non_hover_text or "Prêt. Sélectionnez les dossiers.")
        finally:
            self._counter_hover_internal_set = False

    def _on_logo_hover_enter(self, _event: tk.Event) -> None:
        if self._logo_active_path and self._logo_active_path.exists():
            self._set_counter_hover_directory("logo", self._logo_active_path)

    def _on_logo_hover_motion(self, _event: tk.Event) -> None:
        if self._logo_active_path and self._logo_active_path.exists():
            self._set_counter_hover_directory("logo", self._logo_active_path)

    def _on_logo_hover_leave(self, _event: tk.Event) -> None:
        self._clear_counter_hover_directory("logo")

    def _init_photo_orientation_menu(self) -> None:
        """Menu contextuel commun pour corriger l'orientation des photos."""
        self._photo_orientation_menu = tk.Menu(self, tearoff=0)
        self._photo_orientation_menu.add_command(
            label="Orientation: corriger via EXIF",
            command=lambda: self._apply_orientation_on_context("normalize"),
        )
        self._photo_orientation_menu.add_command(
            label="Orientation: rotation 90° droite",
            command=lambda: self._apply_orientation_on_context("right"),
        )
        self._photo_orientation_menu.add_command(
            label="Orientation: rotation 90° gauche",
            command=lambda: self._apply_orientation_on_context("left"),
        )
        self._photo_orientation_menu.add_command(
            label="Orientation: rotation 180°",
            command=lambda: self._apply_orientation_on_context("180"),
        )
        self._photo_orientation_menu.add_separator()
        self._photo_orientation_menu.add_command(
            label="Ajouter à l'album...",
            command=self._add_logo_photo_to_virtual_album,
        )

    def _show_logo_photo_ctx_menu(self, event: tk.Event) -> str | None:
        path = self._logo_active_path
        if not path or not path.exists() or not is_supported_image(path):
            return None
        self._orientation_ctx_path = path
        try:
            self._photo_orientation_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._photo_orientation_menu.grab_release()
        return "break"

    def _apply_orientation_on_context(self, operation: str) -> None:
        path = self._orientation_ctx_path
        if not path or not path.exists():
            return
        self._apply_orientation_correction(path, operation=operation)

    def _apply_orientation_correction(self, path: Path, operation: str = "normalize") -> None:
        """Corrige l'orientation d'une photo et remet Orientation EXIF à 1."""
        if not path.exists() or not is_supported_image(path):
            return

        try:
            with Image.open(path) as opened_img:
                fmt = str(opened_img.format or "").upper()
                exif = opened_img.getexif()
                img = ImageOps.exif_transpose(opened_img.copy())

            if operation == "right":
                img = img.rotate(-90, expand=True)
            elif operation == "left":
                img = img.rotate(90, expand=True)
            elif operation == "180":
                img = img.rotate(180, expand=True)

            exif[274] = 1  # EXIF Orientation
            exif_bytes = exif.tobytes()

            save_kwargs: dict[str, Any] = {"exif": exif_bytes}
            if fmt in ("JPEG", "JPG"):
                save_kwargs["quality"] = 95

            img.save(path, **save_kwargs)
            self._refresh_photo_after_orientation(path)
            self.var_counter.set(f"Orientation corrigée: {path.name}")
            self._audit_event("orientation_fix", {"path": str(path), "operation": operation})
        except Exception as exc:
            logger.error("Correction orientation impossible pour %s: %s", path, exc)
            messagebox.showerror("Orientation", f"Impossible de corriger l'orientation:\n{exc}")

    def _refresh_photo_after_orientation(self, path: Path) -> None:
        """Rafraîchit les deux visionneuses et la vignette logo après correction orientation."""
        active = self.viewer
        try:
            self.viewer_photo.display_image(path, grab_focus=False, keep_hidden=(active is not self.viewer_photo))
        except Exception:
            pass
        try:
            self.viewer_grid.display_image(path, grab_focus=False, keep_hidden=(active is not self.viewer_grid))
        except Exception:
            pass

        self._update_logo_preview(path)

        try:
            self._load_data_to_treeview()
        except Exception:
            pass

    def _get_random_coffee_prompt(self) -> str:
        """Retourne un texte court aléatoire depuis _resources/coffee.md, préfixé par 'Clic ici '."""
        if not self._coffee_lines_cache:
            self._coffee_lines_cache = self._load_coffee_lines()

        if not self._coffee_lines_cache:
            return "Clic ici soutenir le projet"
        return f"Clic ici {random.choice(self._coffee_lines_cache)}"

    def _load_coffee_lines(self) -> list[str]:
        """Charge les lignes candidates du fichier coffee.md."""
        candidates = [
            get_resource_path("src/photoselect/_resources/coffee.md"),
            Path(__file__).parent / "_resources" / "coffee.md",
            Path("src/photoselect/_resources/coffee.md"),
        ]

        lines: list[str] = []
        for p in candidates:
            if not p.exists():
                continue
            try:
                raw_lines = p.read_text(encoding="utf-8").splitlines()
                for line in raw_lines:
                    s = line.strip()
                    if not s:
                        continue
                    # Nettoie les préfixes visuels fréquents du fichier source.
                    if s.startswith("👉"):
                        s = s[1:].strip()
                    lines.append(s)
                break
            except Exception:
                continue
        return lines

    def _bind_coffee_tip_interactions(self) -> None:
        """Rafraîchit la phrase coffee lors des interactions utilisateur."""
        self.bind_all("<ButtonRelease-1>", self._on_user_interaction_refresh_coffee_tip, add="+")
        self.bind_all("<KeyRelease>", self._on_user_interaction_refresh_coffee_tip, add="+")
        self.bind_all("<MouseWheel>", self._on_user_interaction_refresh_coffee_tip, add="+")

    def _on_user_interaction_refresh_coffee_tip(self, _event: tk.Event | None = None) -> None:
        """Met à jour la phrase aléatoire, avec anti-clignotement léger."""
        now = time.time()
        if now - self._coffee_last_refresh_ts < 0.25:
            return
        self._coffee_last_refresh_ts = now
        if hasattr(self, "var_coffee_tip"):
            self.var_coffee_tip.set(self._get_random_coffee_prompt())

    def _on_coffee_tip_click(self, _event: tk.Event | None = None) -> None:
        self._on_user_interaction_refresh_coffee_tip()
        self._show_btc_donation_dialog()

    def _show_btc_donation_dialog(self) -> None:
        """Affiche une fenêtre de donation BTC avec texte et QR code."""
        dlg = tk.Toplevel(self)
        dlg.title("Soutenir PhotoSelect")
        dlg.configure(bg="#f8fafc")
        dlg.transient(self)
        dlg.grab_set()

        content = tk.Frame(dlg, bg="#f8fafc", padx=16, pady=14)
        content.pack(fill=tk.BOTH, expand=True)

        message = (
            "Si ce logiciel vous a fait gagner du temps et de la clarté dans vos photos,\n"
            "vous pouvez m’offrir un café ☕(ou plus selon votre karma)\n"
            "C’est entièrement libre — et toujours très apprécié.\n"
            "Don uniquement en BTC"
        )
        tk.Label(
            content,
            text=message,
            justify="center",
            anchor="center",
            wraplength=520,
            bg="#f8fafc",
            fg="#111827",
            font=("Segoe UI", 10),
        ).pack(fill=tk.X, pady=(0, 12))

        qr_candidates = [
            get_resource_path("src/photoselect/assets/QR_BTC.jpg"),
            Path(__file__).parent / "assets" / "QR_BTC.jpg",
            Path("src/photoselect/assets/QR_BTC.jpg"),
        ]

        qr_path: Path | None = None
        for p in qr_candidates:
            if p.exists():
                qr_path = p
                break

        if qr_path is not None:
            try:
                with Image.open(qr_path) as qr_img:
                    img = qr_img.copy()
                img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                qr_photo = ImageTk.PhotoImage(img)
                qr_label = tk.Label(content, image=qr_photo, bg="#f8fafc", borderwidth=0, highlightthickness=0)
                qr_label.image = qr_photo
                qr_label.pack(pady=(0, 8))
            except Exception as exc:
                tk.Label(
                    content,
                    text=f"QR BTC indisponible ({exc})",
                    bg="#f8fafc",
                    fg="#b91c1c",
                    font=("Segoe UI", 10, "italic"),
                ).pack(pady=(0, 8))
        else:
            tk.Label(
                content,
                text="QR BTC introuvable.",
                bg="#f8fafc",
                fg="#b91c1c",
                font=("Segoe UI", 10, "italic"),
            ).pack(pady=(0, 8))

        tk.Button(content, text="Fermer", width=14, command=dlg.destroy, bg="lightgray", fg="black").pack(pady=(4, 0))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self._center_dialog_on_main(dlg)
        dlg.focus_force()

    def _reindex_album_now(self) -> None:
        """Force une réindexation du dossier de sélection choisi."""
        cleanup = self._cleanup_missing_album_outputs()

        root = self._normalize_album_root()
        if root is None:
            removed_count = int(cleanup.get("removed", 0))
            if removed_count > 0:
                messagebox.showinfo(
                    "Réindexation sélection",
                    f"{removed_count} dossier(s) de sélection inexistant(s) ont été supprimé(s) de la liste et de la base.\n"
                    "Sélectionnez un dossier de sélection valide."
                )
            else:
                messagebox.showinfo("Réindexation sélection", "Sélectionnez un dossier de sélection valide.")
            return

        rows = self._sync_album_index()
        self._album_watch_signature = self._album_folder_signature()
        if self._is_album_scope_active():
            self._load_data_to_treeview()
            self._update_copies_counter()
            self._update_statistics()
        self._audit_event(
            "reindex_album",
            {
                "album_root": str(root),
                "indexed": len(rows),
                "removed_missing_roots": int(cleanup.get("removed", 0)),
            },
        )
        removed_count = int(cleanup.get("removed", 0))
        if removed_count > 0:
            messagebox.showinfo(
                "Réindexation sélection",
                f"Réindexation terminée ({len(rows)} image(s)).\n"
                f"Dossiers de sélection inexistants supprimés: {removed_count}."
            )
        else:
            messagebox.showinfo("Réindexation sélection", f"Réindexation terminée ({len(rows)} image(s)).")

    def _cleanup_missing_album_outputs(self) -> dict[str, int]:
        """Supprime les dossiers de sélection inexistants du sélecteur, du registre et des bases locales associées."""
        self._init_path_registry()
        self._init_album_index_registry()

        with sqlite3.connect(PATH_REGISTRY_DB_FILE) as conn:
            rows = conn.execute(
                "SELECT path FROM saved_paths WHERE kind = 'album_output'"
            ).fetchall()

        known_paths = {self._normalize_saved_path(str(r[0] or "")) for r in rows}
        known_paths.update(self._saved_album_output_paths)
        cfg_out = self._normalize_saved_path(str(self.config_data.get("last_out") or ""))
        if cfg_out:
            known_paths.add(cfg_out)

        missing_roots: list[str] = []
        for path_str in sorted(p for p in known_paths if p):
            p = Path(path_str)
            if not p.exists() or not p.is_dir():
                missing_roots.append(path_str)

        if not missing_roots:
            return {"removed": 0}

        with sqlite3.connect(PATH_REGISTRY_DB_FILE) as conn:
            for missing in missing_roots:
                conn.execute(
                    "DELETE FROM saved_paths WHERE kind = 'album_output' AND path = ?",
                    (missing,),
                )

        with sqlite3.connect(self._album_index_db) as conn:
            for missing in missing_roots:
                conn.execute("DELETE FROM album_index WHERE album_root = ?", (missing,))
                conn.execute("DELETE FROM album_audit_state WHERE album_root = ?", (missing,))

        self._saved_album_output_paths = [p for p in self._saved_album_output_paths if p not in missing_roots]
        self._saved_album_output_paths.sort(key=str.casefold)
        self._refresh_path_selectors()

        current_out = self._normalize_saved_path(str(self.var_out.get() or "")) if hasattr(self, "var_out") else ""
        if current_out in missing_roots:
            replacement = self._saved_album_output_paths[0] if self._saved_album_output_paths else ""
            if hasattr(self, "var_out"):
                self.var_out.set(replacement)
            self.config_data["last_out"] = replacement
            self._save_config()
        elif cfg_out in missing_roots:
            self.config_data["last_out"] = ""
            self._save_config()

        return {"removed": len(missing_roots)}

    def _begin_long_action(self) -> None:
        """Active le mode action longue avec bouton Stop disponible."""
        self._stop_event.clear()
        self.btn_open.config(state="disabled")
        self.btn_refresh.config(state="disabled")
        self.btn_rename.config(state="disabled")
        self.btn_purge.config(state="disabled")
        self.btn_theme_classify.config(state="disabled")
        self.btn_normalize_geo.config(state="disabled")
        self.btn_reindex_album.config(state="disabled")
        self.btn_integrity.config(state="disabled")
        self.btn_export_log.config(state="disabled")
        self.btn_stop.config(state="normal")

    def _end_long_action(self) -> None:
        """Restaure l'état normal de l'UI après une action longue."""
        self.btn_open.config(state="normal")
        self.btn_refresh.config(state="normal")
        self.btn_rename.config(state="normal")
        self.btn_purge.config(state="normal")
        self.btn_theme_classify.config(state="normal")
        self.btn_normalize_geo.config(state="normal")
        self.btn_reindex_album.config(state="normal")
        self.btn_integrity.config(state="normal")
        self.btn_export_log.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _purge_data(self) -> None:
        """Supprime les données locales (bases + vignettes) et remet l'app à zéro."""
        if self.btn_stop.cget("state") == "normal":
            messagebox.showwarning("Action en cours", "Arrêtez d'abord l'opération en cours avant de purger les données.")
            return

        answer = messagebox.askyesno(
            "⚠ Purger les données locales",
            "Cette action va supprimer toutes les bases de données locales et toutes les vignettes en cache.\n\n"
            "PhotoSelect redémarrera comme au premier usage (sans historique local).\n\n"
            "Continuer ?"
        )
        if not answer:
            return

        self._cancel_background_count()

        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
            self.db = None

        removed_items = 0
        errors: list[str] = []
        removed_legacy = 0
        for target in (APP_DB_DIR, APP_CACHE_DIR):
            if not target.exists():
                continue
            try:
                shutil.rmtree(target)
                removed_items += 1
            except Exception as e:
                errors.append(f"{target}: {e}")

        # Supprime aussi l'ancienne DB locale éventuelle dans le dossier source
        # pour éviter une réimportation automatique juste après purge.
        source_candidates: set[Path] = set()
        for raw in (
            str(self._current_source_dir or "").strip(),
            self.var_in.get().strip() if hasattr(self, "var_in") else "",
            str(self.config_data.get("last_in") or "").strip(),
        ):
            if not raw:
                continue
            try:
                source_candidates.add(Path(raw).resolve())
            except Exception:
                source_candidates.add(Path(raw))

        for source_path in source_candidates:
            legacy_db = source_path / ".photoselect.db"
            if not legacy_db.exists():
                continue
            try:
                legacy_db.unlink()
                removed_legacy += 1
            except Exception as e:
                errors.append(f"{legacy_db}: {e}")

        self._current_source_dir = None
        self._saved_source_paths = []
        self._saved_album_output_paths = []
        self._refresh_path_selectors()
        self.var_in.set("")
        self.var_out.set("")
        self.var_search.set("")
        self.config_data["last_in"] = ""
        self.config_data["last_out"] = ""
        self.config_data["last_virtual_album_name"] = ""
        self.config_data["last_viewed_source"] = ""
        self.config_data["last_viewed_path"] = ""
        self._active_virtual_album_id = None
        self._active_virtual_album_name = ""
        self._save_config()
        self._clear_table_display("Données locales purgées. Sélectionnez les dossiers pour recommencer.")

        if errors:
            messagebox.showwarning("Purge partielle", "Certaines données n'ont pas pu être supprimées:\n\n" + "\n".join(errors))
            return

        messagebox.showinfo(
            "Purge terminée",
            f"Purge terminée ({removed_items} dossiers supprimés, {removed_legacy} base(s) legacy supprimée(s)).",
        )

    def _show_help(self, _event: tk.Event | None = None) -> None:
        """Affiche une aide contextuelle au centre de l'écran."""
        # Déterminer le contexte
        context = "Tableau de Bord"
        focused = self.focus_get()
        
        # Si on est dans une visionneuse
        if isinstance(focused, FullscreenViewer) or (hasattr(self, "viewer_photo") and focused and focused.winfo_toplevel() == self.viewer_photo) or (hasattr(self, "viewer_grid") and focused and focused.winfo_toplevel() == self.viewer_grid):
            context = "Visionneuse"
        elif self.notebook.index("current") == 1: # Index de l'onglet visages/mosaïque
            context = "Mosaïque"
            
        self._show_contextual_help_window(context)

    def _show_contextual_help_window(self, context: str) -> None:
        """Crée et centre la fenêtre d'aide contextuelle."""
        win = tk.Toplevel(self)
        win.title(f"Aide PhotoSelect — {context}")
        win.configure(bg="#1f2937") # Gris très foncé premium
        win.attributes("-topmost", True)
        win.resizable(False, False)
        
        # Style
        main_font = ("Segoe UI", 10)
        bold_font = ("Segoe UI", 10, "bold")
        header_font = ("Segoe UI", 14, "bold")
        
        # Header
        header_frame = tk.Frame(win, bg="#111827", padx=20, pady=15)
        header_frame.pack(fill=tk.X)
        tk.Label(header_frame, text=f"Raccourcis : {context}", font=header_font, fg="#60a5fa", bg="#111827").pack(side=tk.LEFT)
        tk.Label(header_frame, text="ESC pour fermer", font=("Segoe UI", 9), fg="#9ca3af", bg="#111827").pack(side=tk.RIGHT)
        
        # Grille des raccourcis
        content_frame = tk.Frame(win, bg="#1f2937", padx=30, pady=20)
        content_frame.pack(fill=tk.BOTH, expand=True)
        
        shortcuts = HELP_SHORTCUTS.get(context, []) + HELP_SHORTCUTS.get("Global", [])
        
        for i, (key, desc) in enumerate(shortcuts):
            # Touche (à droite, gras)
            tk.Label(content_frame, text=key, font=bold_font, fg="#f3f4f6", bg="#1f2937", anchor="e", width=25).grid(row=i, column=0, pady=4, padx=(0, 15))
            # Description (à gauche)
            tk.Label(content_frame, text=desc, font=main_font, fg="#d1d5db", bg="#1f2937", anchor="w").grid(row=i, column=1, pady=4)
            
        # Bouton fermer
        btn_close = tk.Button(win, text="J'ai compris", command=win.destroy, 
                              bg="#3b82f6", fg="white", font=bold_font, 
                              relief=tk.FLAT, padx=20, pady=8, cursor="hand2")
        btn_close.pack(pady=20)
        btn_close.bind("<Enter>", lambda e: btn_close.config(bg="#2563eb"))
        btn_close.bind("<Leave>", lambda e: btn_close.config(bg="#3b82f6"))
        
        win.bind("<Escape>", lambda e: win.destroy())
        
        # Centrage
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        
        # Centrer sur l'écran global
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.focus_set()

    def _setup_manual_tab(self) -> None:
        """Initialise ou rafraîchit l'onglet Manuel."""
        for child in self.tab_manual.winfo_children():
            child.destroy()
        self._render_markdown_help_to_widget(self.tab_manual, is_tab=True)

    def _setup_notes_tab(self) -> None:
        """Initialise l'onglet Notes."""
        if hasattr(self, "txt_user_notes") and self.txt_user_notes.winfo_exists():
            return
            
        header = tk.Frame(self.tab_notes, bg="#fef3c7", padx=10, pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header, 
            text="Vous pouvez envoyer vos observations à mchvalidation@gmail.com",
            bg="#fef3c7",
            fg="#92400e",
            font=("Segoe UI", 10, "bold")
        ).pack(side=tk.LEFT)
        
        self.txt_user_notes = tk.Text(
            self.tab_notes,
            wrap=tk.WORD,
            bg="white",
            fg="#1f2937",
            font=("Consolas", 11),
            padx=15,
            pady=15,
            relief=tk.FLAT,
            undo=True
        )
        self.txt_user_notes.pack(fill=tk.BOTH, expand=True)
        
        existing_notes = self.config_data.get("user_notes", "")
        if existing_notes:
            self.txt_user_notes.insert("1.0", existing_notes)
            
        self.txt_user_notes.bind("<FocusOut>", lambda e: self._save_config())

    def _render_markdown_help_to_widget(self, container: tk.Misc, is_tab: bool = False) -> None:
        """Rendu du README.md formaté dans un widget donné."""
        readme_path = get_resource_path("README.md")
        if not readme_path.exists():
            readme_path = Path.cwd() / "README.md"
        
        content = ""
        if readme_path.exists():
            content = readme_path.read_text(encoding="utf-8")
        else:
            content = "# PhotoSelect\n\nAide indisponible (README.md introuvable)."

        bg_color = "#fafafa" if not is_tab else "#ffffff"
        
        frame = tk.Frame(container, bg=bg_color)
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(frame, wrap=tk.WORD, bg=bg_color, fg="#222", padx=20, pady=15,
                       font=("Segoe UI", 11), relief=tk.FLAT, cursor="arrow",
                       selectbackground="#cce5ff")
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        text.tag_configure("h1", font=("Segoe UI", 20, "bold"), foreground="#1a1a2e", spacing3=12)
        text.tag_configure("h2", font=("Segoe UI", 15, "bold"), foreground="#16213e", spacing1=15, spacing3=6)
        text.tag_configure("h3", font=("Segoe UI", 12, "bold"), foreground="#0f3460", spacing1=10, spacing3=4)
        text.tag_configure("bold", font=("Segoe UI", 11, "bold"))
        text.tag_configure("italic", font=("Segoe UI", 11, "italic"))
        text.tag_configure("code", font=("Consolas", 10), background="#eef2ff", foreground="#1f2937")
        text.tag_configure("code_block", font=("Consolas", 10), background="#1f2937", foreground="#f3f4f6",
                           lmargin1=20, lmargin2=20, rmargin=20, spacing1=5, spacing3=5)
        text.tag_configure("list_item", lmargin1=25, lmargin2=40)
        text.tag_configure("table", font=("Consolas", 10), foreground="#111827", spacing1=3, spacing3=3)
        text.tag_configure("table_header", font=("Consolas", 10, "bold"), foreground="#111827")

        in_code_block = False
        lines = content.split("\n")
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                idx += 1
                continue
            if in_code_block:
                text.insert(tk.END, line + "\n", "code_block")
                idx += 1
                continue

            if self._is_markdown_table_line(line):
                table_lines: list[str] = []
                while idx < len(lines) and self._is_markdown_table_line(lines[idx]):
                    table_lines.append(lines[idx])
                    idx += 1
                self._render_markdown_table(text, table_lines)
                continue

            if line.startswith("### "):
                text.insert(tk.END, line[4:] + "\n", "h3")
                idx += 1
                continue
            if line.startswith("## "):
                text.insert(tk.END, line[3:] + "\n", "h2")
                idx += 1
                continue
            if line.startswith("# "):
                text.insert(tk.END, line[2:] + "\n", "h1")
                idx += 1
                continue
            if line.startswith("- "):
                self._insert_formatted(text, "• " + line[2:] + "\n", "list_item")
                idx += 1
                continue
            
            self._insert_formatted(text, line + "\n")
            idx += 1
        
        text.configure(state="disabled")

    @staticmethod
    def _is_markdown_table_line(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2

    @staticmethod
    def _parse_markdown_table_row(line: str) -> list[str]:
        stripped = line.strip().strip("|")
        return [cell.strip() for cell in stripped.split("|")]

    @staticmethod
    def _is_markdown_table_separator(cells: list[str]) -> bool:
        if not cells:
            return False
        return all(bool(re.fullmatch(r":?-{3,}:?", cell.strip())) for cell in cells)

    @staticmethod
    def _strip_markdown_inline(text: str) -> str:
        """Nettoie les marqueurs inline Markdown pour l'affichage tabulaire texte."""
        value = str(text or "")
        value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
        value = re.sub(r"\*(.+?)\*", r"\1", value)
        value = re.sub(r"`(.+?)`", r"\1", value)
        return value

    def _render_markdown_table(self, text_widget: tk.Text, table_lines: list[str]) -> None:
        rows = [self._parse_markdown_table_row(line) for line in table_lines if self._is_markdown_table_line(line)]
        if not rows:
            return

        header = rows[0]
        body = rows[1:]
        if body and self._is_markdown_table_separator(body[0]):
            body = body[1:]

        col_count = max(len(header), *(len(row) for row in body)) if body else len(header)

        def _normalize(row: list[str]) -> list[str]:
            if len(row) < col_count:
                return row + [""] * (col_count - len(row))
            return row

        header = [self._strip_markdown_inline(cell) for cell in _normalize(header)]
        body = [[self._strip_markdown_inline(cell) for cell in _normalize(row)] for row in body]

        widths = [0] * col_count
        for row in [header] + body:
            for idx, cell in enumerate(row):
                widths[idx] = max(widths[idx], len(cell))

        border = "+" + "+".join("-" * (w + 2) for w in widths) + "+\n"

        def _format_row(row: list[str]) -> str:
            return "| " + " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)) + " |\n"

        text_widget.insert(tk.END, border, "table")
        text_widget.insert(tk.END, _format_row(header), ("table", "table_header"))
        text_widget.insert(tk.END, border, "table")
        for row in body:
            text_widget.insert(tk.END, _format_row(row), "table")
        text_widget.insert(tk.END, border + "\n", "table")


    def _insert_formatted(self, text_widget: tk.Text, content: str, base_tag: str = "") -> None:
        """Insère du texte avec gestion du **gras**, *italique* et `code` inline."""
        # Pattern pour **gras**, *italique*, `code`
        pattern = r'(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|([^*`]+))'
        for match in re.finditer(pattern, content):
            if match.group(2):  # **gras**
                tags = ("bold",) if not base_tag else (base_tag, "bold")
                text_widget.insert(tk.END, match.group(2), tags)
            elif match.group(3):  # *italique*
                tags = ("italic",) if not base_tag else (base_tag, "italic")
                text_widget.insert(tk.END, match.group(3), tags)
            elif match.group(4):  # `code`
                tags = ("code",) if not base_tag else (base_tag, "code")
                text_widget.insert(tk.END, match.group(4), tags)
            elif match.group(5):  # texte normal
                if base_tag:
                    text_widget.insert(tk.END, match.group(5), base_tag)
                else:
                    text_widget.insert(tk.END, match.group(5))

    def _open_folder_from_var(self, var: tk.StringVar) -> None:
        """Helper pour ouvrir le dossier contenu dans une variable Tk."""
        path_str = var.get().strip()
        if path_str:
            path = Path(path_str)
            if path.exists():
                import subprocess
                try:
                    subprocess.run(['explorer', str(path.resolve())])
                except Exception as e:
                    logger.error("Erreur ouverture dossier : %s", e)

    def _clear_table_display(self, message: str) -> None:
        """Vide l'affichage du tableau et réinitialise l'état visuel lié."""
        self._counter_hover_source = None
        self.tree.delete(*self.tree.get_children())
        self.progress.configure(value=0)
        self.var_counter.set(message)
        self.var_copies.set("Exportées : 0")
        for viewer in (self.viewer_photo, self.viewer_grid):
            viewer._current_path = None
            viewer.canvas.delete("all")
        self._update_logo_preview(None)
        self._virtual_album_sidebar_rows = []
        if hasattr(self, "var_virtual_album_header"):
            self.var_virtual_album_header.set("Albums (0)")
        if hasattr(self, "list_virtual_albums"):
            self._virtual_album_listbox_syncing = True
            try:
                self.list_virtual_albums.configure(state="normal")
                self.list_virtual_albums.delete(0, tk.END)
                self.list_virtual_albums.configure(state="disabled")
            finally:
                self._virtual_album_listbox_syncing = False
        self._reset_stats_display()

    def _reset_stats_display(self) -> None:
        """Réinitialise les valeurs de l'onglet statistiques."""
        if not hasattr(self, "_stats_vars"):
            return
        defaults = {
            "photos": "0",
            "dirs": "0",
            "size_total": "0 B",
            "size_avg": "0 B",
            "localized": "0.0 %",
            "geocoded": "0.0 %",
            "copied": "0.0 %",
            "cameras": "0",
            "top_camera": "-",
            "date_range": "-",
            "duplicate_health": "0 (0 groupe)",
            "faces_health": "0 / 0 / 0 / 0",
            "face_perf": "-",
            "face_runtime_status": str(getattr(self, "_face_runtime_status", "Désactivée") or "Désactivée"),
            "theme_runtime_status": str(getattr(self, "_theme_runtime_status", "Inactif") or "Inactif"),
            "theme_last_summary": str(getattr(self, "_theme_last_summary", "-") or "-"),
            "album_health": "-",
            "last_integrity_audit": str(self.config_data.get("stats_last_integrity_audit") or "-"),
        }
        for key, value in defaults.items():
            if key in self._stats_vars:
                self._stats_vars[key].set(value)

    def _update_statistics(self) -> None:
        """Calcule et met à jour les statistiques globales de la base courante."""
        if self._is_virtual_album_scope_active():
            rows = self._build_virtual_album_rows()
            total = len(rows)
            if total == 0:
                self._reset_stats_display()
                return

            total_size = sum(int(r.get("filesize") or 0) for r in rows)
            avg_size = int(total_size / total) if total else 0
            dirs = {str(Path(r["filepath"]).parent) for r in rows if r.get("filepath")}
            gps_count = sum(1 for r in rows if r.get("latitude") is not None and r.get("longitude") is not None)
            geocoded_count = sum(1 for r in rows if (r.get("country") or r.get("region") or r.get("city")))
            copied_count = sum(1 for r in rows if int(r.get("is_copied") or 0) == 1)

            camera_values = [str(r.get("camera_model") or "").strip() for r in rows]
            camera_values = [c for c in camera_values if c]
            camera_counter = Counter(camera_values)
            top_camera = camera_counter.most_common(1)[0][0] if camera_counter else "-"

            dates = [str(r.get("date_taken") or "").strip() for r in rows if str(r.get("date_taken") or "").strip()]
            if dates:
                date_from = min(dates)
                date_to = max(dates)
                date_range = f"{date_from}  ->  {date_to}" if date_from != date_to else date_from
            else:
                date_range = "-"

            active_album = self._get_active_virtual_album()
            active_album_name = str(active_album.get("name") or "-") if active_album else "-"

            self._stats_vars["photos"].set(str(total))
            self._stats_vars["dirs"].set(str(len(dirs)))
            self._stats_vars["size_total"].set(format_size(total_size))
            self._stats_vars["size_avg"].set(format_size(avg_size))
            self._stats_vars["localized"].set(f"{(gps_count * 100 / total):.1f} % ({gps_count}/{total})")
            self._stats_vars["geocoded"].set(f"{(geocoded_count * 100 / total):.1f} % ({geocoded_count}/{total})")
            self._stats_vars["copied"].set(f"{(copied_count * 100 / total):.1f} % ({copied_count}/{total})")
            self._stats_vars["cameras"].set(str(len(camera_counter)))
            self._stats_vars["top_camera"].set(top_camera)
            self._stats_vars["date_range"].set(date_range)
            self._stats_vars["duplicate_health"].set("N/A (mode Albums)")
            self._stats_vars["faces_health"].set("N/A (mode Albums)")
            self._stats_vars["face_perf"].set(self._format_face_engine_perf_line("opencv"))
            self._stats_vars["face_runtime_status"].set(str(getattr(self, "_face_runtime_status", "Désactivée") or "Désactivée"))
            self._stats_vars["theme_runtime_status"].set(str(getattr(self, "_theme_runtime_status", "Inactif") or "Inactif"))
            self._stats_vars["theme_last_summary"].set(str(getattr(self, "_theme_last_summary", "-") or "-"))
            self._stats_vars["album_health"].set(f"Album actif: {active_album_name} | Photos: {total}")
            self._stats_vars["last_integrity_audit"].set(str(self.config_data.get("stats_last_integrity_audit") or "-"))
            return

        if self._is_album_scope_active():
            rows = self._build_album_virtual_rows()
            total = len(rows)
            if total == 0:
                self._reset_stats_display()
                return

            total_size = sum(int(r.get("filesize") or 0) for r in rows)
            avg_size = int(total_size / total) if total else 0

            dirs = {str(Path(str(r.get("filepath") or "")).parent) for r in rows if r.get("filepath")}
            dates = [str(r.get("date_taken") or "").strip() for r in rows if str(r.get("date_taken") or "").strip()]
            if dates:
                date_from = min(dates)
                date_to = max(dates)
                date_range = f"{date_from}  ->  {date_to}" if date_from != date_to else date_from
            else:
                date_range = "-"

            root = self._normalize_album_root()
            unreadable_count = 0
            missing_count = 0
            audited_at = 0
            if root is not None:
                unreadable_count, missing_count, audited_at = self._get_album_audit_state(root)
            audit_stamp = datetime.fromtimestamp(audited_at).strftime("%Y-%m-%d %H:%M") if audited_at > 0 else "jamais"

            self._stats_vars["photos"].set(str(total))
            self._stats_vars["dirs"].set(str(len(dirs)))
            self._stats_vars["size_total"].set(format_size(total_size))
            self._stats_vars["size_avg"].set(format_size(avg_size))
            self._stats_vars["localized"].set("N/A (mode Sélection)")
            self._stats_vars["geocoded"].set("N/A (mode Sélection)")
            self._stats_vars["copied"].set("N/A (mode Sélection)")
            self._stats_vars["cameras"].set("N/A")
            self._stats_vars["top_camera"].set("N/A")
            self._stats_vars["date_range"].set(date_range)
            self._stats_vars["duplicate_health"].set("N/A (mode Sélection)")
            self._stats_vars["faces_health"].set("N/A (mode Sélection)")
            self._stats_vars["face_perf"].set(self._format_face_engine_perf_line("opencv"))
            self._stats_vars["face_runtime_status"].set(str(getattr(self, "_face_runtime_status", "Désactivée") or "Désactivée"))
            self._stats_vars["theme_runtime_status"].set(str(getattr(self, "_theme_runtime_status", "Inactif") or "Inactif"))
            self._stats_vars["theme_last_summary"].set(str(getattr(self, "_theme_last_summary", "-") or "-"))
            self._stats_vars["album_health"].set(
                f"Illisibles: {unreadable_count} | Manquantes: {missing_count} | Dernier audit: {audit_stamp}"
            )
            self._stats_vars["last_integrity_audit"].set(str(self.config_data.get("stats_last_integrity_audit") or "-"))
            return

        if not self.db:
            self._reset_stats_display()
            return

        rows = self.db.get_all_photos(order_by="id")
        total = len(rows)
        if total == 0:
            self._reset_stats_display()
            return

        total_size = sum(int(r.get("filesize") or 0) for r in rows)
        avg_size = int(total_size / total) if total else 0

        dirs = {str(Path(r["filepath"]).parent) for r in rows if r.get("filepath")}
        gps_count = sum(1 for r in rows if r.get("latitude") is not None and r.get("longitude") is not None)
        geocoded_count = sum(1 for r in rows if (r.get("country") or r.get("region") or r.get("city")))
        copied_count = sum(1 for r in rows if int(r.get("is_copied") or 0) == 1)
        duplicate_photos = len(self._duplicate_photo_ids) if self._duplicate_cache_valid else -1
        duplicate_groups = self._duplicate_group_count if self._duplicate_cache_valid else -1

        camera_values = [str(r.get("camera_model") or "").strip() for r in rows]
        camera_values = [c for c in camera_values if c]
        camera_counter = Counter(camera_values)
        top_camera = camera_counter.most_common(1)[0][0] if camera_counter else "-"

        dates = [str(r.get("date_taken") or "").strip() for r in rows if str(r.get("date_taken") or "").strip()]
        if dates:
            date_from = min(dates)
            date_to = max(dates)
            date_range = f"{date_from}  ->  {date_to}" if date_from != date_to else date_from
        else:
            date_range = "-"

        self._stats_vars["photos"].set(str(total))
        self._stats_vars["dirs"].set(str(len(dirs)))
        self._stats_vars["size_total"].set(format_size(total_size))
        self._stats_vars["size_avg"].set(format_size(avg_size))
        self._stats_vars["localized"].set(f"{(gps_count * 100 / total):.1f} % ({gps_count}/{total})")
        self._stats_vars["geocoded"].set(f"{(geocoded_count * 100 / total):.1f} % ({geocoded_count}/{total})")
        self._stats_vars["copied"].set(f"{(copied_count * 100 / total):.1f} % ({copied_count}/{total})")
        self._stats_vars["cameras"].set(str(len(camera_counter)))
        self._stats_vars["top_camera"].set(top_camera)
        self._stats_vars["date_range"].set(date_range)
        if duplicate_photos >= 0 and duplicate_groups >= 0:
            self._stats_vars["duplicate_health"].set(f"{duplicate_photos} ({duplicate_groups} groupe(s))")
        else:
            self._stats_vars["duplicate_health"].set("Non calcule (audit ou mode Doublons)")
        face_stats = self.db.get_face_stats()
        self._stats_vars["faces_health"].set(
            f"{int(face_stats.get('processed') or 0)} / {int(face_stats.get('photos_with_faces') or 0)} / {int(face_stats.get('faces_total') or 0)} / {int(face_stats.get('people_total') or 0)}"
        )
        self._update_face_engine_perf_stats_lines()
        self._stats_vars["face_runtime_status"].set(str(getattr(self, "_face_runtime_status", "Désactivée") or "Désactivée"))
        self._stats_vars["theme_runtime_status"].set(str(getattr(self, "_theme_runtime_status", "Inactif") or "Inactif"))
        self._stats_vars["theme_last_summary"].set(str(getattr(self, "_theme_last_summary", "-") or "-"))
        self._stats_vars["album_health"].set("N/A (mode Source)")
        self._stats_vars["last_integrity_audit"].set(str(self.config_data.get("stats_last_integrity_audit") or "-"))

    @staticmethod
    def _source_key(folder_path: Path) -> str:
        """Retourne une clé stable pour un dossier source (local ou réseau)."""
        normalized = str(folder_path.resolve()).lower()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def _get_db_file_for_source(self, folder_path: Path) -> Path:
        """Retourne le chemin DB (portable) situé dans _PhotoSelect."""
        meta_dir = folder_path / META_DIR_NAME
        ensure_directory(meta_dir)
        preferred = meta_dir / "main.sqlite3"
        alt1 = meta_dir / "main.db"
        alt2 = meta_dir / "photos.db"
        if not preferred.exists():
            if alt1.exists(): return alt1
            if alt2.exists(): return alt2
        return preferred

    def _get_cache_dir_for_source(self, folder_path: Path) -> Path:
        """Retourne le cache local des vignettes situé dans _PhotoSelect."""
        cache_dir = folder_path / META_DIR_NAME / "cache"
        ensure_directory(cache_dir)
        return cache_dir

    def _abs_path(self, rel_path: str | Path | None) -> Path | None:
        """Transforme un chemin relatif (en base) en chemin absolu."""
        if rel_path is None or str(rel_path).strip() == "":
            return None
        if not self._current_source_dir:
            try:
                return Path(str(rel_path))
            except Exception:
                return None
        p = Path(str(rel_path))
        if p.is_absolute():
            return p
        return self._current_source_dir / p

    def _rel_path(self, abs_path: Path | str | None) -> str:
        """Transforme un chemin absolu en chemin relatif par rapport à la source active."""
        if abs_path is None:
            return ""
        try:
            p = Path(str(abs_path)).resolve()
        except Exception:
            return str(abs_path)
            
        if not self._current_source_dir:
            return str(p)
        try:
            # Sur Windows, on normalise tout en minuscule pour la comparaison de parenté
            root_norm = os.path.normcase(str(self._current_source_dir.resolve()))
            p_norm = os.path.normcase(str(p))
            
            if p_norm.startswith(root_norm):
                # On tente le relative_to standard
                try:
                    return str(p.relative_to(self._current_source_dir.resolve()))
                except ValueError:
                    # Fallback manuel si relative_to chipote sur des détails (ex: slashs)
                    rel = p_norm[len(root_norm):].lstrip(os.sep)
                    return rel
            return str(p)
        except Exception:
            return str(p)

    def _cancel_background_count(self) -> None:
        """Arrête le comptage asynchrone en cours (si présent)."""
        self._count_stop_event.set()
        if self._count_thread and self._count_thread.is_alive():
            self._count_thread.join(timeout=0.5)
        self._count_thread = None

    def _start_background_count(self, folder_path: Path) -> None:
        """Lance un comptage des images en tâche de fond pour le dossier source."""
        self._cancel_background_count()
        self._count_stop_event.clear()
        self._count_request_id += 1
        request_id = self._count_request_id
        self.var_counter.set("Comptage des fichiers image en arrière-plan...")
        self._count_thread = threading.Thread(
            target=self._count_files_worker,
            args=(folder_path, request_id),
            daemon=True,
        )
        self._count_thread.start()

    def _count_files_worker(self, folder_path: Path, request_id: int) -> None:
        """Compte les fichiers image de manière interruptible pour informer l'utilisateur."""
        try:
            count = 0
            for root, dirnames, filenames in os.walk(folder_path, topdown=True):
                if self._count_stop_event.is_set() or request_id != self._count_request_id:
                    return

                root_path = Path(root)
                kept_dirs: list[str] = []
                for dirname in dirnames:
                    dir_path = root_path / dirname
                    if self._is_path_excluded_from_source_scan(dir_path, folder_path):
                        continue
                    kept_dirs.append(dirname)
                dirnames[:] = kept_dirs

                for filename in filenames:
                    if self._count_stop_event.is_set() or request_id != self._count_request_id:
                        return
                    file_path = root_path / filename
                    if self._is_path_excluded_from_source_scan(file_path, folder_path):
                        continue
                    if is_supported_image(file_path):
                        count += 1
            if self._count_stop_event.is_set() or request_id != self._count_request_id:
                return
            self.after(0, lambda: self.var_counter.set(f"Source analysée : {count} fichiers image détectés."))
        except Exception as e:
            if self._count_stop_event.is_set() or request_id != self._count_request_id:
                return
            self.after(0, lambda msg=str(e): self.var_counter.set(f"Comptage image impossible : {msg}"))

    def _ask_in(self) -> None:
        previous_in = self._normalize_saved_path(self.var_in.get())
        initial = self.config_data.get("last_in", "")
        folder = filedialog.askdirectory(title="Dossier source des photos", initialdir=initial if initial else None)
        if folder:
            normalized = self._add_saved_path("source", folder)
            if not normalized:
                return
            changed = Path(normalized).resolve() != Path(previous_in).resolve() if previous_in else True
            self.var_in.set(normalized)
            self._on_source_selector_changed()
            if not changed and Path(normalized).exists() and not self.db:
                self._init_db(Path(normalized))

    def _sync_single_file_from_watcher(self, path: Path) -> None:
        """Synchronise un fichier unique détecté par le Watcher."""
        if not self.db or not path.exists():
            return
        if self._is_path_excluded_from_source_scan(path):
            return
        try:
            # On vérifie si déjà là
            rel = self._rel_path(path)
            existing = self.db.get_photo_by_filepath(rel)
            if existing:
                return
            
            # Nouveau fichier: on extrait les métas
            meta = extract_all_metadata(path)
            meta["filepath"] = rel
            self.db.insert_photos([meta])
            logger.info("[watcher] Sync ajout: %s", path.name)
            self._invalidate_duplicate_cache()
            
            self._schedule_watcher_ui_refresh()
        except Exception as e:
            logger.error("[watcher] Erreur sync ajout %s: %s", path.name, e)

    def _sync_deletion_from_watcher(self, path: Path) -> None:
        """Supprime une photo de la base si supprimée du disque."""
        if not self.db:
            return
        if self._is_path_excluded_from_source_scan(path):
            return
        rel = self._rel_path(path)
        try:
            deleted_count = int(self.db.delete_photos_by_filepaths([rel]) or 0)
            if deleted_count <= 0:
                logger.debug("[watcher] Suppression ignorée (hors DB ou déjà absente): %s", path)
                return

            logger.info("[watcher] Sync suppression: %s", path.name)
            self._invalidate_duplicate_cache()
            self._schedule_watcher_ui_refresh()
        except Exception as e:
            logger.error("[watcher] Erreur sync suppression %s: %s", path.name, e)

    def _init_db(
        self,
        folder_path: Path,
        migrate_legacy: bool = True,
        cleanup_entries: bool = True,
        start_face_worker: bool = True,
        update_copies: bool = True,
        schedule_faces_preload: bool = True,
    ) -> None:
        self._cancel_pending_startup_load()
        if self.db:
            try: self.db.close()
            except Exception: pass

        self._init_project_paths(folder_path)
        db_file = self._get_db_file_for_source(folder_path)
        logger.info("[db] Ouverture de la base : %s", db_file.resolve())
        legacy_db = folder_path / ".photoselect.db"

        # TENTATIVE DE MIGRATION DEPUIS APPDATA (S'il n'y a pas encore de données portables ou si elles sont vides)
        should_migrate = not db_file.exists()
        if not should_migrate and db_file.exists():
            # Vérification rapide du contenu
            try:
                check_db = Database(db_file)
                if check_db.get_photo_count() == 0:
                    should_migrate = True
                check_db.close()
            except Exception:
                should_migrate = True

        if should_migrate:
            # Calcul du hash global historique
            import hashlib
            source_hash = hashlib.sha1(str(folder_path.resolve()).lower().encode()).hexdigest()
            global_db = APP_DB_DIR / f"{source_hash}.sqlite3"
            
            logger.info("[db] Evaluation migration : global_db=%s exists=%s", global_db.name, global_db.exists())
            if global_db.exists():
                g_size = global_db.stat().st_size
                p_size = db_file.stat().st_size if db_file.exists() else 0
                logger.info("[db] Comparaison tailles : Global=%d bytes, Portable=%d bytes", g_size, p_size)
                
                # On migre si la base portable est plus petite ou si elle est vide
                if not db_file.exists() or should_migrate:
                    try:
                        shutil.copy2(global_db, db_file)
                        logger.info("[db] Migration REUSSIE depuis la base globale AppData (%s)", source_hash)
                        
                        # Relativisation immédiate des chemins pour le mode portable
                        temp_db = Database(db_file)
                        try:
                            with temp_db._conn:
                                # On transforme les chemins absolus en relatifs par rapport à la source
                                source_prefix = str(folder_path.resolve()) + os.sep
                                temp_db._conn.execute(
                                    "UPDATE photos SET filepath = SUBSTR(filepath, LENGTH(?) + 1) WHERE filepath LIKE (? || '%')",
                                    (source_prefix, source_prefix)
                                )
                            logger.info("[db] Relativisation des chemins effectuee apres migration.")
                        finally:
                            temp_db.close()
                            
                        # MIGRATION DES ALBUMS VIRTUELS SI POSSIBLE
                        global_album_index = APP_DB_DIR / "_album_index.sqlite3"
                        if global_album_index.exists():
                            try:
                                if not self._album_index_db.exists():
                                    shutil.copy2(global_album_index, self._album_index_db)
                                    logger.info("[db] Migration des albums virtuels effectuee.")
                            except Exception as e:
                                logger.warning("[db] Echec migration albums: %s", e)
                    except Exception as e:
                        logger.warning("[db] Echec migration depuis AppData: %s", e)

        if migrate_legacy and not db_file.exists() and legacy_db.exists():
            try:
                shutil.copy2(legacy_db, db_file)
                logger.info("Migration DB locale effectuée depuis %s", legacy_db)
            except Exception as e:
                logger.warning("Migration DB locale impossible (%s): %s", legacy_db, e)

        # Migration forcée de main.db vers main.sqlite3 pour cohérence si besoin
        if db_file.suffix == ".db" and db_file.name == "main.db":
            target_sqlite3 = db_file.with_suffix(".sqlite3")
            if not target_sqlite3.exists():
                try:
                    db_file.rename(target_sqlite3)
                    db_file = target_sqlite3
                    logger.info("Base renommée de main.db vers main.sqlite3.")
                except Exception:
                    pass

        self.db = Database(db_file)
        # Reset cache de chemin pour le scan optimisé
        if hasattr(self, "_cached_meta_dir_norm"):
            delattr(self, "_cached_meta_dir_norm")
        
        count = self.db.get_photo_count()
        size_kb = db_file.stat().st_size / 1024 if db_file.exists() else 0
        logger.info("[db] Base chargee : %d photos trouvees. Taille: %.1f KB", count, size_kb)
        self._current_source_dir = folder_path.resolve()
        if cleanup_entries:
            self._cleanup_db_entries_for_source()
        self._apply_active_selection(refresh_sidebar=True)
        self._invalidate_duplicate_cache()
        self.db.set_face_index_enabled(
            bool(self.config_data.get("face_recognition_enabled", False)),
            self._get_face_engine_backend(),
        )
        if start_face_worker:
            self._ensure_face_worker_running()
        self._ensure_theme_worker_running()
        if schedule_faces_preload:
            self._start_faces_background_preload_scheduler()
        if update_copies:
            self._update_copies_counter()

    def _cleanup_db_entries_for_source(self) -> None:
        """Supprime les entrées DB invalides (fichiers absents) et les doublons de chemin canonique."""
        if not self.db:
            return

        rows = self.db.get_all_photos(order_by="id")
        missing_ids, duplicate_ids = self._collect_db_cleanup_ids(rows, self._current_source_dir)

        deleted = 0
        for photo_id in missing_ids + duplicate_ids:
            self.db.delete_photo(photo_id)
            deleted += 1

        if deleted:
            logger.info(
                "Auto-nettoyage DB: %d entrée(s) supprimée(s) (absentes=%d, doublons=%d).",
                deleted,
                len(missing_ids),
                len(duplicate_ids),
            )

    def _ask_out(self) -> None:
        initial = self.config_data.get("last_out", "")
        folder = filedialog.askdirectory(title="Dossier de sortie (copie)", initialdir=initial if initial else None)
        if folder:
            normalized = self._add_saved_path("album_output", folder)
            if not normalized:
                return
            self.var_out.set(normalized)
            self._on_output_selector_changed()

    def _start_scan(self, resume: bool = True) -> None:
        self._cancel_background_count()
        p_in = self.var_in.get().strip()
        p_out = self.var_out.get().strip()

        if not p_in:
            messagebox.showwarning("Attention", "Veuillez sélectionner un dossier source.")
            return

        path_in = Path(p_in)
        path_out = Path(p_out)

        normalized_in = self._add_saved_path("source", str(path_in))
        if normalized_in:
            path_in = Path(normalized_in)
            self.var_in.set(normalized_in)
        
        # Le dossier de sortie est géré automatiquement dans _PhotoSelect
        path_out = path_in / META_DIR_NAME / "Sélection"
        self.var_out.set(str(path_out))
        
        self.config_data["last_in"] = str(path_in)
        self.config_data["last_out"] = str(path_out)
        self._save_config()
        logger.info("[scan] Demande scan: source=%s out=%s resume=%s", path_in, path_out, resume)

        if not path_in.exists():
            messagebox.showerror("Erreur", "Le dossier d'entrée spécifié n'existe pas.")
            return

        ensure_directory(path_out)

        try:
            if path_in.resolve() == path_out.resolve():
                messagebox.showerror("Erreur", "Le dossier de sortie ne peut pas être exactement le dossier source.")
                return
        except Exception:
            pass

        if self._current_source_dir and self._current_source_dir != path_in.resolve():
            self._clear_table_display("Changement de dossier source détecté. Tableau vidé.")

        if not resume:
            if not messagebox.askyesno("Confirmation", "Voulez-vous vraiment vider la base de données et TOUT re-scanner ?"):
                return

        self._init_db(path_in, migrate_legacy=resume)
            
        self.btn_open.config(state="disabled")
        self.btn_refresh.config(state="disabled")
        self.btn_rename.config(state="disabled")
        self.btn_purge.config(state="disabled")
        self.btn_theme_classify.config(state="disabled")
        self.btn_normalize_geo.config(state="disabled")
        self.btn_reindex_album.config(state="disabled")
        self.btn_integrity.config(state="disabled")
        self.btn_export_log.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.var_counter.set("Recherche des fichiers image... (GPS local force pour scan global)")
        self.progress.configure(value=0, maximum=100)
        
        self._stop_event.clear()
        scan_geo_mode = "local"
        self._scan_thread = threading.Thread(target=self._scan_worker, args=(path_in, resume, scan_geo_mode), daemon=True)
        self._scan_thread.start()

    def _scan_worker(self, path_in: Path, resume: bool, scan_geo_mode: str = "local") -> None:
        if not self.db:
            logger.warning("[scan] Worker démarré sans base active: abort.")
            return
        logger.info("[scan] Worker démarré: source=%s resume=%s geo_mode=%s", path_in, resume, scan_geo_mode)
        try:
            raw_jpgs = list_jpg_files(path_in)
        except Exception as e:
            logger.exception("[scan] Erreur listage fichiers: %s", e)
            self._update_counter_threadsafe(f"Erreur recherche : {e}")
            self._finish_scan()
            return

        raw_total_files = len(raw_jpgs)
        all_jpgs = [p for p in raw_jpgs if not self._is_path_excluded_from_source_scan(p, path_in)]
        excluded_files = max(0, raw_total_files - len(all_jpgs))
        logger.info(
            "[scan] Fichiers trouvés sur disque: bruts=%d exclus=%d gardés=%d (source=%s)",
            raw_total_files,
            excluded_files,
            len(all_jpgs),
            path_in,
        )
            
        total_files = len(all_jpgs)
        if total_files == 0:
            self._update_counter_threadsafe("Aucun fichier image trouvé.")
            self._finish_scan()
            return

        if not resume:
            try:
                self.db.clear_photos()
                self.after(0, lambda: self.tree.delete(*self.tree.get_children()))
                self.after(0, self._reset_stats_display)
            except Exception as e:
                self._update_counter_threadsafe(f"Erreur préparation re-scan : {e}")
                self._finish_scan()
                return

        already_processed_count = 0

        def _safe_resolve(path: Path) -> Path:
            try:
                return path.resolve(strict=False)
            except Exception:
                return path

        def _canon(path: Path) -> str:
            try:
                return os.path.normcase(str(_safe_resolve(path)))
            except Exception:
                return os.path.normcase(str(path))

        if resume:
            existing_photos = self.db.get_all_photos(order_by="id")
            all_jpg_resolved = {_canon(p) for p in all_jpgs}

            # Purge les entrées DB dont le fichier source a disparu.
            missing_ids: list[int] = []
            duplicate_ids: list[int] = []
            existing_filepaths: set[str] = set()
            for photo in existing_photos:
                raw_path = str(photo.get("filepath") or "").strip()
                if not raw_path:
                    missing_ids.append(int(photo["id"]))
                    continue
                resolved = _canon(Path(raw_path))
                if resolved not in all_jpg_resolved:
                    missing_ids.append(int(photo["id"]))
                    continue
                if resolved in existing_filepaths:
                    duplicate_ids.append(int(photo["id"]))
                    continue
                existing_filepaths.add(resolved)

            if missing_ids or duplicate_ids:
                for missing_id in missing_ids:
                    self.db.delete_photo(missing_id)
                for duplicate_id in duplicate_ids:
                    self.db.delete_photo(duplicate_id)
                logger.info(
                    "Purge scan reprise: %d absente(s), %d doublon(s) supprimé(s).",
                    len(missing_ids),
                    len(duplicate_ids),
                )

            jpgs = [f for f in all_jpgs if _canon(f) not in existing_filepaths]
            already_processed_count = len(existing_filepaths)
            logger.info(
                "[scan] Mode reprise: en_base=%d nouveaux=%d total_disque=%d",
                already_processed_count,
                len(jpgs),
                total_files,
            )
        else:
            jpgs = all_jpgs

        if len(jpgs) == 0:
            self._update_counter_threadsafe("Vérification et mise à jour des noms de collection...")
            self.db.update_collection_names()
            sticky_msg = (
                f"Scan disque terminé: 0 nouveau fichier. Base déjà à jour "
                f"({already_processed_count}/{total_files}). Pour retraiter les existants: Vider / Re-scanner."
            )
            self.after(0, lambda msg=sticky_msg: self._set_sticky_counter_message(msg, ttl_sec=12.0))
            self.after(0, lambda: self.progress.configure(mode="determinate", maximum=max(1, total_files), value=min(total_files, max(1, total_files))))
            self._refresh_table_threadsafe()
            self._finish_scan()
            return

        if resume and already_processed_count > 0:
            msg = f"Reprise du scan: {len(jpgs)} restants sur {total_files}. Extraction ..."
            logger.info(msg)
            self._update_counter_threadsafe(msg)
        else:
            msg = f"Trouvé {total_files} fichiers. Extraction des métadonnées..."
            logger.info(msg)
            self._update_counter_threadsafe(msg)

        self.after(
            0,
            lambda: self.progress.configure(
                mode="determinate",
                maximum=max(1, total_files),
                value=min(already_processed_count, max(1, total_files)),
            ),
        )

        count = already_processed_count
        failed_files = 0
        progress_refresh_interval_sec = 3.0
        last_progress_refresh_ts = 0.0
        
        start_time = time.time()
        start_count = count
        cache_dir = self._get_cache_dir_for_source(path_in)

        # Nettoyage best-effort des vignettes orphelines lors d'un scan.
        try:
            valid_hashes = {
                hashlib.md5(self._rel_path(p).encode()).hexdigest()
                for p in all_jpgs
            }
            removed_thumbs = 0
            for thumb in cache_dir.glob("*.jpg"):
                if thumb.stem not in valid_hashes:
                    try:
                        thumb.unlink()
                        removed_thumbs += 1
                    except Exception:
                        pass
            if removed_thumbs:
                logger.info("Nettoyage cache vignettes: %d fichier(s) orphelin(s) supprimé(s).", removed_thumbs)
        except Exception as e:
            logger.debug("Nettoyage cache ignoré: %s", e)

        geo_mode = "local" if scan_geo_mode not in ("local", "online") else scan_geo_mode

        def process_file(filepath: Path) -> tuple[dict[str, Any] | None, str | None]:
            size = get_file_size(filepath)
            try:
                from photoselect.exif_reader import extract_all_metadata
                meta = extract_all_metadata(filepath, mode=geo_mode)
                meta["filesize"] = size
                # Stockage RELATIF dans la base de données
                meta["filepath"] = self._rel_path(filepath)

                # Génération des vignettes (légères, hash relatif pour portabilité)
                try:
                    generate_thumbnail(filepath, cache_dir, use_rel_hash_of=path_in)
                except Exception:
                    pass
                return (meta, None)
            except Exception as exc:
                return (None, f"{filepath}: {exc}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            # On traite par petits lots pour garder l'UI réactive et sauvegarder régulièrement
            file_chunks = [jpgs[i:i + 100] for i in range(0, len(jpgs), 100)]
            
            for chunk in file_chunks:
                if self._stop_event.is_set():
                    break
                
                # Extraction parallèle pour le lot actuel
                logger.info("Traitement d'un lot de %d fichiers...", len(chunk))
                results = list(executor.map(process_file, chunk))

                ok_rows: list[dict[str, Any]] = []
                for meta, error_text in results:
                    if meta is not None:
                        ok_rows.append(meta)
                        continue
                    failed_files += 1
                    if error_text:
                        logger.warning("[scan] Fichier ignoré (erreur metadata): %s", error_text)

                # Insertion en base uniquement des fichiers valides.
                if ok_rows:
                    self.db.insert_photos(ok_rows)
                count += len(ok_rows)
                
                # Mise à jour UI proposée (vitesse, temps...)
                elapsed = time.time() - start_time
                processed_in_session = count - start_count
                if elapsed > 0 and processed_in_session > 0:
                    speed_per_sec = processed_in_session / elapsed
                    speed_per_hour = int(speed_per_sec * 3600)
                    remaining_files = total_files - count
                    remaining_secs = remaining_files / speed_per_sec if speed_per_sec > 0 else 0
                    
                    hh = int(remaining_secs // 3600)
                    mm = int((remaining_secs % 3600) // 60)
                    ss = int(remaining_secs % 60)
                    rem_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
                    
                    msg = (
                        f"Parallélisation active | Traitées : {count}/{total_files} "
                        f"| Erreurs : {failed_files} | Vitesse : {speed_per_hour} /h | Restant : {rem_str}"
                    )
                    self._update_counter_threadsafe(msg)

                now_ts = time.time()
                if (now_ts - last_progress_refresh_ts) >= progress_refresh_interval_sec or count >= total_files:
                    self.after(
                        0,
                        lambda c=count, t=total_files: self.progress.configure(
                            mode="determinate",
                            maximum=max(1, t),
                            value=min(c, max(1, t)),
                        ),
                    )
                    last_progress_refresh_ts = now_ts

        if self._stop_event.is_set():
            self._update_counter_threadsafe("Mise à jour des noms de collection...")
            self.db.update_collection_names()
            self._invalidate_duplicate_cache()
            self._update_counter_threadsafe(f"Scan interrompu proprement à {count}/{total_files} (erreurs: {failed_files}).")
            logger.info("[scan] Interrompu: traitées=%d total=%d erreurs=%d", count, total_files, failed_files)
            self._refresh_table_threadsafe()
            self._finish_scan()
            return

        self._update_counter_threadsafe("Génération des collections chronologiques...")
        self.after(
            0,
            lambda: self.progress.configure(
                mode="determinate",
                maximum=max(1, total_files),
                value=min(total_files, max(1, total_files)),
            ),
        )
        self.db.update_collection_names()
        self._invalidate_duplicate_cache()

        final_msg = (
            f"Terminé ! Scan disque effectué ({total_files} fichiers détectés, "
            f"{count} traités, erreurs: {failed_files})."
        )
        logger.info("[scan] Fin: detectés=%d traités=%d erreurs=%d", total_files, count, failed_files)
        self.after(0, lambda msg=final_msg: self._set_sticky_counter_message(msg, ttl_sec=10.0))
        self._refresh_table_threadsafe()
        self._finish_scan()

    def _update_counter_threadsafe(self, text: str) -> None:
        self.after(0, lambda: self.var_counter.set(text))

    def _begin_photo_progress(self, total: int, label: str) -> None:
        """Initialise la progression pour une opération photo longue."""
        self.progress.configure(maximum=max(1, total), value=0)
        self.var_counter.set(f"{label}: 0/{total}")

    def _tick_photo_progress(self, processed: int, total: int, label: str) -> None:
        """Met à jour la progression toutes les 10 photos (et à la fin)."""
        if processed % 10 != 0 and processed != total:
            return
        self.progress.configure(value=min(processed, max(1, total)))
        self.var_counter.set(f"{label}: {processed}/{total}")
        self.update_idletasks()

    def _end_photo_progress(self, total: int, label: str) -> None:
        """Finalise la progression d'une opération photo longue."""
        self.progress.configure(maximum=max(1, total), value=max(1, total))
        self.var_counter.set(f"{label}: terminé ({total}/{total})")

    @staticmethod
    def _format_eta(seconds: float | None) -> str:
        if seconds is None:
            return ""
        sec = max(0, int(round(seconds)))
        minutes, remaining = divmod(sec, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f" (~reste {hours:02d}h{minutes:02d}m)"
        if minutes > 0:
            return f" (~reste {minutes:02d}m{remaining:02d}s)"
        return f" (~reste {remaining}s)"

    def _tick_photo_progress_threadsafe(self, processed: int, total: int, label: str, eta_seconds: float | None = None) -> None:
        """Met à jour la progression depuis un thread de fond."""
        if processed % 10 != 0 and processed != total:
            return

        def _apply() -> None:
            self.progress.configure(value=min(processed, max(1, total)), maximum=max(1, total))
            self.var_counter.set(f"{label}: {processed}/{total}{self._format_eta(eta_seconds)}")

        self.after(0, _apply)

    def _refresh_table_threadsafe(self) -> None:
        self.after(0, self._load_data_to_treeview)
        self.after(0, self._update_copies_counter)
        self.after(0, self._update_statistics)

    def _invalidate_duplicate_cache(self) -> None:
        self._duplicate_photo_ids = set()
        self._duplicate_group_count = 0
        self._duplicate_cache_valid = False

    def _refresh_duplicate_cache(self, force: bool = False) -> None:
        if self._duplicate_cache_valid and not force:
            return
        if not self.db:
            self._invalidate_duplicate_cache()
            return
        ids, groups = self._get_active_duplicate_info()
        self._duplicate_photo_ids = ids
        self._duplicate_group_count = int(groups)
        self._duplicate_cache_valid = True

    def _prepare_duplicate_scope_async(self, reindex: bool) -> None:
        """Prépare le scope Doublons en arrière-plan pour garder l'UI fluide."""
        if not self.db or self._duplicate_prepare_in_progress:
            return

        self._duplicate_prepare_prev_counter = str(self.var_counter.get() or "") if hasattr(self, "var_counter") else ""

        if reindex:
            self._invalidate_duplicate_cache()

        self._duplicate_prepare_in_progress = True
        self._duplicate_prepare_thread = None
        try:
            self.progress.stop()
        except Exception:
            pass

        if reindex:
            self.progress.configure(mode="determinate", maximum=1, value=0)
            self.var_counter.set("Préparation doublons: indexation...")
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start(10)
            self.var_counter.set("Préparation doublons...")
        self.update_idletasks()

        def _progress(done: int, total: int) -> None:
            self.after(0, lambda d=done, t=total: self._apply_duplicate_prepare_progress(d, t))

        def _worker() -> None:
            error_text = ""
            ids: set[int] = set()
            groups = 0
            try:
                if reindex:
                    self.db.build_duplicate_index(progress_callback=_progress, cancel_check=self._stop_event.is_set)
                ids, groups = self._get_active_duplicate_info()
            except Exception as exc:
                error_text = str(exc)

            self.after(0, lambda: self._on_duplicate_scope_ready(ids, groups, error_text))

        self._duplicate_prepare_thread = threading.Thread(target=_worker, daemon=True)
        self._duplicate_prepare_thread.start()

    def _apply_duplicate_prepare_progress(self, done: int, total: int) -> None:
        if not self._duplicate_prepare_in_progress:
            return
        self.progress.configure(mode="determinate", maximum=max(1, total), value=min(done, max(1, total)))
        self.var_counter.set(f"Préparation doublons : {done}/{total}")

    def _on_duplicate_scope_ready(self, ids: set[int], groups: int, error_text: str) -> None:
        self._duplicate_prepare_in_progress = False
        self._duplicate_prepare_thread = None
        try:
            self.progress.stop()
        except Exception:
            pass
        self.progress.configure(mode="determinate", value=0)

        if error_text:
            self._invalidate_duplicate_cache()
            self.var_counter.set(f"Préparation doublons échouée: {error_text}")
            self._duplicate_prepare_prev_counter = ""
            return

        self._duplicate_photo_ids = set(ids)
        self._duplicate_group_count = int(groups)
        self._duplicate_cache_valid = True

        # Les stats peuvent dépendre du cache doublons même hors scope Doublons.
        self._update_statistics()

        if self._is_duplicates_scope_active():
            self.var_counter.set(f"Doublons prêts: {len(self._duplicate_photo_ids)} photos ({self._duplicate_group_count} groupes).")
            self._load_data_to_treeview()
            self._update_copies_counter()
        else:
            current = str(self.var_counter.get() or "") if hasattr(self, "var_counter") else ""
            if current.startswith("Préparation doublons"):
                fallback = self._duplicate_prepare_prev_counter.strip() or "Prêt. Sélectionnez les dossiers."
                self.var_counter.set(fallback)

        self._duplicate_prepare_prev_counter = ""

    def _finish_scan(self) -> None:
        self.after(0, lambda: self.btn_open.config(state="normal"))
        self.after(0, lambda: self.btn_refresh.config(state="normal"))
        self.after(0, lambda: self.btn_rename.config(state="normal"))
        self.after(0, lambda: self.btn_purge.config(state="normal"))
        self.after(0, lambda: self.btn_theme_classify.config(state="normal"))
        self.after(0, lambda: self.btn_normalize_geo.config(state="normal"))
        self.after(0, lambda: self.btn_reindex_album.config(state="normal"))
        self.after(0, lambda: self.btn_integrity.config(state="normal"))
        self.after(0, lambda: self.btn_export_log.config(state="normal"))
        self.after(0, lambda: self.btn_stop.config(state="disabled"))
        
        # Réinitialisation de la vue pour éviter un écran vide (si on était en mode Doublons/Visages par ex.)
        self.after(0, lambda: self._reset_view_to_standard_source())
        
        # On s'assure que les workers IA reprennent leur travail sur les nouveaux fichiers
        self.after(500, self._ensure_face_worker_running)
        self.after(500, self._ensure_theme_worker_running)

    def _reset_view_to_standard_source(self) -> None:
        """Remet la vue à zéro (Source, pas de filtre) pour montrer les résultats du scan."""
        if hasattr(self, "var_search"):
            self.var_search.set("")
        if hasattr(self, "var_viewer_scope"):
            self.var_viewer_scope.set("folder")
        self._load_data_to_treeview()
        logger.info("[scan] Vue réinitialisée sur Source (toutes les photos).")

    # ── RENOMMAGE ───────────────────────────────────────────────────

    @staticmethod
    def _looks_like_camera_or_whatsapp_name(stem: str) -> bool:
        """Détecte les noms auto-générés (appareil photo/téléphone/WhatsApp)."""
        if not stem:
            return False

        name = stem.strip().upper()
        patterns = (
            r"(?:IMG|VID|AUD|PTT)-\d{8}-WA\d{4,6}(?:\(\d+\))?",  # WhatsApp
            r"(?:IMG|DSC|PXL|VID|MVIMG|MVI|MOV|GOPR|DJI|SAM|CIMG|DSCN|RSCN|PICT|PHOTO|_MG|DSCF)[-_]?\d{3,}(?:[_-]\d+)*",
            r"\d{8}[_-]\d{6}(?:[_-]\d+)?",                        # YYYYMMDD_HHMMSS
            r"PXL_\d{8}_\d{6,}(?:_[A-Z0-9]+)?",
        )
        return any(re.fullmatch(pattern, name) is not None for pattern in patterns)
    
    def _start_rename(self) -> None:
        if not self.db: return
        dlg = tk.Toplevel(self)
        dlg.title("⚠ Renommage des originaux")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        frame = tk.Frame(dlg, padx=14, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            justify="left",
            anchor="w",
            text=(
                "Cette action va renommer DÉFINITIVEMENT tous les fichiers originaux sur le disque.\n\n"
                "Chaque fichier sera renommé avec la valeur affichée dans la colonne \"Nom\" du tableau.\n\n"
                "Exemple :\n"
                "  IMG_20240315_142030.jpg  ->  240315_France_Paris-001.jpg\n\n"
                "⚠ Cette opération est irréversible. Vérifiez les noms avant de continuer."
            ),
        ).pack(fill=tk.X)

        protect_var = tk.BooleanVar(value=self.config_data.get("rename_protect_original_names", True))

        def _persist_protection(*_args: Any) -> None:
            self.config_data["rename_protect_original_names"] = bool(protect_var.get())
            self._save_config()

        protect_var.trace_add("write", _persist_protection)
        tk.Checkbutton(
            frame,
            text=(
                "Protéger les noms originaux (recommandé) :\n"
                "si le nom actuel ne ressemble pas à un nom appareil photo/WhatsApp, il est conservé."
            ),
            variable=protect_var,
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, pady=(10, 4))

        result = {"confirmed": False}

        def _confirm(_event: tk.Event | None = None) -> None:
            result["confirmed"] = True
            dlg.destroy()

        def _cancel(_event: tk.Event | None = None) -> None:
            dlg.destroy()

        buttons = tk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(10, 0))
        tk.Button(buttons, text="Continuer", width=14, command=_confirm, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(buttons, text="Annuler", width=14, command=_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT)

        dlg.bind("<Return>", _confirm)
        dlg.bind("<Escape>", _cancel)

        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.focus_force()
        self.wait_window(dlg)

        if not result["confirmed"]:
            return

        self._rename_protect_original_names = bool(protect_var.get())

        self.btn_open.config(state="disabled")
        self.btn_refresh.config(state="disabled")
        self.btn_rename.config(state="disabled")
        self.btn_purge.config(state="disabled")
        self.btn_normalize_geo.config(state="disabled")
        self.btn_integrity.config(state="disabled")
        self.btn_export_log.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.var_counter.set("Préparation du renommage...")
        self._stop_event.clear()
        
        threading.Thread(target=self._rename_worker, daemon=True).start()

    def _rename_worker(self) -> None:
        if not self.db: return
        photos = self.db.get_all_photos(order_by="id")
        total = len(photos)
        protect_original_names = bool(
            getattr(self, "_rename_protect_original_names", self.config_data.get("rename_protect_original_names", True))
        )
        
        if total == 0:
            self._update_counter_threadsafe("Aucune photo dans la base.")
            self._finish_scan()
            return
            
        self.after(0, lambda: self.progress.configure(value=0, maximum=total))
        
        renamed_count = 0
        skipped_protected = 0
        for i, photo in enumerate(photos):
            if self._stop_event.is_set():
                break
                
            photo_id = photo["id"]
            source_path = Path(photo["filepath"])
            collection_name = photo["collection_name"]
            
            if collection_name and source_path.exists():
                if protect_original_names and not self._looks_like_camera_or_whatsapp_name(source_path.stem):
                    skipped_protected += 1
                    continue

                new_filename = collection_name + source_path.suffix
                if source_path.name != new_filename:
                    new_path = source_path.with_name(new_filename)
                    if not new_path.exists():
                        try:
                            source_path.rename(new_path)
                            self.db.update_photo_path(photo_id, str(new_path), new_filename)
                            renamed_count += 1
                        except Exception as e:
                            logger.error("Erreur renommage: %s", e)
            
            if i % 10 == 0:
                self.after(0, lambda c=i: self.progress.configure(value=c))
                if protect_original_names:
                    self._update_counter_threadsafe(f"Renommage : {i}/{total} (protégés : {skipped_protected})")
                else:
                    self._update_counter_threadsafe(f"Renommage : {i}/{total}")

        self.after(0, lambda: self.progress.configure(value=total))
        if protect_original_names:
            self._update_counter_threadsafe(
                f"Renommage terminé ! {renamed_count} fichiers renommés, {skipped_protected} conservés (protection)."
            )
        else:
            self._update_counter_threadsafe(f"Renommage terminé ! {renamed_count} fichiers renommés.")
        self._refresh_table_threadsafe()
        self._finish_scan()

    # ── ARBRE & TRI ──────────────────────────────────────────────────

    def _build_tree_values(self, row: dict[str, Any], album_virtual: bool = False) -> tuple[Any, ...]:
        """Construit une ligne Treeview alignée strictement avec l'ordre des colonnes."""
        filepath = str(row.get("filepath") or "").strip()
        directory_name = ""
        if filepath:
            try:
                directory_name = Path(filepath).parent.name or ""
            except Exception:
                directory_name = ""

        if album_virtual:
            values_by_col: dict[str, Any] = {
                "copied": "",
                "directory_name": row.get("directory_name") or directory_name,
                "collection_name": row.get("collection_name") or "",
                "filename": row.get("filename") or "",
                "date_taken": row.get("date_taken") or "Inconnue",
                "gps_coords": "",
                "country": "",
                "region": "",
                "city": "",
                "filesize_str": format_size(int(row.get("filesize") or 0)),
                "camera": "",
            }
        else:
            lat_raw = row.get("latitude")
            lon_raw = row.get("longitude")
            try:
                lat = float(lat_raw) if lat_raw is not None and str(lat_raw).strip() != "" else None
                lon = float(lon_raw) if lon_raw is not None and str(lon_raw).strip() != "" else None
            except Exception:
                lat = None
                lon = None
            gps_str = f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else ""
            values_by_col = {
                "copied": "Oui" if int(row.get("is_copied") or 0) == 1 else "",
                "directory_name": directory_name,
                "collection_name": row.get("collection_name") or "",
                "filename": row.get("filename") or "",
                "date_taken": row.get("date_taken") or "Inconnue",
                "gps_coords": gps_str,
                "country": row.get("country") or "",
                "region": row.get("region") or "",
                "city": row.get("city") or "",
                "filesize_str": format_size(int(row.get("filesize") or 0)),
                "camera": row.get("camera_model") or "",
            }

        cols = list(self.tree["columns"])
        values = tuple(values_by_col.get(col, "") for col in cols)
        if bool(self.config_data.get("debug_validate_tree_columns", False)):
            self._debug_validate_tree_values(values, cols, row, album_virtual=album_virtual)
        return values

    def _debug_validate_tree_values(
        self,
        values: tuple[Any, ...],
        columns: list[str],
        row: dict[str, Any],
        album_virtual: bool,
    ) -> None:
        """Validation debug: détecte les décalages de colonnes dans le Treeview."""
        if len(values) != len(columns):
            logger.warning(
                "[tree-debug] Mismatch taille valeurs/colonnes: values=%d columns=%d photo_id=%s album_virtual=%s",
                len(values),
                len(columns),
                row.get("id"),
                album_virtual,
            )
            return

        col_to_idx = {name: idx for idx, name in enumerate(columns)}

        # Contrôles ciblés sur les colonnes les plus sensibles aux inversions.
        if "gps_coords" in col_to_idx and "filesize_str" in col_to_idx:
            gps_val = str(values[col_to_idx["gps_coords"]] or "")
            size_val = str(values[col_to_idx["filesize_str"]] or "")
            if gps_val and any(unit in gps_val for unit in (" B", "KB", "MB", "GB")):
                logger.warning("[tree-debug] gps_coords ressemble a une taille (photo_id=%s): %s", row.get("id"), gps_val)
            if size_val and "," in size_val:
                logger.warning("[tree-debug] filesize_str ressemble a des coordonnees (photo_id=%s): %s", row.get("id"), size_val)

        for col_name in ("country", "region", "city"):
            if col_name not in col_to_idx:
                continue
            val = str(values[col_to_idx[col_name]] or "").strip()
            if val and len(val) > 80:
                logger.warning("[tree-debug] valeur anormalement longue pour %s (photo_id=%s)", col_name, row.get("id"))

    def _load_data_to_treeview(self) -> None:
        self._cancel_pending_startup_load()
        if not self.db and not self._is_album_scope_active():
            self._set_country_quick_filters([])
            return
        
        self.tree.delete(*self.tree.get_children())
        self._album_virtual_rows = {}

        is_virtual_album = self._is_virtual_album_scope_active()
        is_faces_scope = str(self.var_viewer_scope.get()) == "faces"
        
        if is_virtual_album or (is_faces_scope and getattr(self, "_active_face_person_id", None)):
            if is_virtual_album:
                records = self._build_virtual_album_rows()
            else:
                records = self.db.get_photos_by_person(self._active_face_person_id)
            available_countries: set[str] = set()
            search_term = self.var_search.get().strip().lower() if hasattr(self, 'var_search') else ""
            norm_search = _remove_accents(search_term)

            targeted_field: str | None = None
            targeted_value = ""
            filter_match = re.match(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$", search_term)
            if filter_match:
                raw_key = str(filter_match.group(1) or "")
                raw_value = str(filter_match.group(2) or "")
                key = _remove_accents(raw_key.strip())
                value = raw_value.strip()
                key_map = {
                    "pays": "country",
                    "country": "country",
                    "countries": "country",
                    "region": "region",
                    "regions": "region",
                    "ville": "city",
                    "city": "city",
                    "villes": "city",
                    "cities": "city",
                    "dossier": "directory_name",
                    "dossiers": "directory_name",
                    "repertoire": "directory_name",
                    "repertoires": "directory_name",
                    "folder": "directory_name",
                    "folders": "directory_name",
                }
                mapped = key_map.get(key)
                if mapped and value:
                    targeted_field = mapped
                    targeted_value = _remove_accents(value)

            map_points_all: list[tuple[int, float, float]] = []

            self._is_searching = True
            try:
                for row in records:
                    country = str(row.get("country") or "").strip()
                    if country:
                        available_countries.add(country)

                    coll_name = row.get("collection_name") or ""
                    filename = row.get("filename") or ""
                    filepath_raw = str(row.get("filepath") or "")
                    directory_name = str(row.get("directory_name") or "")
                    p_abs = self._abs_path(filepath_raw)
                    parent_name = p_abs.parent.name if p_abs else ""
                    parent_path = str(p_abs.parent) if p_abs else ""
                    searchable = _remove_accents((
                        str(coll_name)
                        + " "
                        + str(filename)
                        + " "
                        + directory_name
                        + " "
                        + parent_name
                        + " "
                        + parent_path
                        + " "
                        + str(row.get("country") or "")
                        + " "
                        + str(row.get("region") or "")
                        + " "
                        + str(row.get("city") or "")
                        + " "
                        + str(row.get("camera_model") or "")
                        + " "
                        + str(row.get("date_taken") or "")
                    ).lower())
                    if search_term:
                        if targeted_field is not None:
                            if targeted_field == "directory_name":
                                raw_dir = str(row.get("directory_name") or "").strip()
                                if not raw_dir and filepath_raw:
                                    try:
                                        raw_dir = Path(filepath_raw).parent.name or ""
                                    except Exception:
                                        raw_dir = ""
                                field_value = _remove_accents(raw_dir.lower())
                                if field_value != targeted_value:
                                    continue
                            else:
                                field_value = _remove_accents(str(row.get(targeted_field) or "").lower())
                                if targeted_value not in field_value:
                                    continue
                        elif norm_search not in searchable:
                            continue

                    lat = row.get("latitude")
                    lon = row.get("longitude")
                    if lat is not None and lon is not None:
                        map_points_all.append((int(row["id"]), float(lat), float(lon)))
                    if self._map_filter_bbox and not self._is_within_map_filter(lat, lon):
                        continue
                    values = self._build_tree_values(row, album_virtual=False)
                    has_gps = lat is not None and lon is not None
                    self.tree.insert("", "end", iid=str(row["id"]), values=values, tags=("gps_link",) if has_gps else ())

                self._set_map_points(map_points_all)

                if not self._try_restore_last_view():
                    self._try_display_first()
            finally:
                self._is_searching = False
            self._refresh_contextual_counter(force=True)
            self._set_country_quick_filters(list(available_countries))
            return

        if self._is_album_scope_active():
            records = self._build_album_virtual_rows()
            available_countries: set[str] = set()
            search_term = self.var_search.get().strip().lower() if hasattr(self, 'var_search') else ""
            norm_search = _remove_accents(search_term)

            self._is_searching = True
            try:
                for row in records:
                    country = str(row.get("country") or "").strip()
                    if country:
                        available_countries.add(country)
                    coll_name = row["collection_name"] or ""
                    filename = row["filename"] or ""
                    if search_term:
                        searchable = _remove_accents((
                            str(coll_name)
                            + " "
                            + str(filename)
                            + " "
                            + str(row.get("date_taken") or "")
                            + " "
                            + str(self._abs_path(row.get("filepath")).parent if row.get("filepath") else "")
                        ).lower())
                        if norm_search not in searchable:
                            continue

                    self._album_virtual_rows[int(row["id"])] = row
                    values = self._build_tree_values(row, album_virtual=True)
                    self.tree.insert("", "end", iid=str(row["id"]), values=values)

                self._set_map_points([])

                if not self._try_restore_last_view():
                    self._try_display_first()
            finally:
                self._is_searching = False
            self._refresh_contextual_counter(force=True)
            self._set_country_quick_filters(list(available_countries))
            return
        
        db_col = self._get_treeview_db_sort_column()

        records = self.db.get_all_photos(order_by=db_col, asc=bool(self.__dict__.get("_sort_asc", True)))
        logger.info("[app] Chargement tableau : %d photos trouves en base.", len(records))
        duplicate_scope = self._is_duplicates_scope_active()
        faces_scope = self._is_faces_scope_active()
        if duplicate_scope and self._duplicate_prepare_in_progress and not self._duplicate_cache_valid:
            self._set_map_points([])
            return
        if duplicate_scope and not self._duplicate_cache_valid and not self._duplicate_prepare_in_progress:
            self._refresh_duplicate_cache()
        duplicate_ids = self._duplicate_photo_ids if duplicate_scope else set()
        face_ids = self.db.get_photo_ids_with_faces() if faces_scope else set()
        
        # Filtre de recherche global + mode ciblé par colonne (ex: pays=fr, region=faro, ville=lyon)
        search_term = self.var_search.get().strip().lower() if hasattr(self, 'var_search') else ""
        norm_search = _remove_accents(search_term)

        targeted_field: str | None = None
        targeted_value = ""
        filter_match = re.match(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$", search_term)
        if filter_match:
            raw_key = str(filter_match.group(1) or "")
            raw_value = str(filter_match.group(2) or "")
            key = _remove_accents(raw_key.strip())
            value = raw_value.strip()
            key_map = {
                "pays": "country",
                "country": "country",
                "countries": "country",
                "region": "region",
                "regions": "region",
                "ville": "city",
                "city": "city",
                "villes": "city",
                "cities": "city",
                "dossier": "directory_name",
                "dossiers": "directory_name",
                "repertoire": "directory_name",
                "repertoires": "directory_name",
                "folder": "directory_name",
                "folders": "directory_name",
            }
            mapped = key_map.get(key)
            if mapped and value:
                targeted_field = mapped
                targeted_value = _remove_accents(value)

        face_name_match_ids = self.db.get_photo_ids_for_person_name(norm_search) if (search_term and targeted_field is None) else set()
        map_points_all: list[tuple[int, float, float]] = []
        available_countries: set[str] = set()
        
        # Flag pour éviter le vol de focus pendant la mise à jour par recherche
        self._is_searching = True
        try:
            for row in records:
                photo_id = int(row.get("id") or 0)
                if duplicate_scope and photo_id not in duplicate_ids:
                    continue
                if faces_scope and photo_id not in face_ids:
                    continue
                country = str(row.get("country") or "").strip()
                if country:
                    available_countries.add(country)
                coll_name = row["collection_name"] or ""
                filename = row.get("filename") or ""
                filepath_raw = str(row.get("filepath") or "")
                directory_name = str(row.get("directory_name") or "")
                parent_name = Path(filepath_raw).parent.name if filepath_raw else ""
                parent_path = str(Path(filepath_raw).parent) if filepath_raw else ""
                searchable = _remove_accents((
                    str(coll_name)
                    + " "
                    + str(filename)
                    + " "
                    + directory_name
                    + " "
                    + parent_name
                    + " "
                    + parent_path
                    + " "
                    + str(row.get("country") or "")
                    + " "
                    + str(row.get("region") or "")
                    + " "
                    + str(row.get("city") or "")
                    + " "
                    + str(row.get("camera_model") or "")
                    + " "
                    + str(row.get("date_taken") or "")
                ).lower())
                if search_term:
                    if targeted_field is not None:
                        if targeted_field == "directory_name":
                            raw_dir = str(row.get("directory_name") or "").strip()
                            if not raw_dir and filepath_raw:
                                try:
                                    raw_dir = Path(filepath_raw).parent.name or ""
                                except Exception:
                                    raw_dir = ""
                            field_value = _remove_accents(raw_dir.lower())
                            if field_value != targeted_value:
                                continue
                        else:
                            field_value = _remove_accents(str(row.get(targeted_field) or "").lower())
                            if targeted_value not in field_value:
                                continue
                    elif (norm_search not in searchable) and (photo_id not in face_name_match_ids):
                        continue
                
                lat = row.get("latitude")
                lon = row.get("longitude")
                if lat is not None and lon is not None:
                    map_points_all.append((int(row["id"]), float(lat), float(lon)))
                if self._map_filter_bbox and not self._is_within_map_filter(lat, lon):
                    continue
                values = self._build_tree_values(row, album_virtual=False)
                has_gps = lat is not None and lon is not None
                self.tree.insert("", "end", iid=str(row["id"]), values=values, tags=("gps_link",) if has_gps else ())

            self._set_map_points(map_points_all)
                
            if not self._try_restore_last_view():
                self._try_display_first()
        finally:
            self._is_searching = False
        self._refresh_contextual_counter(force=True)
        self._set_country_quick_filters(list(available_countries))

    def _try_restore_last_view(self) -> bool:
        """Restaure la dernière photo affichée au démarrage si elle existe dans la vue courante."""
        pending = self._pending_restore_view_path.strip()
        if not pending:
            return False

        try:
            target_path = Path(pending)
            if not target_path.exists():
                self._pending_restore_view_path = ""
                return False

            matched_iid = None
            for iid in self.tree.get_children():
                try:
                    row = self._get_photo_by_id(int(iid))
                except Exception:
                    row = None
                if not row or not row.get("filepath"):
                    continue
                if self._abs_path(row["filepath"]) == target_path:
                    matched_iid = iid
                    break

            if not matched_iid:
                self._pending_restore_view_path = ""
                return False

            self.tree.selection_set(matched_iid)
            self.tree.focus(matched_iid)
            self.tree.see(matched_iid)
            self._pending_restore_view_path = ""
            return True
        except Exception:
            self._pending_restore_view_path = ""
            return False

    def _on_search_changed(self) -> None:
        """Appelé quand le texte de recherche change."""
        current = str(self.var_search.get() or "").strip() if hasattr(self, "var_search") else ""
        injected = str(getattr(self, "_scope_injected_search_filter", "") or "").strip()
        # Si l'utilisateur a modifié le texte injecté, il devient manuel.
        if bool(getattr(self, "_scope_injected_search_armed", False)) and current != injected:
            self._scope_injected_search_armed = False

        if hasattr(self, '_search_timer'):
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(1500, self._load_data_to_treeview)

    def _on_country_quick_filter_click(self, country: str) -> None:
        """Injecte un filtre ciblé Pays=... dans la barre de recherche."""
        country_clean = str(country or "").strip()
        if not country_clean:
            return
        self.var_search.set(f"Pays={country_clean}")

    def _set_country_quick_filters(self, countries: list[str]) -> None:
        """Affiche les pays triés dans un conteneur cliquable sous la zone recherche."""
        if not hasattr(self, "country_quick_text"):
            return

        text = self.country_quick_text
        try:
            text.configure(state="normal")
            text.delete("1.0", "end")
            for tag in text.tag_names():
                if tag.startswith("country_link_"):
                    text.tag_delete(tag)

            sorted_countries = sorted(
                {str(c or "").strip() for c in countries if str(c or "").strip()},
                key=lambda s: _remove_accents(s).lower(),
            )

            if not sorted_countries:
                text.insert("end", "Aucun pays")
                text.configure(height=3)
                text.configure(state="disabled")
                return

            # Répartit les pays sur exactement 3 lignes, en gardant l'ordre alphabétique.
            line_count = 3
            per_line = max(1, (len(sorted_countries) + line_count - 1) // line_count)
            lines = [
                sorted_countries[i:i + per_line]
                for i in range(0, len(sorted_countries), per_line)
            ]
            while len(lines) < line_count:
                lines.append([])

            tag_idx = 0
            for line_idx, countries_line in enumerate(lines[:line_count]):
                for item_idx, country in enumerate(countries_line):
                    tag = f"country_link_{tag_idx}"
                    tag_idx += 1
                    text.insert("end", country, (tag,))
                    if item_idx < len(countries_line) - 1:
                        text.insert("end", " ")
                    text.tag_config(tag, foreground="#1f6feb", underline=True)
                    text.tag_bind(tag, "<Button-1>", lambda _e, c=country: self._on_country_quick_filter_click(c))
                if line_idx < line_count - 1:
                    text.insert("end", "\n")

            text.configure(height=3)

            text.configure(state="disabled")
        except Exception:
            try:
                text.configure(state="disabled")
            except Exception:
                pass

    def _try_display_first(self) -> None:
        children = self.tree.get_children()
        if children:
            first_iid = children[0]
            self.tree.selection_set(first_iid)
            self.tree.focus(first_iid)
            self.tree.see(first_iid)

    def _sort_by(self, col: str) -> None:
        current_sort_col = self.__dict__.get("_sort_col", "date_taken")
        current_sort_asc = bool(self.__dict__.get("_sort_asc", True))
        if current_sort_col == col:
            self._sort_asc = not current_sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        
        for c in self.tree["columns"]:
            t = self.tree.heading(c)["text"]
            t = t.replace(" ▲", "").replace(" ▼", "")
            self.tree.heading(c, text=t)

        self.tree.heading(col, text=self.tree.heading(col)["text"] + (" ▲" if self._sort_asc else " ▼"))
        self._load_data_to_treeview()
        self._try_display_first()

    def _on_tree_select(self, event: tk.Event) -> None:
        """Affiche la photo lorsque la sélection du tableau change."""
        if getattr(self, "_editing_dialog_open", False):
            return
        if getattr(self, "_suspend_tree_select_once", False):
            self._suspend_tree_select_once = False
            return
        selection = self.tree.selection()
        if not selection:
            return
        if getattr(self, "_right_clicking", False):
            return

        row = self._get_selected_tree_row()
        if row:
            try:
                photo_id = int(row.get("id"))
            except Exception:
                photo_id = 0
            self._highlight_map_photo(photo_id if photo_id > 0 else None)
            # La sélection du tableau ne doit pas voler le focus clavier au Treeview.
            self._display_on_viewers(self._abs_path(row["filepath"]), grab_focus=False)
            self._refresh_contextual_counter(force=True)
            if photo_id > 0:
                self._sync_faces_tab_to_photo(photo_id)

    def _is_within_map_filter(self, lat: float | None, lon: float | None) -> bool:
        """Retourne True si la photo est dans la zone de filtre spatiale active."""
        if self._map_filter_bbox is None:
            return True
        if lat is None or lon is None:
            return False
        lon_min, lon_max, lat_min, lat_max = self._map_filter_bbox
        return lon_min <= float(lon) <= lon_max and lat_min <= float(lat) <= lat_max

    def _on_map_canvas_configure(self, _event: tk.Event) -> None:
        self._render_map()

    def _on_map_mousewheel(self, event: tk.Event) -> str:
        """Zoom carte centré sur le pointeur (molette)."""
        w = max(1.0, float(self.map_canvas.winfo_width()))
        h = max(1.0, float(self.map_canvas.winfo_height()))
        x = max(0.0, min(w, float(getattr(event, "x", 0.0))))
        y = max(0.0, min(h, float(getattr(event, "y", 0.0))))

        def _safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except Exception:
                return default

        delta = _safe_int(getattr(event, "delta", 0) or 0, 0)
        num = _safe_int(getattr(event, "num", 0) or 0, 0)
        if num == 4 or delta > 0:
            zoom_factor = 0.82
        elif num == 5 or delta < 0:
            zoom_factor = 1.22
        else:
            return "break"

        cursor_lon, cursor_lat = self._map_xy_to_lonlat(x, y, w, h)
        current_lon_span = max(1e-6, self._map_view_lon_max - self._map_view_lon_min)
        current_lat_span = max(1e-6, self._map_view_lat_max - self._map_view_lat_min)

        new_lon_span = current_lon_span * zoom_factor
        new_lat_span = current_lat_span * zoom_factor

        # Bornes de zoom
        new_lon_span = max(self._map_min_lon_span, min(360.0, new_lon_span))
        new_lat_span = max(self._map_min_lat_span, min(180.0, new_lat_span))

        fx = x / w
        fy = y / h

        new_lon_min = cursor_lon - (fx * new_lon_span)
        new_lon_max = new_lon_min + new_lon_span
        new_lat_max = cursor_lat + (fy * new_lat_span)
        new_lat_min = new_lat_max - new_lat_span

        # Clamp sur le monde [-180,180] x [-90,90]
        if new_lon_min < -180.0:
            shift = -180.0 - new_lon_min
            new_lon_min += shift
            new_lon_max += shift
        if new_lon_max > 180.0:
            shift = new_lon_max - 180.0
            new_lon_min -= shift
            new_lon_max -= shift

        if new_lat_min < -90.0:
            shift = -90.0 - new_lat_min
            new_lat_min += shift
            new_lat_max += shift
        if new_lat_max > 90.0:
            shift = new_lat_max - 90.0
            new_lat_min -= shift
            new_lat_max -= shift

        self._map_view_lon_min = max(-180.0, new_lon_min)
        self._map_view_lon_max = min(180.0, new_lon_max)
        self._map_view_lat_min = max(-90.0, new_lat_min)
        self._map_view_lat_max = min(90.0, new_lat_max)

        self._render_map()
        return "break"

    def _on_map_mouse_down(self, event: tk.Event) -> None:
        self._map_drag_start_xy = (float(event.x), float(event.y))
        if self._map_drag_rect_id:
            self.map_canvas.delete(self._map_drag_rect_id)
            self._map_drag_rect_id = None

    def _on_map_click_stop_slideshow(self, _event: tk.Event | None = None) -> None:
        """Un clic sur la carte stoppe immédiatement tout diaporama actif."""
        try:
            if hasattr(self, "viewer_photo") and self.viewer_photo._slideshow_active:
                self.viewer_photo._stop_slideshow()
            if hasattr(self, "viewer_grid") and self.viewer_grid._slideshow_active:
                self.viewer_grid._stop_slideshow()
        except Exception:
            pass

    def _on_map_mouse_drag(self, event: tk.Event) -> None:
        if not self._map_drag_start_xy:
            return
        sx, sy = self._map_drag_start_xy
        ex, ey = float(event.x), float(event.y)
        if self._map_drag_rect_id:
            self.map_canvas.coords(self._map_drag_rect_id, sx, sy, ex, ey)
        else:
            self._map_drag_rect_id = self.map_canvas.create_rectangle(
                sx,
                sy,
                ex,
                ey,
                outline="#ef4444",
                width=2,
                dash=(4, 2),
                fill="#ef4444",
                stipple="gray25",
                tags=("map_selection",),
            )

    def _on_map_mouse_up(self, event: tk.Event) -> None:
        if not self._map_drag_start_xy:
            return
        sx, sy = self._map_drag_start_xy
        ex, ey = float(event.x), float(event.y)
        self._map_drag_start_xy = None

        if self._map_drag_rect_id:
            self.map_canvas.delete(self._map_drag_rect_id)
            self._map_drag_rect_id = None

        if abs(ex - sx) < 8 or abs(ey - sy) < 8:
            return

        w = max(1.0, float(self.map_canvas.winfo_width()))
        h = max(1.0, float(self.map_canvas.winfo_height()))
        x1, x2 = sorted((max(0.0, min(w, sx)), max(0.0, min(w, ex))))
        y1, y2 = sorted((max(0.0, min(h, sy)), max(0.0, min(h, ey))))

        lon1, lat1 = self._map_xy_to_lonlat(x1, y1, w, h)
        lon2, lat2 = self._map_xy_to_lonlat(x2, y2, w, h)

        self._map_filter_bbox = (
            min(lon1, lon2),
            max(lon1, lon2),
            min(lat1, lat2),
            max(lat1, lat2),
        )
        self._fit_map_view_to_bbox(*self._map_filter_bbox, w, h)
        self._load_data_to_treeview()

    def _clear_map_filter(self) -> None:
        self._map_filter_bbox = None
        self._load_data_to_treeview()

    def _fit_map_view_to_bbox(
        self,
        lon_min: float,
        lon_max: float,
        lat_min: float,
        lat_max: float,
        canvas_w: float,
        canvas_h: float,
    ) -> None:
        """Ajuste la vue carte pour centrer et encadrer une bbox sélectionnée."""
        width = max(1.0, canvas_w)
        height = max(1.0, canvas_h)

        # Petite marge autour de la zone pour garder du contexte visuel.
        pad = 1.12
        sel_lon_span = max(1e-6, (lon_max - lon_min) * pad)
        sel_lat_span = max(1e-6, (lat_max - lat_min) * pad)

        # Respecte le ratio du canvas pour un cadrage naturel.
        view_aspect = width / height
        current_aspect = sel_lon_span / sel_lat_span
        if current_aspect < view_aspect:
            sel_lon_span = sel_lat_span * view_aspect
        else:
            sel_lat_span = sel_lon_span / view_aspect

        sel_lon_span = max(self._map_min_lon_span, min(360.0, sel_lon_span))
        sel_lat_span = max(self._map_min_lat_span, min(180.0, sel_lat_span))

        center_lon = (lon_min + lon_max) / 2.0
        center_lat = (lat_min + lat_max) / 2.0

        new_lon_min = center_lon - (sel_lon_span / 2.0)
        new_lon_max = center_lon + (sel_lon_span / 2.0)
        new_lat_min = center_lat - (sel_lat_span / 2.0)
        new_lat_max = center_lat + (sel_lat_span / 2.0)

        # Clamp monde tout en conservant la fenêtre demandée.
        if new_lon_min < -180.0:
            shift = -180.0 - new_lon_min
            new_lon_min += shift
            new_lon_max += shift
        if new_lon_max > 180.0:
            shift = new_lon_max - 180.0
            new_lon_min -= shift
            new_lon_max -= shift

        if new_lat_min < -90.0:
            shift = -90.0 - new_lat_min
            new_lat_min += shift
            new_lat_max += shift
        if new_lat_max > 90.0:
            shift = new_lat_max - 90.0
            new_lat_min -= shift
            new_lat_max -= shift

        self._map_view_lon_min = max(-180.0, new_lon_min)
        self._map_view_lon_max = min(180.0, new_lon_max)
        self._map_view_lat_min = max(-90.0, new_lat_min)
        self._map_view_lat_max = min(90.0, new_lat_max)

    def _on_map_reset_request(self, _event: tk.Event) -> str:
        """Réinitialise la sélection de zone carte (équivalent ancien bouton)."""
        self._clear_map_filter()
        return "break"

    def _set_map_points(self, points: list[tuple[int, float, float]]) -> None:
        self._map_points = points
        if self._map_selected_photo_id is not None and all(pid != self._map_selected_photo_id for pid, _la, _lo in points):
            self._map_selected_photo_id = None

        if self._map_filter_bbox is None:
            status = f"Points carte : {len(points)}"
        else:
            status = f"Zone active - points carte : {len(points)}"

        if hasattr(self, "var_map_status"):
            self.var_map_status.set(status)

        if self._startup_tree_loading:
            return

        self._render_map()

    def _highlight_map_photo(self, photo_id: int | None) -> None:
        self._map_selected_photo_id = photo_id
        self._render_map()

    def _map_lonlat_to_xy(self, lon: float, lat: float, width: float, height: float) -> tuple[float, float]:
        lon_span = max(1e-9, self._map_view_lon_max - self._map_view_lon_min)
        lat_span = max(1e-9, self._map_view_lat_max - self._map_view_lat_min)
        x = ((lon - self._map_view_lon_min) / lon_span) * width
        y = ((self._map_view_lat_max - lat) / lat_span) * height
        return x, y

    def _map_xy_to_lonlat(self, x: float, y: float, width: float, height: float) -> tuple[float, float]:
        lon_span = max(1e-9, self._map_view_lon_max - self._map_view_lon_min)
        lat_span = max(1e-9, self._map_view_lat_max - self._map_view_lat_min)
        lon = self._map_view_lon_min + (x / max(1.0, width)) * lon_span
        lat = self._map_view_lat_max - (y / max(1.0, height)) * lat_span
        return lon, lat

    def _load_map_shapes(self) -> None:
        """Déclenche le chargement asynchorne des polygones monde depuis un GeoJSON local."""
        if self._map_shapes_loaded or self._map_shapes_loading:
            return

        self._map_shapes_loading = True

        def _worker() -> None:
            polygons: list[tuple[tuple[float, float, float, float], list[tuple[float, float]]]] = []
            loaded = False
            geojson_path: Path | None = None
            candidates = [
                # 1. Utilisation de Path(__file__) résolu (le plus robuste en dev)
                Path(__file__).resolve().parent / "assets" / "world_countries.geojson",
                # 2. get_resource_path (pour l'exécutable)
                get_resource_path("src/photoselect/assets/world_countries.geojson"),
                # 3. Chemin relatif direct (si lancé depuis le root)
                Path("src/photoselect/assets/world_countries.geojson"),
            ]
            for p in candidates:
                if p and p.exists():
                    geojson_path = p
                    break

            if geojson_path is None:
                logger.warning("GeoJSON carte introuvable: rendu simplifie fallback")
                self.after(0, lambda: self._on_map_shapes_loaded(False, []))
                return

            try:
                with open(geojson_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                def _append_ring(ring: list[list[float]]) -> None:
                    if len(ring) < 3:
                        return
                    step = 1
                    if len(ring) > 2500:
                        step = 8
                    elif len(ring) > 1200:
                        step = 5
                    elif len(ring) > 500:
                        step = 3

                    points: list[tuple[float, float]] = []
                    for i, coord in enumerate(ring):
                        if i % step != 0 and i != len(ring) - 1:
                            continue
                        if len(coord) < 2:
                            continue
                        lon = float(coord[0])
                        lat = float(coord[1])
                        if -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0:
                            points.append((lon, lat))

                    if len(points) < 3:
                        return

                    lons = [p[0] for p in points]
                    lats = [p[1] for p in points]
                    bbox = (min(lons), max(lons), min(lats), max(lats))
                    polygons.append((bbox, points))

                for feature in data.get("features", []):
                    geom = feature.get("geometry") or {}
                    gtype = geom.get("type")
                    coords = geom.get("coordinates")
                    if not coords:
                        continue

                    if gtype == "Polygon":
                        _append_ring(coords[0])
                    elif gtype == "MultiPolygon":
                        for poly in coords:
                            if poly:
                                _append_ring(poly[0])

                loaded = True
            except Exception as e:
                logger.warning("Chargement GeoJSON carte impossible: %s", e)

            self.after(0, lambda ok=loaded, polys=polygons: self._on_map_shapes_loaded(ok, polys))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_map_shapes_loaded(
        self,
        loaded: bool,
        polygons: list[tuple[tuple[float, float, float, float], list[tuple[float, float]]]],
    ) -> None:
        self._map_shapes_loading = False
        self._map_shapes_loaded = True
        self._map_shape_polygons = polygons if loaded else []
        if loaded:
            logger.info("Carte GeoJSON chargee: %d polygones", len(self._map_shape_polygons))
        if not self._startup_tree_loading:
            self._render_map()

    def _render_map(self) -> None:
        if not hasattr(self, "map_canvas"):
            return

        canvas = self.map_canvas
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())

        canvas.delete("all")

        canvas.create_rectangle(0, 0, w, h, fill="#d9efff", outline="")
        band_y = int(h * 0.52)
        canvas.create_rectangle(0, band_y, w, h, fill="#c2e3ff", outline="")

        self._load_map_shapes()
        view_lon_min = self._map_view_lon_min
        view_lon_max = self._map_view_lon_max
        view_lat_min = self._map_view_lat_min
        view_lat_max = self._map_view_lat_max

        if self._map_shape_polygons:
            for (bbox_lon_min, bbox_lon_max, bbox_lat_min, bbox_lat_max), polygon in self._map_shape_polygons:
                if bbox_lon_max < view_lon_min or bbox_lon_min > view_lon_max:
                    continue
                if bbox_lat_max < view_lat_min or bbox_lat_min > view_lat_max:
                    continue

                xy: list[float] = []
                for lon, lat in polygon:
                    x, y = self._map_lonlat_to_xy(lon, lat, float(w), float(h))
                    xy.extend((x, y))
                if len(xy) >= 6:
                    canvas.create_polygon(*xy, fill="#8fbf7b", outline="#5f8f58", width=1)
        else:
            fallback = [
                [(-170, 70), (-120, 75), (-60, 50), (-90, 15), (-130, 30)],
                [(-80, 12), (-45, -12), (-70, -55)],
                [(-15, 35), (40, 40), (35, -35), (-10, -30)],
                [(0, 70), (140, 55), (170, 35), (100, 5), (40, 25)],
                [(110, -12), (155, -25), (135, -43), (113, -30)],
            ]
            for polygon in fallback:
                xy: list[float] = []
                for lon, lat in polygon:
                    x, y = self._map_lonlat_to_xy(float(lon), float(lat), float(w), float(h))
                    xy.extend((x, y))
                if len(xy) >= 6:
                    canvas.create_polygon(*xy, fill="#8fbf7b", outline="#5f8f58", width=1)

            if self._map_shapes_loading:
                canvas.create_text(
                    w - 10,
                    12,
                    text="Carte en preparation...",
                    fill="#334155",
                    font=("Segoe UI", 8, "italic"),
                    anchor="ne",
                )

        continent_labels: list[tuple[str, float, float]] = [
            ("Amerique du Nord", -108, 48),
            ("Amerique du Sud", -60, -18),
            ("Europe", 15, 53),
            ("Afrique", 18, 4),
            ("Asie", 92, 42),
            ("Oceanie", 136, -24),
        ]
        for label, lon, lat in continent_labels:
            x, y = self._map_lonlat_to_xy(lon, lat, float(w), float(h))
            canvas.create_text(
                x,
                y,
                text=label,
                fill="#1f2937",
                font=("Segoe UI", 8, "bold"),
            )

        for lon in range(-120, 181, 60):
            x, _ = self._map_lonlat_to_xy(float(lon), 0.0, float(w), float(h))
            canvas.create_line(x, 0, x, h, fill="#9db4c7", width=1)
        for lat in range(-60, 61, 30):
            _x, y = self._map_lonlat_to_xy(0.0, float(lat), float(w), float(h))
            canvas.create_line(0, y, w, y, fill="#9db4c7", width=1)

        canvas.create_rectangle(1, 1, w - 1, h - 1, outline="#5d7183", width=1)

        self._map_point_coords = {}
        for photo_id, lat, lon in self._map_points:
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                continue
            x, y = self._map_lonlat_to_xy(lon, lat, float(w), float(h))
            radius = 3
            color = "#ef4444"
            if self._map_selected_photo_id == photo_id:
                radius = 5
                color = "#f59e0b"
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="#111827", width=1)
            self._map_point_coords[photo_id] = (x, y)

        if self._map_filter_bbox is not None:
            lon_min, lon_max, lat_min, lat_max = self._map_filter_bbox
            x1, y1 = self._map_lonlat_to_xy(lon_min, lat_max, float(w), float(h))
            x2, y2 = self._map_lonlat_to_xy(lon_max, lat_min, float(w), float(h))
            canvas.create_rectangle(
                min(x1, x2),
                min(y1, y2),
                max(x1, x2),
                max(y1, y2),
                outline="#dc2626",
                width=2,
                dash=(5, 2),
                fill="#ef4444",
                stipple="gray25",
            )

    def _on_tree_double_click(self, event: tk.Event) -> str:
        """Ouvre le dialogue d'édition sans laisser le double-clic relancer la visionneuse."""
        item = self.tree.identify_row(event.y)
        if item:
            current = self.tree.selection()
            if not current or current[0] != item:
                self.tree.selection_set(item)
            self.tree.focus(item)
        self.after_idle(self._rename_single)
        return "break"

    def _on_tree_motion(self, event: tk.Event) -> None:
        """Change le curseur en main quand la souris survole la colonne GPS."""
        col_ref = self.tree.identify_column(event.x)
        try:
            col_id = self.tree["columns"][int(col_ref[1:]) - 1] if col_ref and col_ref != "#0" else ""
        except Exception:
            col_id = ""
        cursor = "hand2" if col_id == "gps_coords" else ""
        self.tree.configure(cursor=cursor)

    def _on_tree_geo_click(self, event: tk.Event) -> str | None:
        """Ouvre Maps uniquement au clic sur la colonne des coordonnées GPS."""
        if getattr(self, "_editing_dialog_open", False):
            return None

        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return None

        item = self.tree.identify_row(event.y)
        col_ref = self.tree.identify_column(event.x)
        if not item or not col_ref or col_ref == "#0":
            return None

        try:
            col_idx = int(col_ref[1:]) - 1
            col_id = self.tree["columns"][col_idx]
        except Exception:
            return None

        if col_id != "gps_coords":
            return None

        current = self.tree.selection()
        if not current or current[0] != item:
            self.tree.selection_set(item)
        self.tree.focus(item)

        row = None
        try:
            row = self._get_photo_by_id(int(item))
        except Exception:
            row = None
        if not row:
            return "break"

        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is not None and lon is not None:
            _open_in_maps(lat, lon)
            return "break"

        return "break"

    def _on_tree_click_stop_slideshow(self, _event: tk.Event | None = None) -> None:
        """Un clic gauche sur le tableau stoppe immédiatement tout diaporama actif."""
        try:
            if hasattr(self, "viewer_photo") and self.viewer_photo._slideshow_active:
                self.viewer_photo._stop_slideshow()
            if hasattr(self, "viewer_grid") and self.viewer_grid._slideshow_active:
                self.viewer_grid._stop_slideshow()
        except Exception:
            pass

    def _show_tree_ctx_menu(self, event: tk.Event) -> str | None:
        """Affiche le menu contextuel du tableau au clic droit."""
        item = self.tree.identify_row(event.y)
        if item:
            self._right_clicking = True
            # Préserve la multi-sélection si la ligne cliquée en fait déjà partie.
            current = self.tree.selection()
            if not current or item not in current:
                self.tree.selection_set(item)
            self.tree.focus(item)
            # Un clic droit sur le tableau stoppe immédiatement un diaporama en cours.
            try:
                if hasattr(self, "viewer_photo") and self.viewer_photo._slideshow_active:
                    self.viewer_photo._stop_slideshow()
                if hasattr(self, "viewer_grid") and self.viewer_grid._slideshow_active:
                    self.viewer_grid._stop_slideshow()
            except Exception:
                pass
            try:
                delete_label = "Retirer de l'album" if self._is_virtual_album_scope_active() else "Supprimer (Corbeille)"
                try:
                    self._tree_ctx_menu.entryconfigure(self._tree_ctx_delete_index, label=delete_label)
                except Exception:
                    pass
                self._tree_ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._tree_ctx_menu.grab_release()
                # Libère le garde-fou après la fermeture du menu.
                self.after(50, lambda: setattr(self, "_right_clicking", False))
            return "break"
        return None

    def _bind_stats_context_menu_recursive(self, widget: tk.Misc) -> None:
        try:
            widget.bind("<Button-3>", self._show_stats_ctx_menu, add="+")
        except Exception:
            return
        for child in widget.winfo_children() if hasattr(widget, "winfo_children") else []:
            self._bind_stats_context_menu_recursive(child)

    def _show_stats_ctx_menu(self, event: tk.Event) -> str:
        try:
            self._stats_ctx_menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            self._stats_ctx_menu.grab_release()
        return "break"

    def _copy_statistics_page_to_clipboard(self) -> None:
        if not hasattr(self, "_stats_vars"):
            return
        self._update_statistics()

        lines: list[str] = []
        lines.append("Statistiques PhotoSelect")
        lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        source = str(self.var_in.get() or "").strip() if hasattr(self, "var_in") else ""
        if source:
            lines.append(f"Source: {source}")
        scope = str(self.var_viewer_scope.get() or "") if hasattr(self, "var_viewer_scope") else ""
        if scope:
            lines.append(f"Portée affichage: {self._get_scope_label_for_status(scope)}")
        lines.append("")

        for key, label in getattr(self, "_stats_fields", []):
            var = self._stats_vars.get(str(key)) if hasattr(self, "_stats_vars") else None
            value = str(var.get() if var is not None else "-")
            lines.append(f"{label}: {value}")

        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_selected_photo(self) -> None:
        """Copie l'image de la ligne sélectionnée dans le presse-papier."""
        selection = self.tree.selection()
        if not selection: return
        row = self._get_selected_tree_row()
        if row:
            filepath = self._abs_path(row.get("filepath"))
            if filepath.exists():
                if _copy_image_to_clipboard(filepath):
                    logger.info("Image copiée dans le presse-papier : %s", filepath.name)

    def _start_slideshow_from_table_selection(self) -> None:
        """Démarre le diaporama à partir de la ligne sélectionnée, sur le tableau courant (filtré ou non)."""
        if not hasattr(self, "viewer_photo"):
            return
        selection = self.tree.selection() if hasattr(self, "tree") else ()
        if not selection:
            logger.debug("[slideshow] Aucune sélection dans le tableau.")
            return

        photo_id = int(selection[0])
        row = self._get_photo_by_id(photo_id)
        if not row:
            logger.debug("[slideshow] Impossible de trouver la photo ID=%s en base.", photo_id)
            return

        path_raw = str(row.get("filepath") or "").strip()
        p = self._abs_path(path_raw)
        logger.debug("[slideshow] Lancement: id=%s path_rel=%s path_abs=%s", photo_id, path_raw, p)

        if not p or not p.exists():
            logger.warning("[slideshow] Fichier absent: %s", p)
            return

        # Forcer le mode photo pour éviter le basculement mosaïque au démarrage.
        try:
            if hasattr(self, "var_viewer_mode"):
                self.var_viewer_mode.set("photo")
            self.config_data["viewer_mode"] = "photo"
            self._save_config()
        except Exception:
            pass

        # Positionne d'abord les visionneuses sur la ligne demandée, puis lance le diaporama.
        logger.debug("[slideshow] Affichage photo...")
        self._display_on_viewers(p, grab_focus=True)
        logger.info("[slideshow] Démarrage du mode défilement.")
        self.viewer_photo._start_slideshow_from_current_view()

    def _on_tree_start_slideshow_shortcut(self, _event: tk.Event | None = None) -> str:
        """Point d'entrée pour le raccourci D/d (Tableau)."""
        return self._trigger_slideshow_standard_logic()

    def _on_global_start_slideshow_shortcut(self, event: tk.Event | None = None) -> str:
        """Déclenche D/d même si le focus n'est plus exactement sur le tableau."""
        focus_widget = event.widget if event is not None else self.focus_get()
        editable_widgets = (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Listbox)
        if focus_widget is not None and isinstance(focus_widget, editable_widgets):
            return "break"
        return self._trigger_slideshow_standard_logic()

    def _trigger_slideshow_standard_logic(self) -> str:
        """Logique unifiée pour démarrer ou arrêter le diaporama."""
        photo_active = getattr(self.viewer_photo, "_slideshow_active", False)
        grid_active = getattr(self.viewer_grid, "_slideshow_active", False)
        if photo_active or grid_active:
            try:
                if photo_active:
                    self.viewer_photo._stop_slideshow()
                if grid_active:
                    self.viewer_grid._stop_slideshow()
            except Exception:
                pass
            return "break"

        start_path: Path | None = None
        try:
            mode = str(self.var_viewer_mode.get() if hasattr(self, "var_viewer_mode") else self.config_data.get("viewer_mode", "photo"))
            if mode == "multiple":
                grid_path = getattr(self.viewer_grid, "_current_path", None)
                if grid_path and grid_path.exists():
                    start_path = grid_path
                if hasattr(self, "var_viewer_mode"):
                    self.var_viewer_mode.set("photo")
                self.config_data["viewer_mode"] = "photo"
                self._save_config()
        except Exception:
            pass

        if start_path is not None:
            logger.info("[slideshow] Démarrage depuis la photo courante de la mosaïque.")
            self._display_on_viewers(start_path, grab_focus=True)
            if getattr(self.viewer_photo, "_current_path", None):
                self.viewer_photo._start_slideshow_from_current_view()
                return "break"

        # Si la visionneuse a déjà une photo, on démarre directement depuis celle-ci
        if hasattr(self, "viewer_photo") and getattr(self.viewer_photo, "_current_path", None):
            logger.info("[slideshow] Reprise depuis la photo courante.")
            self.viewer_photo._start_slideshow_from_current_view()
        else:
            # Sinon on part de la sélection du tableau
            self._start_slideshow_from_table_selection()
            
        return "break"

    def _copy_selected_row(self) -> None:
        """Copie les données de la ligne sélectionnée dans le presse-papier (texte tabulé)."""
        selection = self.tree.selection()
        if not selection: return
        values = self.tree.item(selection[0], "values")
        if values:
            headers = [self.tree.heading(c)["text"].replace(" ▲", "").replace(" ▼", "") for c in self.tree["columns"]]
            line = "\t".join(str(v) for v in values)
            header_line = "\t".join(headers)
            text = f"{header_line}\n{line}"
            self.clipboard_clear()
            self.clipboard_append(text)

    def _locate_selected_on_disk(self) -> None:
        """Localise la photo sélectionnée dans Windows Explorer."""
        selection = self.tree.selection()
        if not selection: return
        row = self._get_selected_tree_row()
        if row:
            _locate_on_disk(self._abs_path(row["filepath"]))

    def _open_selected_directory(self) -> None:
        """Ouvre le répertoire de la photo sélectionnée."""
        selection = self.tree.selection()
        if not selection: return
        row = self._get_selected_tree_row()
        if row:
            _open_directory(self._abs_path(row["filepath"]))

    def _open_selected_in_maps(self) -> None:
        """Ouvre la position GPS de la photo sélectionnée dans Google Maps."""
        selection = self.tree.selection()
        if not selection: return
        row = self._get_selected_tree_row()
        if row:
            _open_in_maps(row.get("latitude"), row.get("longitude"))

    def _verify_selected_gps_geo(self) -> None:
        """Vérifie les coordonnées GPS et la cohérence avec les champs Pays/Région/Ville."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Vérification GPS / Géographique", "Sélectionnez d'abord une photo.")
            return

        row = self._get_selected_tree_row()
        if not row:
            try:
                row = self._get_photo_by_id(int(selection[0]))
            except Exception:
                row = None
        if not row:
            messagebox.showwarning("Vérification GPS / Géographique", "Impossible de lire la ligne sélectionnée.")
            return

        def _to_float(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                txt = str(value).strip().replace(",", ".")
                if txt == "":
                    return None
                return float(txt)
            except Exception:
                return None

        def _norm(value: Any) -> str:
            txt = str(value or "").strip().lower()
            txt = unicodedata.normalize("NFD", txt)
            txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
            return txt

        filepath = str(row.get("filepath") or "")
        try:
            photo_id = int(row.get("id") or selection[0])
        except Exception:
            photo_id = 0
        lat = _to_float(row.get("latitude"))
        lon = _to_float(row.get("longitude"))

        country = str(row.get("country") or "").strip()
        region = str(row.get("region") or "").strip()
        city = str(row.get("city") or "").strip()

        has_geo_text = bool(country or region or city)
        gps_valid = lat is not None and lon is not None and (-90.0 <= lat <= 90.0) and (-180.0 <= lon <= 180.0)

        lines: list[str] = []
        lines.append("Vérification GPS / Géographique")
        lines.append("")
        if filepath:
            lines.append(f"Fichier : {self._abs_path(filepath).name}")

        if lat is None or lon is None:
            lines.append("GPS : manquant")
        elif gps_valid:
            lines.append(f"GPS : OK ({lat:.6f}, {lon:.6f})")
        else:
            lines.append(f"GPS : invalide ({lat}, {lon})")

        lines.append(
            "Géographique : "
            + (f"Pays={country or '-'} | Région={region or '-'} | Ville={city or '-'}" if has_geo_text else "manquant")
        )

        if gps_valid:
            geo_mode = str(
                self.var_geo_mode.get()
                if hasattr(self, "var_geo_mode")
                else self.config_data.get("geo_mode", "local")
            ).strip().lower()
            if geo_mode not in ("local", "online"):
                geo_mode = "local"

            reference_label = "Référence locale (depuis GPS)"
            reference_loc: dict[str, str]
            if geo_mode == "online":
                reference_label = "Référence en ligne (depuis GPS)"
                reference_loc = get_location_details_online(lat, lon)
                if not any(str(reference_loc.get(k) or "").strip() for k in ("country", "region", "city")):
                    # Fallback robuste si la requête en ligne ne répond pas.
                    reference_loc = get_location_details(lat, lon)
                    reference_label += " -> fallback local"
            else:
                reference_loc = get_location_details(lat, lon)

            calc_country = str(reference_loc.get("country") or "").strip()
            calc_region = str(reference_loc.get("region") or "").strip()
            calc_city = str(reference_loc.get("city") or "").strip()
            lines.append("")
            lines.append(
                reference_label + " : "
                + f"Pays={calc_country or '-'} | Région={calc_region or '-'} | Ville={calc_city or '-'}"
            )

            checks: list[str] = []
            if country and calc_country:
                checks.append("Pays=OK" if _norm(country) == _norm(calc_country) else "Pays=DIFF")
            elif country or calc_country:
                checks.append("Pays=PARTIEL")

            if region and calc_region:
                checks.append("Région=OK" if _norm(region) == _norm(calc_region) else "Région=DIFF")
            elif region or calc_region:
                checks.append("Région=PARTIEL")

            if city and calc_city:
                checks.append("Ville=OK" if _norm(city) == _norm(calc_city) else "Ville=DIFF")
            elif city or calc_city:
                checks.append("Ville=PARTIEL")

            if checks:
                lines.append("Cohérence : " + " | ".join(checks))

            # Auto-correction: complète uniquement les champs manquants depuis la référence GPS.
            new_country = country or calc_country
            new_region = region or calc_region
            new_city = city or calc_city

            changed_fields: list[str] = []
            if not country and new_country:
                changed_fields.append("Pays")
            if not region and new_region:
                changed_fields.append("Région")
            if not city and new_city:
                changed_fields.append("Ville")

            if changed_fields:
                lines.append("")
                if photo_id <= 0 or not self.db:
                    lines.append(
                        "Correction auto : impossible (photo non persistée en base / mode Sélection)."
                    )
                else:
                    target_path = Path(filepath) if filepath else None
                    exif_ok = True
                    exif_unsupported = False

                    if target_path and target_path.exists():
                        if self._supports_exif_write(target_path):
                            exif_ok = self._update_exif_with_viewer_sync(
                                target_path,
                                lat,
                                lon,
                                new_country,
                                new_region,
                                new_city,
                            )
                        else:
                            exif_unsupported = True

                    if exif_ok:
                        self.db.update_photo_location(photo_id, lat, lon, new_country, new_region, new_city)
                        self._load_data_to_treeview()
                        if hasattr(self, "tree") and self.tree.exists(str(photo_id)):
                            self._suspend_tree_select_once = True
                            self.tree.selection_set(str(photo_id))
                            self.tree.focus(str(photo_id))
                            self.tree.see(str(photo_id))
                        self._update_statistics()

                        lines.append(
                            "Correction auto appliquée : " + ", ".join(changed_fields)
                        )
                        if exif_unsupported:
                            lines.append(
                                "EXIF non écrit (format non supporté), base mise à jour."
                            )
                    else:
                        lines.append(
                            "Correction auto échouée : écriture EXIF impossible, base non modifiée."
                        )

        messagebox.showinfo("Vérification GPS / Géographique", "\n".join(lines))

    def _copy_selected_geo(self) -> None:
        """Copie les informations géographiques de la photo sélectionnée."""
        if self._is_album_scope_active():
            messagebox.showinfo("Copier géolocalisation", "Indisponible en mode Sélection (données GPS non indexées).")
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Copier géolocalisation", "Sélectionnez d'abord une photo.")
            return

        row = self._get_photo_by_id(int(selection[0]))
        if not row:
            return

        clip = {
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "country": str(row.get("country") or ""),
            "region": str(row.get("region") or ""),
            "city": str(row.get("city") or ""),
        }

        available = []
        if clip["latitude"] is not None and clip["longitude"] is not None:
            available.append("GPS")
        if clip["country"]:
            available.append("Pays")
        if clip["region"]:
            available.append("Région")
        if clip["city"]:
            available.append("Ville")

        if not available:
            messagebox.showwarning("Copier géolocalisation", "Aucune donnée géolocalisation disponible sur cette photo.")
            return

        self._geo_clipboard = clip
        messagebox.showinfo("Copier géolocalisation", f"Données copiées: {', '.join(available)}")

    def _paste_geo_to_selection(self) -> None:
        """Colle la géolocalisation copiée vers une ou plusieurs photos, avec choix des champs."""
        if self._is_album_scope_active():
            messagebox.showinfo("Coller géolocalisation", "Indisponible en mode Sélection.")
            return
        if not self.db:
            return
        if not self._geo_clipboard:
            messagebox.showinfo("Coller géolocalisation", "Aucune géolocalisation copiée.")
            return

        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Coller géolocalisation", "Sélectionnez au moins une photo cible.")
            return

        clip = self._geo_clipboard
        has_gps = clip.get("latitude") is not None and clip.get("longitude") is not None
        has_country = bool(str(clip.get("country") or ""))
        has_region = bool(str(clip.get("region") or ""))
        has_city = bool(str(clip.get("city") or ""))

        dlg = tk.Toplevel(self)
        dlg.title("Coller géolocalisation")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        frame = tk.Frame(dlg, padx=14, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text=f"Coller vers {len(selection)} photo(s). Choisissez les champs à appliquer:",
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        var_gps = tk.BooleanVar(value=has_gps)
        var_country = tk.BooleanVar(value=has_country)
        var_region = tk.BooleanVar(value=has_region)
        var_city = tk.BooleanVar(value=has_city)

        cb_gps = tk.Checkbutton(frame, text="GPS (Latitude + Longitude)", variable=var_gps)
        cb_country = tk.Checkbutton(frame, text="Pays", variable=var_country)
        cb_region = tk.Checkbutton(frame, text="Région", variable=var_region)
        cb_city = tk.Checkbutton(frame, text="Ville", variable=var_city)
        cb_gps.pack(anchor="w")
        cb_country.pack(anchor="w")
        cb_region.pack(anchor="w")
        cb_city.pack(anchor="w")

        if not has_gps:
            cb_gps.config(state="disabled")
        if not has_country:
            cb_country.config(state="disabled")
        if not has_region:
            cb_region.config(state="disabled")
        if not has_city:
            cb_city.config(state="disabled")

        result = {"ok": False}

        def _confirm(_event: tk.Event | None = None) -> None:
            if not any((var_gps.get(), var_country.get(), var_region.get(), var_city.get())):
                messagebox.showwarning("Coller géolocalisation", "Sélectionnez au moins un champ à coller.")
                return
            result["ok"] = True
            dlg.destroy()

        def _cancel(_event: tk.Event | None = None) -> None:
            dlg.destroy()

        btns = tk.Frame(frame)
        btns.pack(fill=tk.X, pady=(10, 0))
        tk.Button(btns, text="Appliquer", width=14, command=_confirm, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btns, text="Annuler", width=14, command=_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT)

        dlg.bind("<Return>", _confirm)
        dlg.bind("<Escape>", _cancel)

        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

        self.wait_window(dlg)
        if not result["ok"]:
            return

        apply_gps = var_gps.get()
        apply_country = var_country.get()
        apply_region = var_region.get()
        apply_city = var_city.get()

        updated = 0
        unchanged = 0
        exif_failed = 0
        unsupported = 0

        self._begin_long_action()
        self._begin_photo_progress(len(selection), "Collage géolocalisation")

        for idx, item_id in enumerate(selection):
            if self._stop_event.is_set():
                break
            row = self._get_photo_by_id(int(item_id))
            if not row:
                continue

            old_lat = row.get("latitude")
            old_lon = row.get("longitude")
            old_country = str(row.get("country") or "")
            old_region = str(row.get("region") or "")
            old_city = str(row.get("city") or "")

            lat = old_lat
            lon = old_lon
            country = old_country
            region = old_region
            city = old_city

            if apply_gps and has_gps:
                lat = clip.get("latitude")
                lon = clip.get("longitude")
            if apply_country:
                country = str(clip.get("country") or "")
            if apply_region:
                region = str(clip.get("region") or "")
            if apply_city:
                city = str(clip.get("city") or "")

            if (
                lat == old_lat
                and lon == old_lon
                and country == old_country
                and region == old_region
                and city == old_city
            ):
                unchanged += 1
                self._tick_photo_progress(idx + 1, len(selection), "Collage géolocalisation")
                continue

            target_path = self._abs_path(row.get("filepath"))
            if self._supports_exif_write(target_path):
                if not self._update_exif_with_viewer_sync(target_path, lat, lon, country, region, city):
                    exif_failed += 1
                    self._tick_photo_progress(idx + 1, len(selection), "Collage géolocalisation")
                    continue
            else:
                unsupported += 1

            self.db.update_photo_location(int(item_id), lat, lon, country, region, city)
            updated += 1
            self._tick_photo_progress(idx + 1, len(selection), "Collage géolocalisation")

        if self._stop_event.is_set():
            self.var_counter.set(f"Collage géolocalisation interrompu ({updated + unchanged}/{len(selection)})")
        else:
            self._end_photo_progress(len(selection), "Collage géolocalisation")

        self._load_data_to_treeview()
        self._update_statistics()
        self._end_long_action()

        self._audit_event("paste_geo", {
            "targets": len(selection),
            "updated": updated,
            "unchanged": unchanged,
            "exif_failed": exif_failed,
            "unsupported": unsupported,
            "cancelled": bool(self._stop_event.is_set()),
            "fields": {
                "gps": apply_gps,
                "country": apply_country,
                "region": apply_region,
                "city": apply_city,
            },
        })

        if self._stop_event.is_set():
            messagebox.showwarning(
                "Collage géolocalisation interrompu",
                f"Mises à jour: {updated}\nInchangées: {unchanged}\nÉchec EXIF: {exif_failed}\nFormats non supportés: {unsupported}"
            )
            return

        if exif_failed > 0 or unsupported > 0:
            messagebox.showwarning(
                "Collage géolocalisation terminé",
                f"Mises à jour: {updated}\n"
                f"Inchangées: {unchanged}\n"
                f"Échec écriture EXIF: {exif_failed}\n"
                f"Formats sans écriture EXIF GPS: {unsupported}\n\n"
                "Les fichiers non écrits conservent leurs anciennes valeurs EXIF."
            )
        else:
            messagebox.showinfo(
                "Collage géolocalisation",
                f"Terminé. Mises à jour: {updated}, inchangées: {unchanged}."
            )

    def _get_photo_by_id(self, photo_id: int) -> dict[str, Any] | None:
        """Récupère une photo par son ID (helper interne)."""
        if photo_id < 0:
            return self._album_virtual_rows.get(photo_id)
        if not self.db:
            return None
        return self.db.get_photo_by_id(photo_id)

    def _get_id_from_path(self, filepath: Path) -> int | None:
        """Récupère l'ID d'une photo à partir de son chemin."""
        if not self.db: return None
        row = self.db.get_photo_by_path(self._rel_path(filepath))
        return row["id"] if row else None

    def _suspend_viewer_for_edit(self) -> dict[str, Any]:
        """Masque temporairement la visionneuse pendant l'édition pour éviter le clignotement."""
        self._editing_dialog_open = True
        state = {
            "was_viewable": bool(self.viewer.winfo_ismapped()),
            "was_fullscreen": bool(self.viewer._is_fullscreen),
            "geometry": self.viewer.geometry(),
        }
        self.viewer._stop_slideshow()
        if hasattr(self.viewer, "_resize_timer"):
            try:
                self.viewer.after_cancel(self.viewer._resize_timer)
            except Exception:
                pass
        try:
            self.viewer.withdraw()
        except Exception:
            pass
        return state

    def _restore_viewer_after_edit(self, state: dict[str, Any]) -> None:
        """Restaure la visionneuse à sa position/état d'avant édition."""
        try:
            if not state.get("was_viewable"):
                return

            self.viewer.deiconify()
            if state.get("was_fullscreen"):
                if not self.viewer._is_fullscreen:
                    self.viewer.after(20, self.viewer._go_fullscreen)
            else:
                geometry = state.get("geometry")
                if geometry:
                    try:
                        self.viewer.geometry(str(geometry))
                    except Exception:
                        pass
        finally:
            self._editing_dialog_open = False

    @staticmethod
    def _normalize_base_name(raw_name: str, source_suffix: str | None = None) -> str:
        """Nettoie un nom saisi et retire une éventuelle extension tapée par l'utilisateur."""
        name = re.sub(r'[\\/*?:"<>|]', "", raw_name.strip())
        if not name:
            return ""

        lowered = name.lower()
        if source_suffix and source_suffix.strip():
            suffix = source_suffix.lower().strip()
            if not suffix.startswith("."):
                suffix = f".{suffix}"
            if lowered.endswith(suffix):
                name = name[:-len(suffix)].rstrip()
                lowered = name.lower()

        for ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic"):
            if lowered.endswith(ext):
                name = name[:-len(ext)].rstrip()
                break

        return name

    @staticmethod
    def _parse_coordinate(value: str) -> float | None:
        """Convertit une saisie latitude/longitude en float (ou None)."""
        raw = value.strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _update_exif_with_viewer_sync(
        self,
        filepath: Path,
        latitude: float | None,
        longitude: float | None,
        country: str,
        region: str,
        city: str,
    ) -> bool:
        """Écrit l'EXIF GPS sans interaction avec la visionneuse."""
        ok = update_image_exif_location(filepath, latitude, longitude, country, region, city)
        if ok:
            logger.info("EXIF écrit: %s (lat=%s, lon=%s, country=%s, region=%s, city=%s)", filepath, latitude, longitude, country, region, city)
        else:
            logger.warning("EXIF non écrit: %s (lat=%s, lon=%s, country=%s, region=%s, city=%s)", filepath, latitude, longitude, country, region, city)
        return ok

    @staticmethod
    def _supports_exif_write(filepath: Path) -> bool:
        """Retourne True si le format gère l'écriture EXIF GPS via piexif."""
        return filepath.suffix.lower() in (".jpg", ".jpeg", ".jfif")

    def _resolve_city_candidates(self, city_name: str) -> list[dict[str, Any]]:
        """Retourne des candidats de localisation pour une ville (DB puis base offline)."""
        norm_city = _remove_accents(city_name).lower().strip()
        if not norm_city or not self.db:
            return []

        grouped: dict[tuple[str, str, str, float | None, float | None], dict[str, Any]] = {}
        for row in self.db.get_all_photos(order_by="id"):
            row_city = str(row.get("city") or "").strip()
            if _remove_accents(row_city).lower().strip() != norm_city:
                continue
            lat = row.get("latitude")
            lon = row.get("longitude")
            key = (
                row_city or city_name,
                str(row.get("region") or "").strip(),
                str(row.get("country") or "").strip(),
                round(float(lat), 5) if lat is not None else None,
                round(float(lon), 5) if lon is not None else None,
            )
            if key not in grouped:
                grouped[key] = {
                    "city": key[0],
                    "region": key[1],
                    "country": key[2],
                    "latitude": lat,
                    "longitude": lon,
                    "count": 0,
                    "source": "Bibliothèque"
                }
            grouped[key]["count"] += 1

        db_candidates = sorted(grouped.values(), key=lambda c: c.get("count", 0), reverse=True)
        if db_candidates:
            return db_candidates

        offline = find_city_candidates(city_name, limit=30)
        if offline:
            return offline

        return find_city_candidates_online(city_name, limit=15)

    def _choose_city_candidate(self, city_name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Demande à l'utilisateur de choisir une localisation quand la ville est ambiguë."""
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        dlg = tk.Toplevel(self)
        dlg.title(f"Choisir la localisation - {city_name}")
        dlg.geometry("760x360")
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(
            dlg,
            text=f"Plusieurs localisations trouvées pour '{city_name}'. Sélectionnez la bonne.",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=12, pady=(10, 6))

        tree = ttk.Treeview(dlg, columns=("city", "region", "country", "gps", "source", "count"), show="headings", height=11)
        tree.heading("city", text="Ville")
        tree.heading("region", text="Région")
        tree.heading("country", text="Pays")
        tree.heading("gps", text="GPS")
        tree.heading("source", text="Source")
        tree.heading("count", text="Occur.")

        tree.column("city", width=170, anchor="w")
        tree.column("region", width=150, anchor="w")
        tree.column("country", width=80, anchor="center")
        tree.column("gps", width=190, anchor="w")
        tree.column("source", width=100, anchor="center")
        tree.column("count", width=60, anchor="e")

        for idx, cand in enumerate(candidates):
            lat = cand.get("latitude")
            lon = cand.get("longitude")
            gps = f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else ""
            tree.insert("", "end", iid=str(idx), values=(
                cand.get("city", ""),
                cand.get("region", ""),
                cand.get("country", ""),
                gps,
                cand.get("source", ""),
                cand.get("count", "")
            ))

        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        tree.selection_set("0")
        tree.focus("0")

        result: dict[str, Any] = {"index": None}

        def _ok(_event: tk.Event | None = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            result["index"] = int(selection[0])
            dlg.destroy()

        def _cancel(_event: tk.Event | None = None) -> None:
            dlg.destroy()

        frame_btn = tk.Frame(dlg)
        frame_btn.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Button(frame_btn, text="Choisir", width=14, command=_ok, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(frame_btn, text="Annuler", width=14, command=_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT)

        tree.bind("<Double-1>", _ok)
        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)

        self.wait_window(dlg)
        index = result.get("index")
        if index is None:
            return None
        return candidates[index]

    def _rename_batch(self) -> None:
        """Édite plusieurs photos sélectionnées (Changements communs)."""
        if self._is_album_scope_active():
            messagebox.showinfo("Édition", "Indisponible en mode Sélection.")
            return
        if not self.db: return
        selection = self.tree.selection()
        if not selection: return
        count = len(selection)
        if count <= 1:
            self._rename_single()
            return

        viewer_state = self._suspend_viewer_for_edit()

        # Dialogue personnalisé pour édition en masse
        dlg = tk.Toplevel(self)
        dlg.title(f"Éditer {count} photos")
        dlg.geometry("620x430")
        dlg.configure(bg="#f0f2f5")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"Modification de {count} photos sélectionnées", 
                 font=("Segoe UI", 12, "bold"), fg="#3498db", bg="#f0f2f5").pack(pady=15)
        
        tk.Label(dlg, text="Laissez vide pour ne pas modifier le champ.", 
                 font=("Segoe UI", 9, "italic"), fg="#7f8c8d", bg="#f0f2f5").pack()

        form_frame = tk.Frame(dlg, bg="#f0f2f5")
        form_frame.pack(pady=10, padx=20, fill=tk.X)

        var_name = tk.StringVar()
        var_lat = tk.StringVar()
        var_lon = tk.StringVar()
        var_country = tk.StringVar()
        var_region = tk.StringVar()
        var_city = tk.StringVar()

        tk.Label(form_frame, text="Nom :", fg="#2c3e50", bg="#f0f2f5").grid(row=0, column=0, sticky="e", pady=5)
        tk.Entry(form_frame, textvariable=var_name, width=44).grid(row=0, column=1, columnspan=2, padx=10, pady=5, sticky="w")

        tk.Label(form_frame, text="Latitude :", fg="#2c3e50", bg="#f0f2f5").grid(row=1, column=0, sticky="e", pady=5)
        tk.Entry(form_frame, textvariable=var_lat, width=18).grid(row=1, column=1, padx=(10, 4), pady=5, sticky="w")

        tk.Label(form_frame, text="Longitude :", fg="#2c3e50", bg="#f0f2f5").grid(row=2, column=0, sticky="e", pady=5)
        tk.Entry(form_frame, textvariable=var_lon, width=18).grid(row=2, column=1, padx=(10, 4), pady=5, sticky="w")

        tk.Label(form_frame, text="Pays :", fg="#2c3e50", bg="#f0f2f5").grid(row=3, column=0, sticky="e", pady=5)
        tk.Entry(form_frame, textvariable=var_country, width=44).grid(row=3, column=1, columnspan=2, padx=10, pady=5, sticky="w")

        tk.Label(form_frame, text="Région :", fg="#2c3e50", bg="#f0f2f5").grid(row=4, column=0, sticky="e", pady=5)
        tk.Entry(form_frame, textvariable=var_region, width=44).grid(row=4, column=1, columnspan=2, padx=10, pady=5, sticky="w")

        tk.Label(form_frame, text="Ville :", fg="#2c3e50", bg="#f0f2f5").grid(row=5, column=0, sticky="e", pady=5)
        entry_city = tk.Entry(form_frame, textvariable=var_city, width=30)
        entry_city.grid(row=5, column=1, padx=(10, 4), pady=5, sticky="w")

        def _autofill_from_city(_event: tk.Event | None = None) -> None:
            city_input = var_city.get().strip()
            if not city_input:
                return
            candidates = self._resolve_city_candidates(city_input)
            if not candidates:
                messagebox.showinfo("Compléter", f"Aucune localisation trouvée pour '{city_input}'.")
                return
            selected = self._choose_city_candidate(city_input, candidates)
            if not selected:
                return
            var_city.set(str(selected.get("city") or city_input))
            var_country.set(str(selected.get("country") or ""))
            var_region.set(str(selected.get("region") or ""))
            lat = selected.get("latitude")
            lon = selected.get("longitude")
            var_lat.set(f"{float(lat):.6f}" if lat is not None else "")
            var_lon.set(f"{float(lon):.6f}" if lon is not None else "")

        tk.Button(form_frame, text="Compléter", command=_autofill_from_city, bg="lightgray", fg="black").grid(row=5, column=2, padx=(0, 10), pady=5, sticky="w")
        entry_city.bind("<Return>", _autofill_from_city)

        def _apply():
            logger.info("Edition batch demandee: %d photo(s)", count)
            new_name_raw = var_name.get().strip()
            new_country = var_country.get().strip()
            new_region = var_region.get().strip()
            new_city = var_city.get().strip()
            new_lat = self._parse_coordinate(var_lat.get())
            new_lon = self._parse_coordinate(var_lon.get())

            city_selected: dict[str, Any] | None = None
            if new_city:
                candidates = self._resolve_city_candidates(new_city)
                if len(candidates) == 1:
                    city_selected = candidates[0]
                elif len(candidates) > 1:
                    city_selected = self._choose_city_candidate(new_city, candidates)
                    if not city_selected:
                        return

            if city_selected:
                new_city = str(city_selected.get("city") or new_city)
                new_country = str(city_selected.get("country") or new_country)
                new_region = str(city_selected.get("region") or new_region)
                if city_selected.get("latitude") is not None:
                    new_lat = float(city_selected["latitude"])
                if city_selected.get("longitude") is not None:
                    new_lon = float(city_selected["longitude"])
            
            self._begin_long_action()
            self._begin_photo_progress(count, "Edition batch")
            exif_updated = 0
            exif_failed = 0
            exif_skipped_unsupported = 0
            
            for idx, item_id in enumerate(selection):
                if self._stop_event.is_set():
                    break
                photo_id = int(item_id)
                row = self._get_photo_by_id(photo_id)
                if not row:
                    continue

                # Mise à jour Géo
                if any([new_country, new_region, new_city, new_lat is not None, new_lon is not None]):
                    c = new_country if new_country else (row["country"] or "")
                    r = new_region if new_region else (row["region"] or "")
                    v = new_city if new_city else (row["city"] or "")
                    lat = new_lat if new_lat is not None else row.get("latitude")
                    lon = new_lon if new_lon is not None else row.get("longitude")
                    row_path = Path(row["filepath"])
                    if not self._supports_exif_write(row_path):
                        self.db.update_photo_location(photo_id, lat, lon, c, r, v)
                        exif_skipped_unsupported += 1
                        logger.warning("Edition batch: EXIF non supporte pour %s, DB locale mise a jour.", row_path)
                    elif self._update_exif_with_viewer_sync(row_path, lat, lon, c, r, v):
                        self.db.update_photo_location(photo_id, lat, lon, c, r, v)
                        exif_updated += 1
                    else:
                        exif_failed += 1
                        logger.warning("Edition batch ignoree (EXIF echec) photo_id=%s path=%s", photo_id, row.get("filepath"))
                
                # Mise à jour Nom Collection (on garde le suffixe original ou séquentiel ?)
                # En mode batch, on remplace juste le nom de base, l'unicité sera gérée au prochain scan ou manuellement
                if new_name_raw:
                    source_suffix = Path(row["filepath"]).suffix if row else ""
                    new_name = self._normalize_base_name(new_name_raw, source_suffix)
                    if not new_name:
                        continue
                    self.db.update_single_collection_name(photo_id, new_name)
                
                self._tick_photo_progress(idx + 1, count, "Edition batch")

            if self._stop_event.is_set():
                self.var_counter.set("Edition batch interrompue")
            else:
                self._end_photo_progress(count, "Edition batch")

            self._load_data_to_treeview()
            self._end_long_action()
            logger.info("Edition batch terminee: total=%d exif_ok=%d exif_fail=%d", count, exif_updated, exif_failed)
            self._audit_event("edit_batch", {
                "selected": count,
                "exif_ok": exif_updated,
                "exif_failed": exif_failed,
                "exif_unsupported": exif_skipped_unsupported,
                "updated_name": bool(new_name_raw),
                "updated_geo": bool(any([new_country, new_region, new_city, new_lat is not None, new_lon is not None])),
                "cancelled": bool(self._stop_event.is_set()),
            })
            if self._stop_event.is_set():
                messagebox.showwarning("Edition batch", "Opération interrompue par l'utilisateur.")
                dlg.destroy()
                return
            if exif_failed > 0:
                messagebox.showwarning(
                    "EXIF non écrit",
                    f"{count} photos mises à jour, mais {exif_failed} fichier(s) n'ont pas pu être écrits en EXIF.\n"
                    "Ces localisations ne seront pas fiables après un rescan complet."
                )
            elif exif_skipped_unsupported > 0:
                messagebox.showwarning(
                    "Format sans EXIF GPS",
                    f"{count} photos mises à jour. {exif_skipped_unsupported} fichier(s) ne supportent pas l'écriture EXIF GPS (format non JPEG).\n"
                    "La géolocalisation est enregistrée localement dans la base, mais peut être perdue après un rescan complet."
                )
            elif exif_updated > 0:
                messagebox.showinfo("Succès", f"{count} photos mises à jour. EXIF GPS écrit pour {exif_updated} fichier(s).")
            else:
                messagebox.showinfo("Succès", f"{count} photos mises à jour.")
            dlg.destroy()

        tk.Button(dlg, text="Appliquer les changements", command=_apply, **self.btn_colors).pack(pady=20)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        try:
            self.wait_window(dlg)
        finally:
            self._restore_viewer_after_edit(viewer_state)

    def _rename_single(self) -> None:
        """Renomme le nom de collection et les infos géo d'une photo (F2)."""
        if self._is_album_scope_active():
            self._rename_album_single()
            return
        if not self.db: return
        selection = self.tree.selection()
        if not selection: return
        if len(selection) > 1:
            messagebox.showinfo("Renommer", "Sélectionnez une seule photo pour la renommer.")
            return

        item_id = selection[0]
        photo_id = int(item_id)
        row = next((r for r in self.db.get_all_photos(order_by="id") if r["id"] == photo_id), None)
        if not row: return

        current_name = row["collection_name"] or ""
        current_country = row["country"] or ""
        current_region = row["region"] or ""
        current_city = row["city"] or ""
        current_lat = row.get("latitude")
        current_lon = row.get("longitude")
        source_path = Path(row["filepath"])

        viewer_state = self._suspend_viewer_for_edit()

        # Dialogue personnalisé
        dlg = tk.Toplevel(self)
        dlg.title("Éditer la photo")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)

        tk.Label(dlg, text=f"Fichier : {source_path.name}", font=("Arial", 10, "italic"), fg="#555").grid(
            row=0, column=0, columnspan=3, padx=15, pady=(15, 10), sticky="w")

        var_name = tk.StringVar(value=current_name)
        var_lat = tk.StringVar(value=f"{float(current_lat):.6f}" if current_lat is not None else "")
        var_lon = tk.StringVar(value=f"{float(current_lon):.6f}" if current_lon is not None else "")
        var_country = tk.StringVar(value=current_country)
        var_region = tk.StringVar(value=current_region)
        var_city = tk.StringVar(value=current_city)

        tk.Label(dlg, text="Nom :", font=("Arial", 10)).grid(row=1, column=0, padx=(15, 5), pady=4, sticky="e")
        tk.Entry(dlg, textvariable=var_name, width=45, font=("Arial", 10)).grid(row=1, column=1, columnspan=2, padx=(0, 15), pady=4)

        tk.Label(dlg, text="Latitude :", font=("Arial", 10)).grid(row=2, column=0, padx=(15, 5), pady=4, sticky="e")
        tk.Entry(dlg, textvariable=var_lat, width=20, font=("Arial", 10)).grid(row=2, column=1, padx=(0, 6), pady=4, sticky="w")

        tk.Label(dlg, text="Longitude :", font=("Arial", 10)).grid(row=3, column=0, padx=(15, 5), pady=4, sticky="e")
        tk.Entry(dlg, textvariable=var_lon, width=20, font=("Arial", 10)).grid(row=3, column=1, padx=(0, 6), pady=4, sticky="w")

        tk.Label(dlg, text="Pays :", font=("Arial", 10)).grid(row=4, column=0, padx=(15, 5), pady=4, sticky="e")
        tk.Entry(dlg, textvariable=var_country, width=45, font=("Arial", 10)).grid(row=4, column=1, columnspan=2, padx=(0, 15), pady=4)

        tk.Label(dlg, text="Région :", font=("Arial", 10)).grid(row=5, column=0, padx=(15, 5), pady=4, sticky="e")
        tk.Entry(dlg, textvariable=var_region, width=45, font=("Arial", 10)).grid(row=5, column=1, columnspan=2, padx=(0, 15), pady=4)

        tk.Label(dlg, text="Ville :", font=("Arial", 10)).grid(row=6, column=0, padx=(15, 5), pady=4, sticky="e")
        entry_city = tk.Entry(dlg, textvariable=var_city, width=31, font=("Arial", 10))
        entry_city.grid(row=6, column=1, padx=(0, 6), pady=4, sticky="w")

        def _autofill_from_city(_event: tk.Event | None = None) -> None:
            city_input = var_city.get().strip()
            if not city_input:
                return
            candidates = self._resolve_city_candidates(city_input)
            if not candidates:
                messagebox.showinfo("Compléter", f"Aucune localisation trouvée pour '{city_input}'.")
                return
            selected = self._choose_city_candidate(city_input, candidates)
            if not selected:
                return
            var_city.set(str(selected.get("city") or city_input))
            var_country.set(str(selected.get("country") or ""))
            var_region.set(str(selected.get("region") or ""))
            lat = selected.get("latitude")
            lon = selected.get("longitude")
            var_lat.set(f"{float(lat):.6f}" if lat is not None else "")
            var_lon.set(f"{float(lon):.6f}" if lon is not None else "")

        tk.Button(dlg, text="Compléter", command=_autofill_from_city, bg="lightgray", fg="black").grid(row=6, column=2, padx=(0, 15), pady=4, sticky="w")
        entry_city.bind("<Return>", _autofill_from_city)

        result = {"confirmed": False}

        def _on_ok(event=None):
            result["confirmed"] = True
            dlg.destroy()

        def _on_cancel(event=None):
            dlg.destroy()

        frame_btns = tk.Frame(dlg)
        frame_btns.grid(row=7, column=0, columnspan=3, pady=(10, 15))
        tk.Button(frame_btns, text="OK", width=12, command=_on_ok, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=5)
        tk.Button(frame_btns, text="Annuler", width=12, command=_on_cancel, bg="lightgray", fg="black").pack(side=tk.LEFT, padx=5)

        dlg.bind("<Return>", _on_ok)
        dlg.bind("<Escape>", _on_cancel)

        # Centrer sur la fenêtre parent
        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

        dlg.focus_force()

        self.wait_window(dlg)
        try:
            if not result["confirmed"]:
                logger.info("Edition simple annulee: photo_id=%s", photo_id)
                return

            new_name = self._normalize_base_name(var_name.get(), source_path.suffix)
            new_city = var_city.get().strip()
            if new_city:
                # Finalise la cohérence Ville -> Pays/Région/GPS à la validation.
                candidates = self._resolve_city_candidates(new_city)
                if len(candidates) == 1:
                    selected = candidates[0]
                elif len(candidates) > 1:
                    selected = self._choose_city_candidate(new_city, candidates)
                    if not selected:
                        return
                else:
                    selected = None
                if selected:
                    var_city.set(str(selected.get("city") or new_city))
                    var_country.set(str(selected.get("country") or ""))
                    var_region.set(str(selected.get("region") or ""))
                    lat = selected.get("latitude")
                    lon = selected.get("longitude")
                    if lat is not None:
                        var_lat.set(f"{float(lat):.6f}")
                    if lon is not None:
                        var_lon.set(f"{float(lon):.6f}")

            new_country = var_country.get().strip()
            new_region = var_region.get().strip()
            new_city = var_city.get().strip()
            new_lat = self._parse_coordinate(var_lat.get())
            new_lon = self._parse_coordinate(var_lon.get())

            current_lat_norm = round(float(current_lat), 6) if current_lat is not None else None
            current_lon_norm = round(float(current_lon), 6) if current_lon is not None else None
            new_lat_norm = round(float(new_lat), 6) if new_lat is not None else None
            new_lon_norm = round(float(new_lon), 6) if new_lon is not None else None
            geo_changed = (
                new_lat_norm != current_lat_norm
                or new_lon_norm != current_lon_norm
                or new_country != current_country
                or new_region != current_region
                or new_city != current_city
            )

            logger.info(
                "Edition simple saisie: photo_id=%s old=(%s,%s,%s,%s,%s) new=(%s,%s,%s,%s,%s)",
                photo_id,
                current_lat,
                current_lon,
                current_country,
                current_region,
                current_city,
                new_lat,
                new_lon,
                new_country,
                new_region,
                new_city,
            )

            if not new_name:
                messagebox.showwarning("Erreur", "Le nom ne peut pas être vide.")
                return

            # Renommer le fichier physique si le nom a changé
            new_filename = new_name + source_path.suffix
            new_path = source_path.with_name(new_filename)
            target_path = source_path
            target_filename = source_path.name

            if new_name != current_name:
                if new_path.exists() and new_path != source_path:
                    messagebox.showwarning("Erreur", f"Le fichier {new_filename} existe déjà.")
                    return
                try:
                    if source_path.exists():
                        source_path.rename(new_path)
                    self.db.update_photo_path(photo_id, str(new_path), new_filename)
                    target_path = new_path
                    target_filename = new_filename
                except Exception as e:
                    logger.error("Erreur renommage fichier : %s", e)
                    messagebox.showerror("Erreur", f"Impossible de renommer le fichier :\n{e}")
                    return

            # Mettre à jour la DB
            self.db.update_single_collection_name(photo_id, new_name)
            if geo_changed:
                if self._supports_exif_write(target_path):
                    if not self._update_exif_with_viewer_sync(target_path, new_lat, new_lon, new_country, new_region, new_city):
                        messagebox.showerror(
                            "Erreur EXIF",
                            "Impossible d'écrire les coordonnées dans le fichier image.\n"
                            "La localisation n'a pas été enregistrée pour éviter un retour des anciennes valeurs au prochain rescan."
                        )
                        logger.error("Edition simple rejetee: echec EXIF photo_id=%s path=%s", photo_id, target_path)
                        return
                else:
                    logger.warning("Edition simple: EXIF non supporte pour %s, DB locale mise a jour.", target_path)
                self.db.update_photo_location(photo_id, new_lat, new_lon, new_country, new_region, new_city)
                logger.info("Edition simple geoloc enregistree: photo_id=%s", photo_id)
                if not self._supports_exif_write(target_path):
                    messagebox.showwarning(
                        "Format sans EXIF GPS",
                        "Ce format d'image ne supporte pas l'écriture EXIF GPS (non JPEG).\n"
                        "La géolocalisation est enregistrée localement, mais peut être perdue après un rescan complet."
                    )

            # Mettre à jour la ligne du tableau
            vals = list(self.tree.item(item_id, "values"))
            col_idx = {col: idx for idx, col in enumerate(self.tree["columns"])}
            if "collection_name" in col_idx:
                vals[col_idx["collection_name"]] = new_name
            if "filename" in col_idx:
                vals[col_idx["filename"]] = target_filename
            if "gps_coords" in col_idx:
                vals[col_idx["gps_coords"]] = f"🔗 {new_lat:.5f}, {new_lon:.5f}" if new_lat is not None and new_lon is not None else ""
            if "country" in col_idx:
                vals[col_idx["country"]] = new_country
            if "region" in col_idx:
                vals[col_idx["region"]] = new_region
            if "city" in col_idx:
                vals[col_idx["city"]] = new_city
            self.tree.item(item_id, values=vals)

            # Rafraîchir la visionneuse
            self._display_on_viewers(target_path, allow_during_edit=True)

            self._audit_event("edit_single", {
                "photo_id": photo_id,
                "renamed": bool(new_name != current_name),
                "geo_changed": bool(geo_changed),
                "target_path": str(target_path),
            })

            logger.info("Édité : %s → %s (Lat=%s, Lon=%s, Pays=%s, Région=%s, Ville=%s)",
                     source_path.name, new_filename, new_lat, new_lon, new_country, new_region, new_city)
        finally:
            self._restore_viewer_after_edit(viewer_state)

    def _rename_album_single(self) -> None:
        """Renomme un fichier dans le dossier de sélection (sans toucher à la DB source)."""
        selection = self.tree.selection()
        if not selection:
            return
        if len(selection) > 1:
            messagebox.showinfo("Renommer", "Sélectionnez une seule photo de la sélection pour la renommer.")
            return

        row = self._get_photo_by_id(int(selection[0]))
        if not row or not row.get("filepath"):
            return

        source_path = self._abs_path(row.get("filepath"))
        if not source_path.exists():
            messagebox.showwarning("Renommer", "Le fichier n'existe plus sur le disque.")
            return

        current_name = source_path.stem
        raw = simpledialog.askstring("Renommer (Sélection)", "Nouveau nom :", initialvalue=current_name, parent=self)
        if raw is None:
            return

        new_base = self._normalize_base_name(raw, source_path.suffix)
        if not new_base:
            messagebox.showwarning("Erreur", "Le nom ne peut pas être vide.")
            return

        new_path = source_path.with_name(new_base + source_path.suffix)
        if new_path == source_path:
            return
        if new_path.exists():
            messagebox.showwarning("Erreur", f"Le fichier {new_path.name} existe déjà.")
            return

        try:
            source_path.rename(new_path)
            self._remove_album_index_entry(source_path)
            self._sync_album_index()
            self._load_data_to_treeview()
            self._update_statistics()
            self._audit_event("rename_album", {"from": str(source_path), "to": str(new_path)})
        except Exception as e:
            logger.error("Erreur renommage album: %s", e)
            messagebox.showerror("Erreur", f"Impossible de renommer le fichier :\n{e}")

    # ── NAVIGATION & COPIE ───────────────────────────────────────────

    def navigate_tree(self, direction: str) -> None:
        """Navigue dans l'arborescence (haut/bas) suivant la demande de la visionneuse."""
        scope = str(self.var_viewer_scope.get() if hasattr(self, "var_viewer_scope") else self.config_data.get("viewer_scope", "folder"))
        if scope == "album":
            album_paths = self._get_album_ordered_paths()
            if album_paths:
                current_path = self.viewer._current_path
                if current_path in album_paths:
                    idx = album_paths.index(current_path)
                else:
                    idx = 0 if direction == "next" else len(album_paths) - 1

                target_idx = idx + (1 if direction == "next" else -1)
                if 0 <= target_idx < len(album_paths):
                    self._display_on_viewers(album_paths[target_idx], grab_focus=False)
                return

        selection = self.tree.selection()
        if not selection: return
        current = selection[0]
        
        if direction == "next": target = self.tree.next(current)
        else: target = self.tree.prev(current)
            
        if target:
            self.tree.selection_set(target)
            self.tree.see(target)

    def copy_current(self) -> None:
        """Copie le fichier courant (Touche Espace)."""
        if self._is_album_scope_active():
            messagebox.showinfo("Export", "Indisponible en mode Sélection.")
            return
        if not self.db or not self.var_out.get(): return
        selection = self.tree.selection()
        if not selection: return
        photo_id = int(selection[0])
        
        row = next((r for r in self.db.get_all_photos(order_by="id") if r["id"] == photo_id), None)
        if not row: return
        
        source_path = Path(row["filepath"])
        dest_dir = Path(self.var_out.get().strip())
        coll_name = row["collection_name"]
        if not coll_name: return
            
        new_name = coll_name + source_path.suffix
        dest_path = dest_dir / new_name
        
        ensure_directory(dest_dir)
        
        if dest_path.exists():
            # Vérifier si c'est déjà la même photo (taille identique par exemple)
            try:
                if dest_path.stat().st_size == source_path.stat().st_size:
                    # Même taille, on suppose que c'est déjà copié.
                    self.db.set_copied_state(photo_id, True)
                    vals = list(self.tree.item(str(photo_id), "values"))
                    vals[0] = "Oui"
                    self.tree.item(str(photo_id), values=vals)
                    self._update_copies_counter()
                    self._update_statistics()
                    return
                else:
                    # Nom identique mais taille différente : Conflit !
                    answer = messagebox.askyesno(
                        "Conflit de nom",
                        f"Un fichier nommé '{new_name}' existe déjà dans la destination mais semble différent.\n\nVoulez-vous l'écraser ?"
                    )
                    if not answer:
                        return
            except Exception:
                pass

        try:
            shutil.copy2(source_path, dest_path)
            self.db.set_copied_state(photo_id, True)
            
            # Mise à jour UI : On change la première col
            vals = list(self.tree.item(str(photo_id), "values"))
            vals[0] = "Oui"
            self.tree.item(str(photo_id), values=vals)
            
            self._update_copies_counter()
            self._update_statistics()
        except Exception as e:
            logger.error("Erreur copie : %s", e)

    def delete_current(self) -> None:
        """Supprime les photos sélectionnées (Touche Suppr). Multi-sélection supportée."""
        if self._is_album_scope_active():
            self._delete_current_album()
            return
        if self._is_virtual_album_scope_active():
            self._remove_selected_from_current_virtual_album()
            return
        if not self.db: return
        selection = self.tree.selection()
        if not selection: return
        
        try:
            from send2trash import send2trash
        except ImportError:
            logger.error("send2trash n'est pas installé.")
            messagebox.showerror("Erreur", "Le composant send2trash est requis pour cette action.")
            return

        count = len(selection)
        if count > 1:
            answer = messagebox.askyesno(
                "Confirmation de suppression",
                f"Vous allez envoyer {count} photos à la corbeille.\n\n"
                f"• Photos copiées : seule la copie sera supprimée.\n"
                f"• Photos non copiées : l'original sera envoyé à la corbeille.\n\n"
                f"Continuer ?"
            )
            if not answer:
                return

        db_records = self.db.get_all_photos(order_by="id")
        items_to_delete = []  # items à retirer du tree (non copiés)
        deleted_from_db = 0

        total = len(selection)
        try:
            self.progress.stop()
        except Exception:
            pass
        self.progress.configure(mode="determinate", maximum=max(1, total), value=0)
        self.var_counter.set(f"Suppression : 0/{total}")
        self.update_idletasks()

        for idx, item_id in enumerate(selection):
            photo_id = int(item_id)
            row = next((r for r in db_records if r["id"] == photo_id), None)
            if not row: continue

            source_path = self._abs_path(row.get("filepath"))
            is_copied = row.get("is_copied", 0) == 1

            if is_copied and self.var_out.get():
                # Si copié : Met la copie à la corbeille, garde l'original.
                dest_dir = Path(self.var_out.get().strip())
                coll_name = row["collection_name"]
                if coll_name:
                    dest_path = dest_dir / (coll_name + source_path.suffix)
                    if dest_path.exists():
                        try:
                            send2trash(str(dest_path.resolve()))
                            self.db.set_copied_state(photo_id, False)
                            vals = list(self.tree.item(item_id, "values"))
                            vals[0] = ""
                            self.tree.item(item_id, values=vals)
                        except Exception as e:
                            logger.error("Erreur suppr copie (corbeille) : %s", e)
            else:
                # Si NON copié : Met l'original à la corbeille, supprime de la DB.
                if source_path.exists():
                    try:
                        send2trash(str(source_path.resolve()))
                    except Exception as e:
                        logger.error("Erreur suppr originale (corbeille) : %s", e)
                cleanup = self.db.delete_photo(photo_id)
                self._cleanup_face_artifacts_for_deleted_photo(cleanup)
                items_to_delete.append(item_id)
                deleted_from_db += 1

            # Mise à jour progression
            self.progress.configure(value=idx + 1)
            self.var_counter.set(f"Suppression : {idx + 1}/{total}")
            self.update_idletasks()

        # Trouver le prochain élément à sélectionner
        if items_to_delete:
            last_item = items_to_delete[-1]
            next_sel = self.tree.next(last_item)
            if not next_sel:
                next_sel = self.tree.prev(items_to_delete[0])
            
            for item_id in items_to_delete:
                self.tree.delete(item_id)
            
            if next_sel and self.tree.exists(next_sel):
                self.tree.selection_set(next_sel)
                self.tree.focus(next_sel)
                self.tree.see(next_sel)
            else:
                self.viewer_photo.canvas.delete("all")
                self.viewer_grid.canvas.delete("all")

        self._update_copies_counter()
        if deleted_from_db > 0:
            self._invalidate_duplicate_cache()
            self.progress.configure(mode="determinate", maximum=max(1, total), value=max(1, total))
            if self._is_duplicates_scope_active():
                self._prepare_duplicate_scope_async(reindex=True)
        self._update_statistics()
        self.var_counter.set(
            f"Suppression terminée. {len(items_to_delete)} originaux et {total - len(items_to_delete)} copies envoyés à la corbeille."
        )

    def _cleanup_face_artifacts_for_deleted_photo(self, cleanup: dict[str, list[str]] | None) -> None:
        if not cleanup:
            return

        faces_root = self._get_faces_root_dir()
        if faces_root is None:
            return

        thumb_relpaths = cleanup.get("thumb_relpaths") or []
        for rel in thumb_relpaths:
            rel_clean = str(rel or "").strip()
            if not rel_clean:
                continue
            thumb_path = faces_root / rel_clean
            if thumb_path.exists():
                try:
                    thumb_path.unlink()
                except Exception as exc:
                    logger.debug("[faces] Suppression miniature orpheline impossible: %s (%s)", thumb_path, exc)

        orphan_folders = cleanup.get("orphan_folders") or []
        for folder_name in orphan_folders:
            folder_clean = str(folder_name or "").strip()
            if not folder_clean:
                continue
            folder_path = faces_root / folder_clean
            if folder_path.exists() and folder_path.is_dir():
                try:
                    next(folder_path.iterdir())
                except StopIteration:
                    try:
                        folder_path.rmdir()
                    except Exception as exc:
                        logger.debug("[faces] Suppression dossier orphelin impossible: %s (%s)", folder_path, exc)
                except Exception as exc:
                    logger.debug("[faces] Lecture dossier visage impossible: %s (%s)", folder_path, exc)

    def _delete_current_album(self) -> None:
        """Supprime les photos sélectionnées du dossier de sélection (corbeille)."""
        selection = self.tree.selection()
        if not selection:
            return

        try:
            from send2trash import send2trash
        except ImportError:
            logger.error("send2trash n'est pas installé.")
            messagebox.showerror("Erreur", "Le composant send2trash est requis pour cette action.")
            return

        count = len(selection)
        if count > 1:
            answer = messagebox.askyesno(
                "Confirmation de suppression",
                f"Vous allez envoyer {count} photo(s) de la sélection à la corbeille.\n\nContinuer ?"
            )
            if not answer:
                return

        removed = 0
        failed = 0
        for item_id in selection:
            row = self._get_photo_by_id(int(item_id))
            if not row:
                continue
            path = self._abs_path(str(row.get("filepath") or ""))
            if not path or not path.exists():
                continue
            try:
                send2trash(str(path.resolve()))
                self._remove_album_index_entry(path)
                removed += 1
            except Exception:
                failed += 1
        # Sauvegarder le prochain élément à sélectionner par son filepath
        target_filepath: str | None = None
        if selection:
            last_item = selection[-1]
            next_iid = self.tree.next(last_item) or self.tree.prev(selection[0])
            if next_iid:
                row_next = self._get_photo_by_id(int(next_iid))
                if row_next:
                    target_filepath = str(row_next.get("filepath") or "")

        self._load_data_to_treeview()
        self._update_statistics()

        # Restaurer la sélection par filepath
        if target_filepath:
             for item in self.tree.get_children():
                 row_check = self._get_photo_by_id(int(item))
                 if row_check and str(row_check.get("filepath") or "") == target_filepath:
                     self.tree.selection_set(item)
                     self.tree.focus(item)
                     self.tree.see(item)
                     break
        self._audit_event("delete_album", {"selected": count, "removed": removed, "failed": failed})
        if failed > 0:
            messagebox.showwarning("Suppression sélection", f"Supprimées: {removed}, en échec: {failed}.")

    def _update_copies_counter(self) -> None:
        if self._is_album_scope_active():
            self.var_copies.set(f"Sélection : {len(self._album_virtual_rows)}")
            return
        if self._is_virtual_album_scope_active():
            self.var_copies.set(f"Album : {len(self.tree.get_children())}")
            return
        if self.db:
            count = self.db.get_copied_count()
            self.var_copies.set(f"Exportées : {count}")

    def _on_closing(self) -> None:
        """Sauvegarde les paramètres à la fermeture globale de l'app."""
        if not self._confirm_pending_face_edits_before_leave("l'application"):
            return
        self._stop_event.set()
        self._face_batch_stop.set()
        if self._faces_preload_job is not None:
            try:
                self.after_cancel(self._faces_preload_job)
            except Exception:
                pass
            self._faces_preload_job = None
        self._stop_face_worker(reason="Arrêt à la fermeture")
        self._cancel_background_count()
        self._stop_album_watch()
        self._stop_watcher()
        self.config_data["geometry_main"] = self.geometry().replace("+-", "-")
        self.config_data["state_main"] = self.state()
        self._save_config()
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=1.0)
        if self._audit_thread and self._audit_thread.is_alive():
            self._audit_thread.join(timeout=1.0)
        if self._duplicate_prepare_thread and self._duplicate_prepare_thread.is_alive():
            self._duplicate_prepare_thread.join(timeout=1.0)
        if self._face_batch_thread and self._face_batch_thread.is_alive():
            self._face_batch_thread.join(timeout=1.0)
            
        self.update_idletasks()
        
        # Gestion du panneau de contrôle principal
        if self.state() == "zoomed":
            self.config_data["state_main"] = "zoomed"
        else:
            self.config_data["state_main"] = "normal"
            self.config_data["geometry_main"] = self.geometry().replace("+-", "-")
            
        # Gestion de la visionneuse : sauvegarder sa géométrie
        was_fs = self.viewer._is_fullscreen
        if was_fs:
            self.viewer._exit_fullscreen()
        if self.viewer.winfo_viewable():
            self.config_data["geometry_viewer"] = self.viewer.geometry().replace("+-", "-")
        self.config_data["state_viewer_fullscreen"] = was_fs

        # Mémoriser la dernière photo affichée pour reprise à la même position.
        last_source = self.var_in.get().strip() if hasattr(self, "var_in") else ""
        last_view = ""
        for viewer in (self.viewer, self.viewer_photo, self.viewer_grid):
            if viewer._current_path and viewer._current_path.exists():
                last_view = str(viewer._current_path)
                break
        else:
            selection = self.tree.selection() if hasattr(self, "tree") else ()
            if selection and self.db:
                row = self._get_photo_by_id(int(selection[0]))
                if row and row.get("filepath"):
                    path = self._abs_path(row["filepath"])
                    if path and path.exists():
                        last_view = str(path)

        self.config_data["last_viewed_source"] = last_source
        self.config_data["last_viewed_path"] = last_view
            
        self._save_config()
        
        # Fermer explicitement la visionneuse avant de détruire l'app
        for viewer in (self.viewer_photo, self.viewer_grid):
            try:
                viewer.destroy()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    # Activer la prise en charge DPI par moniteur (Per-Monitor v2)
    # Indispensable pour que les coordonnées Tkinter et ctypes soient cohérentes
    # sur des configurations multi-écrans avec des résolutions/scaling différents.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    ensure_directory(APP_LOG_DIR)
    runtime_log_file = APP_LOG_DIR / "runtime.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(runtime_log_file, encoding="utf-8"),
        ],
    )
    logger.info("[app] Démarrage PhotoSelect. Logs runtime: %s", runtime_log_file)
    app = ControlPanel()
    app.mainloop()


if __name__ == "__main__":
    main()
