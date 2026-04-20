from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from backend.app.models.entities import DateType

logger = logging.getLogger(__name__)


_RE_NUM_DATE = re.compile(
    r"(?P<a>\d{1,4})\s*[-./]\s*(?P<b>\d{1,2})\s*[-./]\s*(?P<c>\d{1,4})"
)


def _try_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _parse_numeric_date(token: str) -> Optional[date]:
    """
    Parse common numeric formats:
    - dd.mm.yyyy / dd.mm.yy
    - yyyy-mm-dd
    - dd-mm-yy
    """
    m = _RE_NUM_DATE.search(token)
    if not m:
        return None
    a = int(m.group("a"))
    b = int(m.group("b"))
    c = int(m.group("c"))

    # If one side is clearly a year, use it.
    if a >= 1900 and a <= 2099:
        y, mm, dd = a, b, c
        return _try_date(y, mm, dd)
    if c >= 1900 and c <= 2099:
        y, mm, dd = c, b, a
        return _try_date(y, mm, dd)

    # Two-digit year: assume 20xx.
    if c < 100:
        y = 2000 + c
        mm, dd = b, a
        return _try_date(y, mm, dd)
    if a < 100 and c >= 1:
        # yy-mm-dd (rare)
        y = 2000 + a
        mm, dd = b, c
        return _try_date(y, mm, dd)

    # Ambiguous 3-part number: best-effort dd-mm-yyish.
    return _try_date(c, b, a)


def _center_y(box: np.ndarray) -> float:
    pts = np.asarray(box, dtype=np.float32).reshape(-1, 2)
    if pts.size == 0:
        return 0.0
    return float(np.mean(pts[:, 1]))


def _prep_variants(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Fast OpenCV-only preprocessing variants to help dotted-matrix print."""
    out: list[tuple[str, np.ndarray]] = []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    def add(tag: str, g: np.ndarray) -> None:
        out.append((tag, g))

    add("gray", gray)

    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    add("clahe", clahe.apply(gray))

    # Sharpen helps dot-matrix edges.
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0)
    sharp = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)
    add("sharp", np.clip(sharp, 0, 255).astype(np.uint8))

    # Adaptive threshold for low-contrast backgrounds.
    ath = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)
    add("ath", ath)

    # Inverted threshold sometimes works better for OCR engines.
    add("ath_inv", 255 - ath)

    # Upscale small crops (keeps this bounded so it's still quick).
    scaled: list[tuple[str, np.ndarray]] = []
    for tag, g in out:
        h, w = g.shape[:2]
        if max(h, w) < 900:
            scaled.append((f"{tag}_2x", cv2.resize(g, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)))
    out.extend(scaled)
    return out


@dataclass(slots=True)
class ExpiryDetection:
    date_type: DateType
    raw_text: str | None
    normalized_date: str | None
    confidence: float
    stages: dict[str, Any]


_OCR = None


def _get_ocr() -> RapidOCR:
    global _OCR
    if _OCR is None:
        # Instantiate once per process for speed.
        _OCR = RapidOCR()
    return _OCR


def detect_expiry_date(bgr: np.ndarray) -> ExpiryDetection:
    """
    Detect a date in a user-cropped expiry area quickly.

    We favor candidates *lower in the image* when multiple dates are present
    (common label layout: produced on top, expiry/use-by below).
    """
    ocr = _get_ocr()
    variants = _prep_variants(bgr)
    year_now = date.today().year

    candidates: list[dict[str, Any]] = []
    raw_hits: list[dict[str, Any]] = []

    for tag, g in variants:
        try:
            # RapidOCR expects RGB or BGR; grayscale works too but we pass 3ch for consistency.
            if g.ndim == 2:
                img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            else:
                img = g
            res, _ = ocr(img)
        except Exception as e:
            logger.debug("expiry OCR variant failed tag=%s err=%s", tag, e)
            continue

        if not res:
            continue

        for det in res:
            # det: [box, text, score]
            if not isinstance(det, (list, tuple)) or len(det) < 3:
                continue
            box, text, score = det[0], det[1], det[2]
            if not isinstance(text, str):
                continue
            t = text.strip()
            if len(t) < 4:
                continue
            try:
                sc = float(score)
            except Exception:
                sc = 0.0

            y = _center_y(np.asarray(box))
            raw_hits.append({"tag": tag, "text": t, "score": round(sc, 3), "y": round(y, 1)})

            # Extract dates from the text token(s).
            has_time = ":" in t
            for m in _RE_NUM_DATE.finditer(t):
                token = m.group(0)
                dt = _parse_numeric_date(token)
                if not dt:
                    continue
                # Prefer lower y and higher OCR confidence.
                year_bias = 18.0 if dt.year >= year_now else -28.0
                # Dotted-matrix often misreads the year; for expiry scans, past years are almost always wrong.
                time_penalty = -22.0 if has_time else 0.0
                cand_score = (
                    sc * 100.0
                    + (y / max(1.0, float(bgr.shape[0]))) * 60.0
                    + year_bias
                    + time_penalty
                )
                candidates.append(
                    {
                        "tag": tag,
                        "raw": token,
                        "iso": dt.isoformat(),
                        "ocr_score": sc,
                        "y": y,
                        "score": cand_score,
                    }
                )

    best = max(candidates, key=lambda c: c["score"]) if candidates else None
    stages = {
        "variants_tried": len(variants),
        "raw_hits_head": raw_hits[:24],
        "candidates": sorted(candidates, key=lambda c: -c["score"])[:12],
    }

    if not best:
        return ExpiryDetection(
            date_type=DateType.unknown,
            raw_text=None,
            normalized_date=None,
            confidence=0.0,
            stages=stages,
        )

    # Confidence: map OCR score (0..1) into something usable, and bump slightly because crop is targeted.
    conf = float(min(0.98, max(0.0, best["ocr_score"] * 0.95 + 0.15)))
    return ExpiryDetection(
        date_type=DateType.expiry,
        raw_text=str(best["raw"]),
        normalized_date=str(best["iso"]),
        confidence=conf,
        stages=stages,
    )

