from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from backend.app.config import settings
from backend.app.models.entities import DateType
from backend.app.observability import trace_prefix

logger = logging.getLogger(__name__)


def _load_image_bgr(path: Path) -> Optional[np.ndarray]:
    """Decode image with ``cv2.imdecode`` — ``cv2.imread`` often fails on Unicode paths (Windows)."""
    try:
        raw = Path(path).expanduser().resolve().read_bytes()
        if not raw:
            return None
        buf = np.frombuffer(raw, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except OSError:
        return None


def _decode_barcodes(bgr: np.ndarray) -> list[str]:
    try:
        detector = cv2.barcode.BarcodeDetector()
        ok, infos, corners, straight = detector.detectAndDecode(bgr)
        codes: list[str] = []
        if ok and infos is not None:
            for info in infos:
                if isinstance(info, str) and info:
                    codes.append(info)
        return codes
    except Exception:
        return []


def _decode_qr(bgr: np.ndarray) -> list[str]:
    try:
        det = cv2.QRCodeDetector()
        ok, decoded, _, _ = det.detectAndDecodeMulti(bgr)
        if ok and decoded:
            return [d for d in decoded if d]
    except Exception:
        pass
    return []


@dataclass
class PipelineResult:
    barcode: Optional[str]
    raw_ocr_text: str
    date_type: DateType
    raw_date_text: Optional[str]
    normalized_date: Optional[str]
    confidence: float
    stages: dict[str, Any] = field(default_factory=dict)
    product_name_guess: Optional[str] = None


def run_pipeline(image_paths: list[Path]) -> PipelineResult:
    t0 = time.perf_counter()
    stages: dict[str, Any] = {}
    timing_ms: dict[str, float] = {}
    barcodes: list[str] = []

    t_load0 = time.perf_counter()
    images_bgr = [_load_image_bgr(Path(p)) for p in image_paths]
    images_bgr = [im for im in images_bgr if im is not None]
    timing_ms["load_images"] = (time.perf_counter() - t_load0) * 1000.0
    if not images_bgr:
        logger.warning("%sno decodable images from paths %s", trace_prefix(), image_paths)
        return PipelineResult(
            barcode=None,
            raw_ocr_text="",
            date_type=DateType.unknown,
            raw_date_text=None,
            normalized_date=None,
            confidence=0.0,
            stages={"error": "no_images_loaded"},
        )

    t_bc0 = time.perf_counter()
    for im in images_bgr:
        barcodes.extend(_decode_barcodes(im))
        barcodes.extend(_decode_qr(im))
    timing_ms["barcode_qr"] = (time.perf_counter() - t_bc0) * 1000.0

    barcode = barcodes[0] if barcodes else None
    stages["barcodes"] = barcodes
    # OCR is intentionally stubbed out in this repo cleanup. Keep a stable response shape
    # for the client, but return empty OCR fields.
    timing_ms["ocr_ms"] = 0.0
    stages["ocr_engine"] = "absent"
    stages["tier"] = "high" if barcode else "low"
    conf = 0.90 if barcode else 0.0

    elapsed = time.perf_counter() - t0
    timing_ms["total"] = elapsed * 1000.0
    stages["timing_ms"] = timing_ms

    logger.info(
        "%svision pipeline: total=%.2fs conf=%.3f tier=%s barcode=%s",
        trace_prefix(),
        elapsed,
        float(conf),
        stages["tier"],
        barcode,
    )

    return PipelineResult(
        barcode=barcode,
        raw_ocr_text="",
        date_type=DateType.unknown,
        raw_date_text=None,
        normalized_date=None,
        confidence=float(conf),
        stages=stages,
        product_name_guess=None,
    )


def persist_frames(files: list[tuple[str, bytes]]) -> list[Path]:
    settings.scan_storage.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for _, data in files:
        name = f"{uuid.uuid4().hex}.jpg"
        path = settings.scan_storage / name
        path.write_bytes(data)
        paths.append(path)
    return paths
