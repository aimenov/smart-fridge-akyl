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
from backend.app.modules.barcode_decode import (
    BarcodeCandidate,
    barcode_candidate_rank_key,
    decode_barcodes_best,
)
from backend.app.logging_config import get_recognition_logger
from backend.app.modules.barcode_gtin import normalize_barcode_to_gtin14
from backend.app.modules.expiry_date import ExpiryDetection, detect_expiry_date
logger = logging.getLogger(__name__)
recognition_log = get_recognition_logger()


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


def _votes_by_gtin14(
    best_per_frame: list[BarcodeCandidate], *, retail_only: bool
) -> dict[str, int]:
    votes: dict[str, int] = {}
    for c in best_per_frame:
        if not (c.valid_check_digit and c.normalized_gtin_14):
            continue
        sym = (c.symbology or "").upper().replace("-", "_")
        if retail_only and sym == "EAN_8":
            continue
        k = c.normalized_gtin_14
        votes[k] = votes.get(k, 0) + 1
    return votes


def _pick_barcode_consensus(
    per_frame_ranked: list[list[BarcodeCandidate]],
) -> tuple[Optional[BarcodeCandidate], dict[str, Any]]:
    """
    Live scanning reliability: pick a barcode only when it is stable across frames.

    Strategy:
    - For each frame, take its best candidate (already ranked by decoder rules).
    - Vote by checksum-valid ``normalized_gtin_14``. Prefer EAN-13/UPC reads first:
      accidental EAN-8 patches are ignored until no retail-length votes remain.
    - Require either:
      - >=2 matching votes and >=60% of frames-with-any-candidate, OR
      - single-frame case: valid checksum and score >= 155 (blocks ~140 false EAN-8 patches).
    """
    frames_total = max(1, len(per_frame_ranked))
    best_per_frame: list[BarcodeCandidate] = [r[0] for r in per_frame_ranked if r]
    frames_with_any_candidate = max(1, len(best_per_frame))

    votes = _votes_by_gtin14(best_per_frame, retail_only=True)
    vote_mode = "retail_first"
    if not votes:
        votes = _votes_by_gtin14(best_per_frame, retail_only=False)
        vote_mode = "all_symbologies"

    debug: dict[str, Any] = {
        "frames": frames_total,
        "frames_with_any_candidate": len(best_per_frame),
        "vote_mode": vote_mode,
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
        # Require a strong read: marginal false EAN-8 patches often sit ~140–150.
        ok = bool(c.valid_check_digit and c.normalized_gtin_14 and float(c.score) >= 155.0)
        debug["consensus_rule"] = "single_frame_strict"
        debug["vote_mode"] = vote_mode
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
    matching.sort(key=barcode_candidate_rank_key)
    return (matching[0] if matching else None), debug


def _pick_expiry_consensus(
    per_frame: list[tuple[str | None, float, str | None]],
) -> tuple[tuple[str | None, float, str | None], dict[str, Any]]:
    """
    Choose an expiry date only when it's stable across frames.

    Input tuples: (normalized_date_iso, confidence, raw_text).
    Rules:
    - Multi-frame: vote by normalized ISO date; require >=2 votes and >=60% of frames-with-any-date.
    - Single-frame: accept only if confidence >= 0.90.
    """
    frames_total = max(1, len(per_frame))
    frames_with_date = [x for x in per_frame if x[0]]
    denom = max(1, len(frames_with_date))
    votes: dict[str, int] = {}
    best_conf: dict[str, float] = {}
    best_raw: dict[str, str | None] = {}
    for iso, conf, raw in frames_with_date:
        if not iso:
            continue
        votes[iso] = votes.get(iso, 0) + 1
        if conf >= best_conf.get(iso, -1.0):
            best_conf[iso] = conf
            best_raw[iso] = raw

    dbg: dict[str, Any] = {
        "frames": frames_total,
        "frames_with_date": len(frames_with_date),
        "votes": votes,
    }

    if frames_total == 1:
        iso, conf, raw = per_frame[0]
        ok = bool(iso and conf >= 0.90)
        dbg["rule"] = "single_frame_strict"
        dbg["accepted"] = ok
        return (iso, conf, raw) if ok else (None, 0.0, None), dbg

    if not votes:
        dbg["rule"] = "no_votes"
        dbg["accepted"] = False
        return (None, 0.0, None), dbg

    winner, n = max(votes.items(), key=lambda kv: kv[1])
    ratio = n / float(denom or 1)
    ok = bool(n >= 2 and ratio >= 0.60)
    dbg["rule"] = "vote"
    dbg["winner"] = winner
    dbg["winner_votes"] = n
    dbg["winner_ratio"] = round(ratio, 3)
    dbg["accepted"] = ok
    if not ok:
        return (None, 0.0, None), dbg
    return (winner, float(best_conf.get(winner, 0.0)), best_raw.get(winner)), dbg


def run_pipeline(
    image_paths: list[Path],
    *,
    run_barcode: bool = True,
    run_expiry: bool = True,
) -> PipelineResult:
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

    barcode_raw: Optional[str] = None
    symbology: Optional[str] = None
    normalized: Optional[str] = None
    display_barcode: Optional[str] = None
    if run_barcode:
        t_bc0 = time.perf_counter()
        per_frame_ranked: list[list[BarcodeCandidate]] = []
        debug_lists: list[list[dict[str, Any]]] = []
        for im in images_bgr:
            ranked, dbg = decode_barcodes_best(im)
            per_frame_ranked.append(ranked)
            debug_lists.append(dbg)

        all_ranked: list[BarcodeCandidate] = [c for r in per_frame_ranked for c in r]
        all_ranked.sort(key=barcode_candidate_rank_key)
        qr_strings: list[str] = []
        for im in images_bgr:
            qr_strings.extend(_decode_qr(im))

        cand_consensus, consensus_dbg = _pick_barcode_consensus(per_frame_ranked)
        # Important: do NOT surface a "best guess" barcode when consensus rejected it.
        # Wrong barcodes are worse than "no barcode yet" for live scanning.
        cand, _qr_fallback = (cand_consensus, None) if cand_consensus else (None, None)
        timing_ms["barcode_qr"] = (time.perf_counter() - t_bc0) * 1000.0

        barcode_raw = cand.raw_text if cand else None
        symbology = cand.symbology if cand else None
        normalized = cand.normalized_gtin_14 if cand else None
        display_barcode = _human_barcode_for_ui(normalized, barcode_raw) if cand_consensus else None

        stages["barcode_candidates"] = [d for batch in debug_lists for d in batch]
        stages["barcodes_decoded"] = [c.raw_text for c in all_ranked[:12]]
        stages["qr_codes"] = qr_strings
        stages["barcode_consensus"] = consensus_dbg
        recognition_log.info(
            "BARCODE | frames=%d | ok=%s | rule=%s | vote_mode=%s | gtin14=%s | ms=%.1f",
            len(per_frame_ranked),
            bool(consensus_dbg.get("accepted")),
            consensus_dbg.get("consensus_rule"),
            consensus_dbg.get("vote_mode"),
            normalized or "-",
            timing_ms["barcode_qr"],
        )
    else:
        stages["barcode_skipped"] = True

    # Expiry/date OCR (fast; users typically crop the printed date area).
    expiry: ExpiryDetection | None = None
    if run_expiry:
        t_ocr0 = time.perf_counter()
        if len(images_bgr) == 1:
            # Live scan uploads one frame per request: return best single-frame parse,
            # and let the frontend stabilize across repeated requests.
            expiry = detect_expiry_date(images_bgr[0], fast=True)
            stages["expiry"] = expiry.stages
            stages["expiry_consensus"] = {
                "rule": "single_frame",
                "accepted": bool(expiry.normalized_date and float(expiry.confidence) >= 0.60),
                "normalized": expiry.normalized_date,
                "confidence": round(float(expiry.confidence), 3),
            }
        else:
            # Batch uploads (e.g. extracted video frames): OCR only a few frames for latency.
            # Prefer the **last** frames when several are provided — clips often start blurry.
            batch_imgs = images_bgr[-3:] if len(images_bgr) >= 3 else images_bgr
            per_frame_exp: list[tuple[str | None, float, str | None]] = []
            per_frame_dbg: list[dict[str, Any]] = []
            best_det: ExpiryDetection | None = None
            for im in batch_imgs:
                det = detect_expiry_date(im, fast=True)
                per_frame_exp.append((det.normalized_date, float(det.confidence), det.raw_text))
                per_frame_dbg.append(
                    {
                        "normalized": det.normalized_date,
                        "confidence": round(float(det.confidence), 3),
                        "raw": det.raw_text,
                    }
                )
                if det.normalized_date and (
                    best_det is None or float(det.confidence) > float(best_det.confidence)
                ):
                    best_det = det

            (iso, exp_conf, raw_txt), exp_dbg = _pick_expiry_consensus(per_frame_exp)
            stages["expiry_consensus"] = exp_dbg
            stages["expiry_frames"] = per_frame_dbg
            if iso and best_det is not None:
                # Re-run on a representative frame with full preprocessing to populate richer debug stages.
                best_det_full = detect_expiry_date(batch_imgs[-1], fast=False)
                expiry = ExpiryDetection(
                    date_type=best_det_full.date_type,
                    raw_text=raw_txt,
                    normalized_date=iso,
                    confidence=float(exp_conf),
                    stages=best_det_full.stages,
                )
                stages["expiry"] = expiry.stages
            elif best_det is not None:
                # No consensus: return best single-frame guess for debugging/tests.
                expiry = best_det
                stages["expiry"] = expiry.stages

        timing_ms["ocr_ms"] = (time.perf_counter() - t_ocr0) * 1000.0
        stages["ocr_engine"] = "rapidocr"
        ec = stages.get("expiry_consensus") or {}
        recognition_log.info(
            "EXPIRY_OCR | frames=%d | rule=%s | accepted=%s | date=%s | ms=%.1f",
            len(images_bgr),
            ec.get("rule"),
            ec.get("accepted"),
            (expiry.normalized_date if expiry else None) or "-",
            timing_ms["ocr_ms"],
        )
    else:
        timing_ms["ocr_ms"] = 0.0
        stages["ocr_engine"] = "skipped"
        stages["expiry_skipped"] = True

    # Confidence and tier: barcode and expiry are separate signals; keep it simple for now.
    conf = 0.0
    if display_barcode:
        conf = max(conf, 0.93)
    if expiry and expiry.normalized_date:
        conf = max(conf, float(min(0.92, max(0.0, expiry.confidence))))
    stages["tier"] = "high" if conf >= 0.85 else ("medium" if conf >= 0.5 else "low")

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
        raw_ocr_text=(
            " ".join(
                [
                    str(x.get("text", ""))
                    for x in ((expiry.stages.get("raw_hits_head") if expiry else None) or [])
                    if isinstance(x, dict)
                ]
            ).strip()
            if expiry
            else ""
        ),
        date_type=expiry.date_type if expiry else DateType.unknown,
        raw_date_text=expiry.raw_text if expiry else None,
        normalized_date=expiry.normalized_date if expiry else None,
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
