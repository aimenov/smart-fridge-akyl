from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from backend.app.config import settings
from backend.app.models.entities import DateType
from backend.app.modules import date_parse
from backend.app.modules.inventory_service import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from backend.app.observability import trace_prefix

logger = logging.getLogger(__name__)


def _try_paddle_ocr(images: list[np.ndarray]) -> tuple[str, float, float]:
    """
    Returns (text, mean_line_confidence, wall_time_seconds).
    When PaddleOCR is not installed, returns ("", 0.0, 0.0) quickly.
    """
    t0 = time.perf_counter()
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return "", 0.0, time.perf_counter() - t0
    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    except Exception:
        return "", 0.0, time.perf_counter() - t0
    chunks: list[str] = []
    confidences: list[float] = []
    try:
        for img in images:
            result = ocr.ocr(img, cls=True)
            if not result or not result[0]:
                continue
            for line in result[0]:
                text = line[1][0]
                conf = float(line[1][1])
                chunks.append(text)
                confidences.append(conf)
    except Exception:
        return "\n".join(chunks), 0.0, time.perf_counter() - t0
    text = "\n".join(chunks)
    avg = sum(confidences) / len(confidences) if confidences else 0.0
    return text, avg, time.perf_counter() - t0


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


def _preprocess_variants(bgr: np.ndarray) -> list[np.ndarray]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    variants = [gray]
    variants.append(cv2.convertScaleAbs(gray, alpha=1.35, beta=12))
    sharpen = cv2.filter2D(
        gray,
        -1,
        np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32),
    )
    variants.append(sharpen)
    return variants


def _tiles(gray: np.ndarray, rows: int = 2, cols: int = 2) -> list[np.ndarray]:
    h, w = gray.shape[:2]
    tiles: list[np.ndarray] = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * h // rows, (r + 1) * h // rows
            x0, x1 = c * w // cols, (c + 1) * w // cols
            tiles.append(gray[y0:y1, x0:x1])
    return tiles


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
    all_ocr_chunks: list[str] = []
    paddle_conf = 0.0

    t_load0 = time.perf_counter()
    images_bgr = [cv2.imread(str(p)) for p in image_paths]
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

    t_prep0 = time.perf_counter()
    ocr_inputs: list[np.ndarray] = []
    for im in images_bgr:
        ocr_inputs.extend(_preprocess_variants(im))
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        ocr_inputs.extend(_tiles(gray))
    timing_ms["preprocess_tiles"] = (time.perf_counter() - t_prep0) * 1000.0

    paddle_text, paddle_conf, paddle_wall_s = _try_paddle_ocr(ocr_inputs)
    timing_ms["paddle_ocr"] = paddle_wall_s * 1000.0
    all_ocr_chunks.append(paddle_text)

    combined = "\n".join(t for t in all_ocr_chunks if t).strip()
    stages["ocr_engine"] = "paddleocr" if paddle_conf > 0 else "none"
    if paddle_conf > 0:
        logger.info(
            "%sPaddleOCR: %d line(s) read, mean_conf=%.3f, imgs=%d",
            trace_prefix(),
            len(combined.splitlines()),
            paddle_conf,
            len(ocr_inputs),
        )

    lines = [ln.strip() for ln in combined.splitlines() if len(ln.strip()) > 2]
    product_guess = lines[0][:120] if lines else None

    t_parse0 = time.perf_counter()
    parsed_list = date_parse.parse_dates_from_text(combined)
    timing_ms["date_parse_heuristic"] = (time.perf_counter() - t_parse0) * 1000.0
    stages["parsed_dates"] = [(d.isoformat(), c, s) for d, c, s in parsed_list]

    best_date: Optional[date] = None
    date_conf = 0.0
    raw_snip: Optional[str] = None
    if parsed_list:
        best_date, date_conf, raw_snip = parsed_list[0]

    date_type = date_parse.infer_date_type_from_context(combined)
    if date_type == DateType.unknown and raw_snip:
        date_type = date_parse.infer_date_type_from_context(raw_snip)

    norm = best_date.isoformat() if best_date else None

    conf = date_conf * (0.55 + 0.45 * min(paddle_conf or 0.5, 1.0))
    if barcode and best_date:
        conf = min(1.0, conf + 0.05)

    stages["confidence_breakdown"] = {"date_parse": date_conf, "paddle_avg": paddle_conf}

    tier = "low"
    if conf >= CONFIDENCE_HIGH:
        tier = "high"
    elif conf >= CONFIDENCE_MEDIUM:
        tier = "medium"
    stages["tier"] = tier

    elapsed = time.perf_counter() - t0
    timing_ms["total"] = elapsed * 1000.0
    stages["timing_ms"] = timing_ms

    logger.info(
        "%svision specialist pipeline: total=%.2fs "
        "(load=%.0fms barcode_qr=%.0fms preprocess=%.0fms paddle=%.0fms parse=%.0fms) "
        "images=%d decoded=%d barcodes=%d ocr_chars=%d conf=%.3f tier=%s",
        trace_prefix(),
        elapsed,
        timing_ms["load_images"],
        timing_ms["barcode_qr"],
        timing_ms["preprocess_tiles"],
        timing_ms["paddle_ocr"],
        timing_ms["date_parse_heuristic"],
        len(image_paths),
        len(images_bgr),
        len(barcodes),
        len(combined),
        float(conf),
        tier,
    )

    return PipelineResult(
        barcode=barcode,
        raw_ocr_text=combined,
        date_type=date_type,
        raw_date_text=raw_snip or (parsed_list[0][2] if parsed_list else None),
        normalized_date=norm,
        confidence=float(conf),
        stages=stages,
        product_name_guess=product_guess,
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
