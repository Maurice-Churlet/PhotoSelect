# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

"""Face detection helpers for low-priority background indexing - OpenCV Exclusive Version."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import logging
from PIL import Image, ImageOps

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None

try:
    import face_recognition as fr  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    fr = None

try:
    import numpy as np
except Exception:  # pragma: no cover - optional runtime dependency
    np = None


logger = logging.getLogger(__name__)


FACE_ENGINE_OPENCV = "opencv"
_OPENCV_MAX_SIDE = 640


def _ensure_numpy() -> bool:
    global np
    if np is not None:
        return True
    try:
        import numpy as _np  # type: ignore
        np = _np
        return True
    except Exception:
        return False


def _ensure_opencv() -> bool:
    global cv2
    if cv2 is not None:
        return True
    try:
        import cv2 as _cv2  # type: ignore
        cv2 = _cv2
        return True
    except Exception as exc:
        logger.debug("opencv import failed: %s", exc)
        return False


def _ensure_face_recognition() -> bool:
    global fr
    if fr is not None:
        return True
    try:
        import face_recognition as _fr  # type: ignore
        fr = _fr
        return True
    except Exception as exc:
        logger.debug("face_recognition import failed: %s", exc)
        return False


def get_supported_backends() -> list[str]:
    return [FACE_ENGINE_OPENCV]


def normalize_backend(backend: str | None) -> str:
    return FACE_ENGINE_OPENCV


def is_available(backend: str | None = None) -> bool:
    """Return True when requested backend is usable in current runtime."""
    return _ensure_opencv() and _ensure_numpy()


def get_unavailability_reason(backend: str | None = None) -> str:
    """Return a human-readable reason when a backend is unavailable."""
    if not _ensure_numpy():
        return "numpy indisponible"
    if not _ensure_opencv():
        return "package opencv indisponible"
    return ""


def _detect_faces_opencv(img: Image.Image) -> list[dict[str, Any]]:
    if not _ensure_opencv() or not _ensure_numpy() or cv2 is None or np is None:
        return []

    rgb = np.asarray(img)
    if rgb.size == 0:
        return []

    h, w = rgb.shape[:2]
    if h <= 1 or w <= 1:
        return []

    # Cap resolution for predictable latency on large photos.
    max_side = max(h, w)
    scale = 1.0
    resized = rgb
    if max_side > _OPENCV_MAX_SIDE:
        scale = float(_OPENCV_MAX_SIDE) / float(max_side)
        resized = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        logger.warning("OpenCV cascade unavailable at %s", cascade_path)
        return []

    min_dim = max(24, int(min(gray.shape[0], gray.shape[1]) * 0.045))
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.10,
        minNeighbors=8,
        minSize=(min_dim, min_dim),
    )

    eye_detector = None
    try:
        eye_path = cv2.data.haarcascades + "haarcascade_eye.xml"
        eye_detector = cv2.CascadeClassifier(eye_path)
        if eye_detector.empty():
            eye_detector = None
    except Exception:
        eye_detector = None

    detections: list[dict[str, Any]] = []
    for (x, y, bw, bh) in faces:
        if bw <= 0 or bh <= 0:
            continue
        ratio = float(bw) / float(max(1, bh))
        if ratio < 0.60 or ratio > 1.60:
            continue

        face_area_ratio = float(bw * bh) / float(max(1, gray.shape[0] * gray.shape[1]))
        if face_area_ratio < 0.002:
            continue

        if eye_detector is not None:
            y_eye_end = y + int(bh * 0.68)
            roi = gray[max(0, y):max(0, y_eye_end), max(0, x):max(0, x + bw)]
            if roi.size == 0:
                continue
            eyes = eye_detector.detectMultiScale(
                roi,
                scaleFactor=1.10,
                minNeighbors=3,
                minSize=(max(8, bw // 10), max(8, bh // 10)),
            )
            if len(eyes) < 1:
                continue

        x1 = int(round(float(x) / scale))
        y1 = int(round(float(y) / scale))
        x2 = int(round(float(x + bw) / scale))
        y2 = int(round(float(y + bh) / scale))

        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(x1 + 1, min(w, x2))
        y2 = max(y1 + 1, min(h, y2))

        crop = img.crop((x1, y1, x2, y2)).convert("RGB")
        crop.thumbnail((192, 192), Image.Resampling.LANCZOS)
        embedding = _compute_embedding(crop)

        detections.append(
            {
                "confidence": 0.85,
                "bbox_left": float(x1) / float(w),
                "bbox_top": float(y1) / float(h),
                "bbox_width": float(x2 - x1) / float(w),
                "bbox_height": float(y2 - y1) / float(h),
                "thumb_image": crop,
                "embedding": embedding,
            }
        )
    return detections


def _compute_embedding(face_img: Image.Image) -> list[float]:
    """Compute embedding with face_recognition when available, fallback to histograms."""
    if _ensure_face_recognition() and _ensure_numpy() and fr is not None and np is not None:
        try:
            rgb = np.asarray(face_img.convert("RGB"))
            if rgb.size > 0:
                encodings = fr.face_encodings(
                    rgb,
                    num_jitters=1,
                    model="small",
                )
                if encodings:
                    return [float(v) for v in encodings[0].tolist()]
        except Exception:
            pass

    # Fallback deterministic léger si face_recognition n'est pas disponible.
    img = face_img.convert("RGB").resize((48, 48), Image.Resampling.BILINEAR)
    hist = img.histogram()  # 256 bins per channel
    bins_per_channel = 8
    block_size = 256 // bins_per_channel

    vector: list[float] = []
    for channel in range(3):
        offset = channel * 256
        channel_bins: list[float] = []
        for i in range(bins_per_channel):
            start = offset + (i * block_size)
            end = start + block_size
            channel_bins.append(float(sum(hist[start:end])))
        channel_sum = sum(channel_bins) or 1.0
        vector.extend(v / channel_sum for v in channel_bins)

    w, h = img.size
    ratio = float(w) / float(max(1, h))
    vector.append(ratio)
    return vector


def detect_faces_from_image(
    image_path: Path,
    min_confidence: float | None = None,
    backend: str | None = None,
) -> list[dict[str, Any]]:
    """Detect faces using OpenCV (Exclusive backend)."""
    if not image_path.exists() or not image_path.is_file():
        return []

    try:
        with Image.open(image_path) as opened:
            img = ImageOps.exif_transpose(opened.copy()).convert("RGB")
    except Exception:
        return []

    return _detect_faces_opencv(img)
