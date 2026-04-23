# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_THEME_MODEL_DIR = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "PhotoSelect" / "models"
_THEME_MODEL_PATH = _THEME_MODEL_DIR / "imagenet_mobilenetv2.onnx"
_THEME_LABELS_PATH = _THEME_MODEL_DIR / "imagenet_labels.json"
_THEME_LABELS_TXT_PATH = _THEME_MODEL_DIR / "imagenet_labels.txt"

_THEME_ACTIVE_MODEL_ROOT: Path | None = None
_THEME_ACTIVE_MODEL_PATH: Path = _THEME_MODEL_PATH
_THEME_ACTIVE_LABELS_JSON_PATH: Path = _THEME_LABELS_PATH
_THEME_ACTIVE_LABELS_TXT_PATH: Path = _THEME_LABELS_TXT_PATH

_THEME_ENGINE_LOCK = threading.Lock()
_THEME_ENGINE_CACHE: dict[str, Any] = {
    "net": None,
    "labels": [],
    "ready": False,
    "backend": "none",
    "reason": "",
}

# Mapping thématique -> mots-clés ImageNet fréquents.
_THEME_IMAGENET_KEYWORDS: dict[str, list[str]] = {
    "animals": [
        "dog", "cat", "bird", "horse", "cow", "goat", "sheep", "lion", "tiger", "elephant",
        "bear", "monkey", "penguin", "dolphin", "whale", "fox", "wolf", "deer", "camel", "zebra",
    ],
    "insects": [
        "insect", "bee", "beetle", "butterfly", "dragonfly", "fly", "mosquito", "ant", "spider", "moth",
        "ladybug", "locust", "mantis", "cricket", "cicada", "cockroach", "weevil", "grasshopper",
    ],
    "flowers": [
        "flower", "rose", "daisy", "sunflower", "orchid", "tulip", "lily", "lotus", "hibiscus", "poppy",
    ],
    "temples": [
        "temple", "pagoda", "monastery", "shrine", "stupa",
    ],
    "churches": [
        "church", "cathedral", "chapel", "basilica", "steeple", "bell cote",
    ],
    "statues": [
        "statue", "sculpture", "figurine", "figure", "stupa", "stela", "stele", "monument",
        "totem pole", "obelisk", "pedestal", "gargoyle", "column", "pillar",
    ],
}


def _normalize_label(label: str) -> str:
    return str(label or "").strip().lower()


def _candidate_model_roots() -> list[Path]:
    roots: list[Path] = []

    # 1) Repo/dev: src/photoselect/assets/models
    try:
        roots.append((Path(__file__).resolve().parent / "assets" / "models"))
    except Exception:
        pass

    # 2) PyInstaller: _MEIPASS/src/photoselect/assets/models
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(str(meipass)) / "src" / "photoselect" / "assets" / "models")
    except Exception:
        pass

    # 3) Fallback local utilisateur
    roots.append(_THEME_MODEL_DIR)
    return roots


def _resolve_model_paths() -> tuple[Path, Path, Path, Path | None]:
    candidates = _candidate_model_roots()
    
    # Listes de noms possibles pour les fichiers
    model_names = ["imagenet_mobilenetv2.onnx", "mobilenetv2-12.onnx", "mobilenetv2.onnx"]
    json_names = ["imagenet_labels.json", "imagenet-simple-labels.json", "labels.json"]
    txt_names = ["imagenet_labels.txt", "labels.txt"]

    for root in candidates:
        if not root.exists():
            continue
        logger.debug("[theme] Checking root: %s", root)
        
        # Trouver le premier modèle qui existe
        found_model = None
        for name in model_names:
            p = root / name
            if p.exists():
                found_model = p
                break
        
        if not found_model:
            continue
            
        # Trouver les labels associés
        found_json = None
        for name in json_names:
            p = root / name
            if p.exists():
                found_json = p
                break
                
        found_txt = None
        for name in txt_names:
            p = root / name
            if p.exists():
                found_txt = p
                break
        
        if found_model and (found_json or found_txt):
            # On retourne des chemins valides, mais on s'assure que si l'un manque, le Path pointe vers un défaut
            return (
                found_model, 
                found_json or (root / "imagenet_labels.json"), 
                found_txt or (root / "imagenet_labels.txt"), 
                root
            )

    # Aucun bundle trouvé: on retourne les chemins fallback AppData par défaut.
    return _THEME_MODEL_PATH, _THEME_LABELS_PATH, _THEME_LABELS_TXT_PATH, None


