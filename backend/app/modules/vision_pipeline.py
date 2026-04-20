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
from backend.app.modules.barcode_decode import BarcodeCandidate, decode_barcodes_best
from backend.app.modules.barcode_gtin import normalize_barcode_to_gtin14
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
    #: Canonical GTIN-14 when checksum-valid; else best-effort raw decode.
    barcode: Optional[str]
    barcode_raw: Optional[str]
    barcode_symbology: Optional[str]
    normalized_gtin_14: Optional[str]
    raw_ocr_text: str
    date_type: DateType
    raw_date_text: Optional[str]
    normalized_date: Optional[str]
    confidence: float
    stages: dict[str, Any] = field(default_factory=dict)
    product_name_guess: Optional[str] = None


def _pick_barcode_from_candidates(
    ranked: list[BarcodeCandidate], qr_strings: list[str]
) -> tuple[Optional[BarcodeCandidate], Optional[str]]:
    """Prefer linear retail symbologies; fall back to QR only if no barcode."""
    if ranked:
        best = ranked[0]
        return best, None
    if qr_strings:
        return None, qr_strings[0]
    return None, None


def _human_barcode_for_ui(normalized_gtin14: str | None, raw_barcode: str | None) -> str | None:
    """
    UI-friendly barcode string.

    - If we have a GTIN-14 with a leading indicator digit of ``0``, show the common GTIN-13 form.
    - Otherwise show the canonical GTIN-14 (preferred) or raw text.
    """
    if normalized_gtin14 and len(normalized_gtin14) == 14 and normalized_gtin14.isdigit():
        if normalized_gtin14[0] == "0":
            return normalized_gtin14[1:]
        return normalized_gtin14
    if raw_barcode and str(raw_barcode).strip():
        return str(raw_barcode).strip()
    return None


def _pick_barcode_consensus(
    per_frame_ranked: list[list[BarcodeCandidate]],
) -> tuple[Optional[BarcodeCandidate], dict[str, Any]]:
    """
    Live scanning reliability: pick a barcode only when it is stable across frames.

    Strategy:
    - For each frame, take its best candidate (already ranked by decoder rules).
    - Vote by checksum-valid ``normalized_gtin_14`` only (avoids locking onto partial/false reads).
    - Require either:
      - >=2 matching votes and >=60% of frames, OR
      - single-frame case: valid checksum and strong score.
    """
    frames_total = max(1, len(per_frame_ranked))
    best_per_frame: list[BarcodeCandidate] = [r[0] for r in per_frame_ranked if r]
    frames_with_any_candidate = max(1, len(best_per_frame))

    votes: dict[str, int] = {}
    for c in best_per_frame:
        if c.valid_check_digit and c.normalized_gtin_14:
            votes[c.normalized_gtin_14] = votes.get(c.normalized_gtin_14, 0) + 1

    debug: dict[str, Any] = {
        "frames": frames_total,
        "frames_with_any_candidate": len(best_per_frame),
        "best_per_frame": [
            {
                "raw": c.raw_text,
                "symbology": c.symbology,
                "gtin14": c.normalized_gtin_14,
                "check_ok": c.valid_check_digit,
                "score": round(float(c.score), 3),
                "preprocess": c.preprocess,
            }
            for c in best_per_frame[:12]
        ],
        "votes_gtin14": votes,
    }

    if not best_per_frame:
        return None, debug

    # If we only have one frame, accept only high-quality checksum-valid reads.
    if len(best_per_frame) == 1:
        c = best_per_frame[0]
        ok = bool(c.valid_check_digit and c.normalized_gtin_14 and c.score >= 120.0)
        debug["consensus_rule"] = "single_frame_strict"
        debug["accepted"] = ok
        return (c if ok else None), debug

    # Multi-frame vote.
    if not votes:
        debug["consensus_rule"] = "no_valid_votes"
        debug["accepted"] = False
        return None, debug

    winner_gtin14, winner_votes = max(votes.items(), key=lambda kv: kv[1])
    # Use frames that produced *any* candidate as denominator; otherwise a single blurry frame
    # can dilute the ratio and prevent lock even when the barcode is consistently decoded.
    ratio = winner_votes / float(frames_with_any_candidate or 1)
    ok = bool(winner_votes >= 2 and ratio >= 0.60)
    debug["consensus_rule"] = "vote_gtin14"
    debug["winner_gtin14"] = winner_gtin14
    debug["winner_votes"] = winner_votes
    debug["winner_ratio"] = round(ratio, 3)
    debug["accepted"] = ok

    if not ok:
        return None, debug

    # Return the best candidate among those that match the winner GTIN-14.
    matching = [
        c
        for c in best_per_frame
        if c.normalized_gtin_14 == winner_gtin14 and c.valid_check_digit
    ]
    matching.sort(key=lambda c: -c.score)
    return (matching[0] if matching else None), debug


