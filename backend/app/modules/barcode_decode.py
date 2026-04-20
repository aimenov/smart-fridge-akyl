from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from backend.app.modules.barcode_gtin import GtinNormalization, normalize_barcode_to_gtin14

logger = logging.getLogger(__name__)


def _polygon_area(points: np.ndarray) -> float:
    if points is None or len(points) < 4:
        return 0.0
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    return float(cv2.contourArea(pts))


def _polygon_centroid(points: np.ndarray) -> tuple[float, float]:
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(pts) == 0:
        return 0.0, 0.0
    return float(pts[:, 0].mean()), float(pts[:, 1].mean())


def _symbology_rank(sym: str) -> float:
    s = (sym or "").upper().replace("-", "_")
    order = (
        "EAN_13",
        "UPC_A",
        "UPC_E",
        "EAN_8",
        "DATAMATRIX",
        "CODE_128",
        "CODE39",
        "ITF",
    )
    try:
        idx = order.index(s)
        return 100.0 - idx * 5.0
    except ValueError:
        return 40.0


def _order_quad_tl_tr_br_bl(pts: np.ndarray) -> np.ndarray:
    """Sort four corner points to (tl, tr, br, bl) for curved / skewed labels."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1).flatten()
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _warp_quad_to_rect(bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    rect = _order_quad_tl_tr_br_bl(quad)
    tl, tr, br, bl = rect
    width_a = float(np.linalg.norm(br - bl))
    width_b = float(np.linalg.norm(tr - tl))
    max_width = max(int(width_a), int(width_b), 2)
    height_a = float(np.linalg.norm(tr - br))
    height_b = float(np.linalg.norm(tl - bl))
    max_height = max(int(height_a), int(height_b), 2)

    dst = np.array(
        [[0.0, 0.0], [max_width - 1.0, 0.0], [max_width - 1.0, max_height - 1.0], [0.0, max_height - 1.0]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(bgr, M, (max_width, max_height))


def _score_candidate(
    *,
    info: str,
    sym: str,
    points: np.ndarray | None,
    img_shape: tuple[int, ...],
    gtin: GtinNormalization,
) -> float:
    h, w = img_shape[:2]
    cx, cy = w / 2.0, h / 2.0
    px, py = _polygon_centroid(points) if points is not None else (cx, cy)
    dist = math.hypot(px - cx, py - cy)
    max_dist = math.hypot(w, h) / 2.0 or 1.0
    center_score = (1.0 - min(dist / max_dist, 1.0)) * 50.0

    area = _polygon_area(points) if points is not None else 0.0
    img_area = float(max(w * h, 1))
    area_ratio = min(area / img_area, 1.0)
    area_score = area_ratio * 40.0

    sym_score = _symbology_rank(sym)
    check_score = 25.0 if gtin.valid_check_digit else 0.0

    return center_score + area_score + sym_score + check_score


@dataclass(slots=True)
class BarcodeCandidate:
    raw_text: str
    symbology: str
    normalized_gtin_14: str | None
    valid_check_digit: bool
    score: float
    preprocess: str


def _decode_with_type_on_image(bgr: np.ndarray, preprocess: str) -> list[BarcodeCandidate]:
    detector = cv2.barcode.BarcodeDetector()
    ok, infos, types, points = detector.detectAndDecodeWithType(bgr)
    if not ok or infos is None or len(infos) == 0:
        return []

    out: list[BarcodeCandidate] = []
    infos_list = list(infos)
    types_list = list(types) if types is not None else ["UNKNOWN"] * len(infos_list)
    for i, info in enumerate(infos_list):
        if not isinstance(info, str) or not info:
            continue
        sym = types_list[i] if i < len(types_list) else "UNKNOWN"
        pts = None
        if points is not None:
            pa = np.asarray(points)
            if pa.ndim == 3 and len(pa) > i:
                pts = pa[i]
            elif pa.ndim == 2 and pa.shape[0] >= 4 and i == 0:
                pts = pa
        gtin = normalize_barcode_to_gtin14(info)
        sc = _score_candidate(
            info=info,
            sym=str(sym),
            points=pts,
            img_shape=bgr.shape,
            gtin=gtin,
        )
        out.append(
            BarcodeCandidate(
                raw_text=info,
                symbology=str(sym),
                normalized_gtin_14=gtin.normalized_gtin_14,
                valid_check_digit=gtin.valid_check_digit,
                score=sc,
                preprocess=preprocess,
            )
        )
    return out


def _maybe_upscale_barcode_roi(bgr: np.ndarray, *, min_long_edge: int = 360) -> np.ndarray:
    h, w = bgr.shape[:2]
    long_edge = max(w, h)
    if long_edge >= min_long_edge:
        return bgr
    scale = min_long_edge / float(long_edge)
    nw = max(2, int(w * scale))
    nh = max(2, int(h * scale))
    return cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_CUBIC)


def _decode_from_detected_quads(source_bgr: np.ndarray, preprocess: str) -> list[BarcodeCandidate]:
    """
    Curved cans / skewed labels: ``detectMulti`` quadrangles → perspective-unwrap → decode.
    Uses the same grayscale detector input OpenCV expects for detection.
    """
    gray = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY)
    detector = cv2.barcode.BarcodeDetector()
    ok, pts = detector.detectMulti(gray)
    if not ok or pts is None:
        return []

    pa = np.asarray(pts)
    if pa.ndim == 2:
        pa = pa.reshape(1, 4, 2)

    seen: dict[str, BarcodeCandidate] = {}
    for qi in range(len(pa)):
        quad = pa[qi]
        try:
            warped = _warp_quad_to_rect(source_bgr, quad)
        except cv2.error:
            logger.debug("barcode warp skipped (cv2.error) preprocess=%s quad=%s", preprocess, qi)
            continue

        warped = _maybe_upscale_barcode_roi(warped)
        for cand in _decode_with_type_on_image(warped, preprocess=f"{preprocess}_warp{qi}"):
            prev = seen.get(cand.raw_text)
            if prev is None or cand.score > prev.score:
                seen[cand.raw_text] = cand

    out = list(seen.values())
    if out:
        logger.debug(
            "barcode quad unwrap preprocess=%s regions=%s decoded=%s",
            preprocess,
            len(pa),
            [c.raw_text for c in out],
        )
    return out


def _preprocessed_bgr_variants(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ath = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        2,
    )

    def maybe_sharp(g: np.ndarray) -> np.ndarray:
        blur = cv2.GaussianBlur(g, (0, 0), sigmaX=3.0)
        sharp = cv2.addWeighted(g, 1.5, blur, -0.5, 0)
        return np.clip(sharp, 0, 255).astype(np.uint8)

    def to_bgr(x: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(x, cv2.COLOR_GRAY2BGR)

    variants: list[tuple[str, np.ndarray]] = []
    for label, plane in ("gray", gray), ("gray_sharp", maybe_sharp(gray)), ("ath", ath), (
        "ath_sharp",
        maybe_sharp(ath),
    ):
        for scale_name, scale in ("1x", 1.0), ("1.5x", 1.5), ("2x", 2.0):
            if scale != 1.0:
                h, w = plane.shape[:2]
                resized = cv2.resize(
                    plane,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_CUBIC,
                )
            else:
                resized = plane
            variants.append((f"{label}_{scale_name}", to_bgr(resized)))
    return variants


def _prefer_candidate(new: BarcodeCandidate, old: BarcodeCandidate) -> bool:
    if new.valid_check_digit != old.valid_check_digit:
        return new.valid_check_digit
    return new.score > old.score


def decode_barcodes_best(bgr: np.ndarray) -> tuple[list[BarcodeCandidate], list[dict[str, Any]]]:
    """
    Full-frame decode + perspective-unwrapped regions from ``detectMulti`` + preprocessed planes.
    Candidates deduped by ``raw_text``; prefers valid GS1 check digit, then composite score.
    """
    all_candidates: dict[str, BarcodeCandidate] = {}

    def ingest(cands: list[BarcodeCandidate]) -> None:
        for c in cands:
            prev = all_candidates.get(c.raw_text)
            if prev is None or _prefer_candidate(c, prev):
                all_candidates[c.raw_text] = c

    ingest(_decode_with_type_on_image(bgr, preprocess="native"))
    ingest(_decode_from_detected_quads(bgr, preprocess="native"))

    gray0 = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ath0 = cv2.adaptiveThreshold(gray0, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)
    ath_bgr = cv2.cvtColor(ath0, cv2.COLOR_GRAY2BGR)
    ingest(_decode_from_detected_quads(ath_bgr, preprocess="ath"))

    for tag, variant in _preprocessed_bgr_variants(bgr):
        ingest(_decode_with_type_on_image(variant, preprocess=tag))

    ranked = sorted(
        all_candidates.values(),
        key=lambda x: (not x.valid_check_digit, -x.score),
    )
    debug = [
        {
            "raw": c.raw_text,
            "symbology": c.symbology,
            "gtin14": c.normalized_gtin_14,
            "check_ok": c.valid_check_digit,
            "score": round(c.score, 3),
            "preprocess": c.preprocess,
        }
        for c in ranked
    ]
    logger.debug("barcode decode summary: %s distinct raw strings", len(ranked))
    return ranked, debug