def _load_labels(labels_json_path: Path, labels_txt_path: Path) -> list[str]:
    if labels_json_path.exists():
        try:
            data = json.loads(labels_json_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data]
            if isinstance(data, dict):
                # Si c'est un dictionnaire, on essaie de trier par clé (souvent index ImageNet)
                try:
                    sorted_keys = sorted(data.keys(), key=lambda x: int(x))
                    return [str(data[k]) for k in sorted_keys]
                except Exception:
                    # En dernier recours on prend juste les valeurs
                    return [str(v) for v in data.values()]
        except Exception as e:
            logger.error("[theme] Erreur lecture JSON labels: %s", e)
    if labels_txt_path.exists():
        return [line.strip() for line in labels_txt_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return []


def _ensure_engine_loaded() -> dict[str, Any]:
    global _THEME_ACTIVE_MODEL_ROOT
    global _THEME_ACTIVE_MODEL_PATH
    global _THEME_ACTIVE_LABELS_JSON_PATH
    global _THEME_ACTIVE_LABELS_TXT_PATH

    with _THEME_ENGINE_LOCK:
        if _THEME_ENGINE_CACHE.get("ready"):
            return _THEME_ENGINE_CACHE

        try:
            import cv2
        except Exception as e:
            _THEME_ENGINE_CACHE.update({
                "ready": False,
                "backend": "none",
                "reason": f"opencv indisponible: {e}",
            })
            return _THEME_ENGINE_CACHE

        try:
            _THEME_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        model_path, labels_json_path, labels_txt_path, active_root = _resolve_model_paths()
        _THEME_ACTIVE_MODEL_ROOT = active_root
        _THEME_ACTIVE_MODEL_PATH = model_path
        _THEME_ACTIVE_LABELS_JSON_PATH = labels_json_path
        _THEME_ACTIVE_LABELS_TXT_PATH = labels_txt_path

        if not model_path.exists() or (not labels_json_path.exists() and not labels_txt_path.exists()):
            _THEME_ENGINE_CACHE.update({
                "ready": False,
                "backend": "none",
                "reason": (
                    "Moteur vision désactivé : les fichiers d'IA sont manquants. "
                    "Veuillez copier 'imagenet_mobilenetv2.onnx' et 'imagenet_labels.json' "
                    f"dans {_THEME_MODEL_DIR}"
                ),
            })
            return _THEME_ENGINE_CACHE

        try:
            labels = _load_labels(labels_json_path, labels_txt_path)
            if not labels:
                raise ValueError("labels invalides")
            net = cv2.dnn.readNetFromONNX(str(model_path))
            _THEME_ENGINE_CACHE.update({
                "net": net,
                "labels": [str(x) for x in labels],
                "ready": True,
                "backend": "opencv-imagenet-onnx",
                "reason": f"modèle chargé depuis: {active_root if active_root else _THEME_MODEL_DIR}",
            })
        except Exception as e:
            _THEME_ENGINE_CACHE.update({
                "ready": False,
                "backend": "none",
                "reason": f"chargement modèle impossible: {e}",
            })

        return _THEME_ENGINE_CACHE


def get_theme_engine_status() -> dict[str, str]:
    state = _ensure_engine_loaded()
    return {
        "backend": str(state.get("backend") or "none"),
        "ready": "yes" if bool(state.get("ready")) else "no",
        "reason": str(state.get("reason") or ""),
    }


def classify_themes_from_image(filepath: Path, theme_keys: list[str], topk: int = 5) -> dict[str, float]:
    result: dict[str, float] = {str(k): 0.0 for k in theme_keys}
    state = _ensure_engine_loaded()
    if not bool(state.get("ready")):
        return result

    try:
        import cv2
        import numpy as np
    except Exception:
        return result

    net = state.get("net")
    labels = list(state.get("labels") or [])
    if net is None or not labels:
        return result

    if not filepath.exists():
        return result

    try:
        # Lecture robuste pour Windows (UTF-8)
        import numpy as np
        img_array = np.fromfile(str(filepath), np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            logger.debug("Classification: échec décodage image %s", filepath)
            return result

        # Prétraitement standard ImageNet (style torchvision).
        blob = cv2.dnn.blobFromImage(
            image=img,
            scalefactor=1.0 / 255.0,
            size=(224, 224),
            mean=(0.485, 0.456, 0.406),
            swapRB=True,
            crop=True,
        )
        # Adaptation std (approximation simple compatible ONNX de nombreux modèles).
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape((1, 3, 1, 1))
        blob = blob / std

        net.setInput(blob)
        logits = net.forward()
        scores = np.array(logits).reshape(-1)
        if scores.size <= 0:
            return result

        # Softmax
        exp_scores = np.exp(scores - np.max(scores))
        probs = exp_scores / np.sum(exp_scores)
        indices = np.argsort(probs)[-max(1, int(topk)):][::-1]

        for idx in indices:
            label = _normalize_label(labels[int(idx)] if int(idx) < len(labels) else "")
            if not label:
                continue
            conf = float(probs[int(idx)])
            for theme_key in theme_keys:
                keywords = _THEME_IMAGENET_KEYWORDS.get(str(theme_key), [])
                if any(k in label for k in keywords):
                    result[str(theme_key)] = max(result[str(theme_key)], conf)
    except Exception as e:
        logger.debug("Classification image impossible (%s): %s", filepath, e)

    return result