def run_pipeline(image_paths: list[Path]) -> PipelineResult:
    t0 = time.perf_counter()
    stages: dict[str, Any] = {}
    timing_ms: dict[str, float] = {}

    t_load0 = time.perf_counter()
    images_bgr = [_load_image_bgr(Path(p)) for p in image_paths]
    images_bgr = [im for im in images_bgr if im is not None]
    timing_ms["load_images"] = (time.perf_counter() - t_load0) * 1000.0
    if not images_bgr:
        logger.warning("vision: no decodable images from paths %s", image_paths)
        return PipelineResult(
            barcode=None,
            barcode_raw=None,
            barcode_symbology=None,
            normalized_gtin_14=None,
            raw_ocr_text="",
            date_type=DateType.unknown,
            raw_date_text=None,
            normalized_date=None,
            confidence=0.0,
            stages={"error": "no_images_loaded"},
        )

    t_bc0 = time.perf_counter()
    per_frame_ranked: list[list[BarcodeCandidate]] = []
    debug_lists: list[list[dict[str, Any]]] = []
    for im in images_bgr:
        ranked, dbg = decode_barcodes_best(im)
        per_frame_ranked.append(ranked)
        debug_lists.append(dbg)

    all_ranked: list[BarcodeCandidate] = [c for r in per_frame_ranked for c in r]
    all_ranked.sort(key=lambda c: (not c.valid_check_digit, -c.score))
    qr_strings: list[str] = []
    for im in images_bgr:
        qr_strings.extend(_decode_qr(im))

    cand_consensus, consensus_dbg = _pick_barcode_consensus(per_frame_ranked)
    # Important: do NOT surface a "best guess" barcode when consensus rejected it.
    # Wrong barcodes are worse than "no barcode yet" for live scanning.
    cand, qr_fallback = (cand_consensus, None) if cand_consensus else (None, None)
    timing_ms["barcode_qr"] = (time.perf_counter() - t_bc0) * 1000.0

    barcode_raw = cand.raw_text if cand else None
    symbology = cand.symbology if cand else None
    normalized = cand.normalized_gtin_14 if cand else None

    display_barcode = _human_barcode_for_ui(normalized, barcode_raw) if cand_consensus else None

    stages["barcode_candidates"] = [d for batch in debug_lists for d in batch]
    stages["barcodes_decoded"] = [c.raw_text for c in all_ranked[:12]]
    stages["qr_codes"] = qr_strings
    stages["barcode_consensus"] = consensus_dbg

    timing_ms["ocr_ms"] = 0.0
    stages["ocr_engine"] = "absent"
    stages["tier"] = "high" if display_barcode else "low"
    conf = 0.93 if display_barcode else 0.0

    elapsed = time.perf_counter() - t0
    timing_ms["total"] = elapsed * 1000.0
    stages["timing_ms"] = timing_ms

    logger.debug(
        "vision pipeline: total=%.2fs conf=%.3f tier=%s barcode=%s sym=%s gtin14=%s",
        elapsed,
        float(conf),
        stages["tier"],
        display_barcode,
        symbology,
        normalized,
    )

    return PipelineResult(
        barcode=display_barcode,
        barcode_raw=barcode_raw,
        barcode_symbology=symbology,
        normalized_gtin_14=normalized,
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
