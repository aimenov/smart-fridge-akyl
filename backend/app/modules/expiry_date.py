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
_RE_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")


def _normalize_unicode_separators(s: str) -> str:
    """Map unicode slashes/dashes (common in OCR) to ASCII separators for regex/parsing."""
    out = s
    for ch in (
        "\uff0f",
        "\uff3c",
        "\u2215",
        "\u2044",
        "\ufe63",
        "\u2013",
        "\u2014",
        "\u2212",
    ):
        out = out.replace(ch, ".")
    return out


def _expiry_date_token_variants(token: str) -> list[tuple[str, float]]:
    """
    OCR repairs for dotted-matrix DD.MM.YY style expiry lines.

    Returns ``(variant_text, score_bonus)`` — bonus nudges ambiguous parses toward real expiry crops.

    - Ghost leading ``1`` on ``03`` for Jan/Feb (``13.01`` / ``13.02`` → ``03.01`` / ``03.02``).
    - Day ``11``–``19`` → subtract 10 (``13`` misread for ``03`` on dot-matrix).
    - Two-digit year neighbours: **YY+1** is boosted when the printed year ends in ``25``/``26``
      (common under-read before ``27``); **YY−1** is boosted when it ends in ``28``/``29`` (over-read).
      Other year shifts are strongly penalized so ``03.02.28`` does not beat a literal ``03.02.27``.
    """
    t = token.strip().replace("-", ".").replace("/", ".").strip()
    bonuses: dict[str, float] = {}

    def add(x: str, bonus: float = 0.0) -> None:
        if not x:
            return
        bonuses[x] = max(bonuses.get(x, float("-inf")), bonus)

    add(t, 0.0)
    # Ghost leading ``1`` on ``03`` for Jan/Feb.
    if re.match(r"^13\.0[12]\.", t):
        add("03" + t[2:], 22.0)

    mday = re.match(r"^(\d{2})\.(\d{1,2})\.(\d{2})$", t)
    if mday:
        d_s, mo, yy_s = mday.groups()
        d_i = int(d_s)
        if 11 <= d_i <= 19:
            add(f"{d_i - 10:02d}.{mo}.{yy_s}", 28.0)

    # YY±1 on every DD.MM.YY token we already derived — needed after day-correction
    # ``03.02.26`` → ``03.02.27`` (year digit slips); original OCR ``13.02-26`` never sees YY±1 on ``03.*`` otherwise.
    #
    # Do **not** use one penalty for all shifts: ``03.02.27`` + YY+1 → ``03.02.28`` must lose to the literal read
    # (future_bias slightly favors later dates). Real slips are usually ``…25/26`` read low → true ``…27``.
    # Conversely ``…28/29`` may be OCR high → true ``…27``.
    shift_penalty = 22.0
    snap = list(bonuses.keys())
    for key in snap:
        m_yy = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$", key)
        if not m_yy:
            continue
        d, mo, yy_s = m_yy.groups()
        yv = int(yy_s)
        base_b = bonuses[key]
        bonus_next = base_b + (14.0 if yv in (25, 26) else -shift_penalty)
        bonus_prev = base_b + (14.0 if yv in (28, 29) else -shift_penalty)
        add(f"{d}.{mo}.{(yv + 1) % 100:02d}", bonus_next)
        add(f"{d}.{mo}.{(yv - 1) % 100:02d}", bonus_prev)
    # Stable order for debugging.
    return sorted(bonuses.items(), key=lambda kv: (-kv[1], kv[0]))


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


def _prep_variants(bgr: np.ndarray, *, fast: bool) -> list[tuple[str, np.ndarray]]:
    """Fast OpenCV-only preprocessing variants to help dotted-matrix print."""
    out: list[tuple[str, np.ndarray]] = []
    # Downscale large frames for speed (mobile frames can be huge; expiry area is usually a small region anyway).
    h0, w0 = bgr.shape[:2]
    max_edge = max(h0, w0)
    # Keep enough detail for dot-matrix while still bounding runtime.
    target_edge = 1100 if fast else 1100
    if max_edge > target_edge:
        scale = float(target_edge) / float(max_edge)
        bgr = cv2.resize(
            bgr,
            (max(2, int(w0 * scale)), max(2, int(h0 * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    def add(tag: str, g: np.ndarray) -> None:
        out.append((tag, g))

    add("gray", gray)

    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    if not fast:
        add("clahe", clahe.apply(gray))

    # Sharpen helps dot-matrix edges.
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0)
    sharp = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)
    add("sharp", np.clip(sharp, 0, 255).astype(np.uint8))

    # One adaptive-threshold variant in fast mode can rescue low-contrast prints.
    if fast:
        ath = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
        )
        add("ath", ath)

    if not fast:
        ath = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
        )
        add("ath", ath)
        add("ath_inv", 255 - ath)

        # Upscale small crops (keeps this bounded so it's still quick).
        scaled: list[tuple[str, np.ndarray]] = []
        for tag, g in out:
            h, w = g.shape[:2]
            if max(h, w) < 900:
                scaled.append(
                    (f"{tag}_2x", cv2.resize(g, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC))
                )
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


def detect_expiry_date(bgr: np.ndarray, *, fast: bool = False) -> ExpiryDetection:
    """
    Detect a date in a user-cropped expiry area quickly.

    We favor candidates *lower in the image* when multiple dates are present
    (common label layout: produced on top, expiry/use-by below).
    """
    ocr = _get_ocr()
    year_now = date.today().year
    today = date.today()

    def parse_ocr_results(tag: str, res: Any, *, img_h: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (candidates, raw_hits) from RapidOCR result list."""
        local_cands: list[dict[str, Any]] = []
        local_hits: list[dict[str, Any]] = []
        if not res:
            return local_cands, local_hits
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
            # Normalize common OCR confusions for date parsing.
            t_norm = (
                t.replace("O", "0")
                .replace("o", "0")
                .replace("I", "1")
                .replace("l", "1")
                .replace("Z", "2")
                .replace("S", "5")
                .replace("G", "6")
                .replace("B", "8")
            )
            # OCR sometimes uses ':' as a separator between date components; treat it like '.'
            # for date parsing (but keep real HH:MM time detection separate).
            t_parse = _normalize_unicode_separators(t_norm.replace(":", "."))
            try:
                sc = float(score)
            except Exception:
                sc = 0.0

            y = _center_y(np.asarray(box))
            local_hits.append({"tag": tag, "text": t, "score": round(sc, 3), "y": round(y, 1)})

            # Extract dates from the text token(s).
            has_time = bool(_RE_TIME.search(t_norm))
            for m in _RE_NUM_DATE.finditer(t_parse):
                token = m.group(0)
                for vt, vbonus in _expiry_date_token_variants(token):
                    dt = _parse_numeric_date(vt)
                    if not dt:
                        continue
                    # Prefer lower y and higher OCR confidence.
                    year_bias = 18.0 if dt.year >= year_now else -28.0
                    # Dotted-matrix often misreads the year; for expiry scans, past years are almost always wrong.
                    time_penalty = -22.0 if has_time else 0.0
                    # Prefer later dates (expiry is usually after manufacturing).
                    # Keep this bounded so crazy far-future dates don't dominate.
                    delta_days = (dt - today).days
                    delta_days = max(-3650, min(3650, int(delta_days)))
                    future_bias = (delta_days / 3650.0) * 22.0
                    cand_score = (
                        sc * 100.0
                        + (y / max(1.0, float(img_h))) * 60.0
                        + year_bias
                        + time_penalty
                        + future_bias
                        + vbonus
                    )
                    local_cands.append(
                        {
                            "tag": tag,
                            "raw": vt,
                            "iso": dt.isoformat(),
                            "dt": dt,
                            "ocr_score": sc,
                            "y": y,
                            "score": cand_score,
                            "has_time": has_time,
                        }
                    )
        return local_cands, local_hits

    def run_ocr_one(tag: str, g: np.ndarray) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            if g.ndim == 2:
                img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            else:
                img = g
            res, _ = ocr(img)
        except Exception as e:
            logger.debug("expiry OCR variant failed tag=%s err=%s", tag, e)
            return [], []
        h_img = g.shape[0] if g.ndim >= 2 else 1
        return parse_ocr_results(tag, res, img_h=h_img)

    # Build ROI list.
    rois: list[tuple[str, np.ndarray]] = [("full", bgr)]
    h, w = bgr.shape[:2]
    if h >= 40 and w >= 40:
        rois.insert(0, ("tr_70", bgr[0 : int(h * 0.75), int(w * 0.20) : w]))

    candidates: list[dict[str, Any]] = []
    raw_hits: list[dict[str, Any]] = []

    if fast:
        # Lightning-fast path: try a small number of OCR passes and short-circuit on a strong expiry hit.
        # Order matters: sharp is usually best for dot-matrix; then ath; fall back to gray.
        plan = ("sharp", "ath", "gray")
        # Try top-right first; only fall back to full frame if nothing found.
        for roi_tag, roi in rois[:1]:
            for vtag, img in _prep_variants(roi, fast=True):
                if vtag not in plan:
                    continue
                tag = f"{roi_tag}_{vtag}"
                cands, hits = run_ocr_one(tag, img)
                raw_hits.extend(hits)
                candidates.extend(cands)
                # Short-circuit: strongest eligible parse (variants may add multiple dates per line).
                eligible_sc = [
                    c
                    for c in cands
                    if isinstance(c.get("dt"), date)
                    and not c.get("has_time")
                    and c["dt"] >= today
                    and float(c.get("ocr_score") or 0.0) >= 0.75
                ]
                if eligible_sc:
                    best_sc = max(eligible_sc, key=lambda c: float(c["score"]))
                    stages = {
                        "variants_tried": 1,
                        "raw_hits_head": raw_hits[:24],
                        "candidates": [best_sc],
                    }
                    conf = float(min(0.98, max(0.0, float(best_sc["ocr_score"]) * 0.95 + 0.15)))
                    return ExpiryDetection(
                        date_type=DateType.expiry,
                        raw_text=str(best_sc["raw"]),
                        normalized_date=str(best_sc["iso"]),
                        confidence=conf,
                        stages=stages,
                    )
            # If we saw any non-time candidates in this ROI, don't expand work further.
            if any((not x.get("has_time")) for x in candidates):
                break

        if not candidates:
            # Fallback: run a single sharp pass on the full frame.
            for roi_tag, roi in rois[-1:]:
                for vtag, img in _prep_variants(roi, fast=True):
                    if vtag != "sharp":
                        continue
                    tag = f"{roi_tag}_{vtag}"
                    cands, hits = run_ocr_one(tag, img)
                    raw_hits.extend(hits)
                    candidates.extend(cands)
                    break
    else:
        # Full (still fast-ish) path: evaluate a richer set of variants.
        variants: list[tuple[str, np.ndarray]] = []
        for roi_tag, roi in rois:
            for vtag, img in _prep_variants(roi, fast=False):
                variants.append((f"{roi_tag}_{vtag}", img))
        for tag, g in variants:
            cands, hits = run_ocr_one(tag, g)
            raw_hits.extend(hits)
            candidates.extend(cands)

    # If we only saw time-stamped (manufacturing) dates, do not return a date.
    # This prevents the live scan from "locking" on manufacturing instead of expiry.
    if candidates and all(bool(c.get("has_time")) for c in candidates):
        stages = {
            "variants_tried": 0 if fast else 0,
            "raw_hits_head": raw_hits[:24],
            "candidates": sorted(candidates, key=lambda c: -c["score"])[:12],
        }
        logger.debug("expiry: only manufacturing/time-stamped dates found; skipping")
        return ExpiryDetection(
            date_type=DateType.unknown,
            raw_text=None,
            normalized_date=None,
            confidence=0.0,
            stages=stages,
        )

    # Post-process: if we saw a manufacturing date with time, and another date with same day/month but a lower year,
    # bump that date's year up to the manufacturing year (common dotted-matrix OCR year slip: 26 -> 25).
    manuf_year = None
    for c in candidates:
        if c.get("has_time") and isinstance(c.get("dt"), date):
            y = c["dt"].year
            manuf_year = y if manuf_year is None else max(manuf_year, y)

    if manuf_year is not None:
        for c in candidates:
            dt = c.get("dt")
            if not isinstance(dt, date) or c.get("has_time"):
                continue
            if dt.year < manuf_year and (manuf_year - dt.year) <= 1:
                bumped = _try_date(manuf_year, dt.month, dt.day)
                if bumped:
                    c["dt"] = bumped
                    c["iso"] = bumped.isoformat()
                    # Reward the bump slightly so it wins over the manufacturing date.
                    c["score"] = float(c["score"]) + 14.0
    else:
        # If we did not see a manufacturing year, but the parsed year is just one behind "now",
        # bump it forward (common OCR slip for dotted-matrix years).
        for c in candidates:
            dt = c.get("dt")
            if not isinstance(dt, date) or c.get("has_time"):
                continue
            if dt.year < year_now and (year_now - dt.year) <= 1:
                bumped = _try_date(year_now, dt.month, dt.day)
                if bumped:
                    c["dt"] = bumped
                    c["iso"] = bumped.isoformat()
                    c["score"] = float(c["score"]) + 10.0

    # Prefer non-time (expiry) dates; if still tied, take highest score.
    best = max(
        candidates,
        # Prefer non-time (expiry) over time-stamped (manufacturing) lines.
        key=lambda c: ((not bool(c.get("has_time", False))), float(c["score"])),
    ) if candidates else None
    stages = {
        "variants_tried": (len(variants) if (not fast) else 0),
        "raw_hits_head": raw_hits[:24],
        "candidates": sorted(candidates, key=lambda c: -c["score"])[:12],
    }

    if not best:
        logger.debug("expiry: miss | candidates=0")
        return ExpiryDetection(
            date_type=DateType.unknown,
            raw_text=None,
            normalized_date=None,
            confidence=0.0,
            stages=stages,
        )

    # Confidence: map OCR score (0..1) into something usable, and bump slightly because crop is targeted.
    conf = float(min(0.98, max(0.0, best["ocr_score"] * 0.95 + 0.15)))
    logger.debug(
        "expiry: best=%s raw=%s conf=%.2f candidates=%d",
        best["iso"],
        best["raw"],
        conf,
        len(candidates),
    )
    return ExpiryDetection(
        date_type=DateType.expiry,
        raw_text=str(best["raw"]),
        normalized_date=str(best["iso"]),
        confidence=conf,
        stages=stages,
    )

