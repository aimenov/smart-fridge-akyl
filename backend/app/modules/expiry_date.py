from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import cv2
import numpy as np

from backend.app.logging_config import get_expiry_logger
from backend.app.models.entities import DateType

logger = logging.getLogger(__name__)
exp_log = get_expiry_logger()


# Disable PaddleX "model host connectivity" check (prevents slow startup in offline LAN setups).
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@dataclass(slots=True)
class ParsedDate:
    raw_text: str
    date_type: DateType
    normalized_date: Optional[str]
    parser_conf: float
    keyword_strength: float


@dataclass(slots=True)
class OcrHit:
    text: str
    ocr_conf: float
    crop_tag: str
    near_edge: bool


@dataclass(slots=True)
class ExpiryResult:
    date_type: DateType
    raw_date_text: Optional[str]
    normalized_date: Optional[str]
    confidence: float
    stages: dict[str, Any]


_MONTHS = {
    # English
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "SEPT": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
    # Russian (common abbreviations)
    "ЯНВ": 1,
    "ФЕВ": 2,
    "МАР": 3,
    "АПР": 4,
    "МАЙ": 5,
    "ИЮН": 6,
    "ИЮЛ": 7,
    "АВГ": 8,
    "СЕН": 9,
    "ОКТ": 10,
    "НОЯ": 11,
    "ДЕК": 12,
    # Kazakh (latin-ish abbreviations sometimes appear)
    "QAN": 1,
    "AQP": 2,
    "NAU": 3,
    "SÄU": 4,
    "SAU": 4,
    "MAM": 5,
    "MAU": 5,
    "MAI": 5,
    "MUS": 6,
    "SHI": 7,
    "TAM": 8,
    "QYR": 9,
    "KYZ": 10,
    "QAZ": 10,
    "KAR": 11,
    "JEL": 12,
}

_KW_EXPIRY = [
    "EXP",
    "USE BY",
    "USE-BY",
    "BB",
    "BEST BEFORE",
    "BEST-BEFORE",
    "ГОДЕН ДО",
    "СРОК ГОДНОСТИ",
    "ГОДНОСТЬ",
    "ЖАРАМДЫЛЫҚ МЕРЗІМІ",
    "ЖАРАМДЫ",
]
_KW_MANUFACTURED = [
    "MFG",
    "MAN",
    "PROD",
    "PRODUCED",
    "PACKED",
    "ИЗГОТОВ",
    "ДАТА ИЗГОТОВ",
    "ӨНДІРІЛГЕН",
    "ДАТА ӨНДІРУ",
    "ҚҰЙЫЛҒАН",
]


def _norm_text(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = s.replace("—", "-").replace("–", "-").replace("_", "-")
    s = re.sub(r"\s+", " ", s)
    return s


def _upper_alnumish(s: str) -> str:
    s = _norm_text(s).upper()
    # common OCR confusions that matter in dates
    s = s.replace("O", "0")
    s = s.replace("I", "1")
    s = s.replace("L", "1")
    return s


def _contains_any(hay: str, needles: list[str]) -> float:
    if not hay:
        return 0.0
    h = hay.upper()
    for i, n in enumerate(needles):
        if n in h:
            # earlier keywords are stronger
            return 1.0 - i * 0.03
    return 0.0


def _try_build_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


_RE_NUM = re.compile(r"(?P<a>\d{1,4})\s*[-./]\s*(?P<b>\d{1,2})\s*[-./]\s*(?P<c>\d{1,4})")
_RE_DDMMM = re.compile(
    r"(?P<d>\d{1,2})\s*(?P<m>[A-ZА-ЯЁ]{3,5})\s*(?P<y>\d{2,4})"
)
_RE_MMMDD = re.compile(
    r"(?P<m>[A-ZА-ЯЁ]{3,5})\s*(?P<d>\d{1,2})\s*(?P<y>\d{2,4})"
)


def _parse_one_date(text: str) -> Optional[tuple[str, float]]:
    """
    Return ISO date string + parser confidence.
    """
    t = _upper_alnumish(text)
    if not t:
        return None

    m = _RE_NUM.search(t)
    if m:
        a = int(m.group("a"))
        b = int(m.group("b"))
        c = int(m.group("c"))
        # Heuristics: choose between YYYY-MM-DD and DD-MM-YYYY
        if a >= 1900 and a <= 2099:
            y, mm, dd = a, b, c
        elif c >= 1900 and c <= 2099:
            y, mm, dd = c, b, a
        else:
            # 2-digit year
            if c < 100:
                y = 2000 + c
            else:
                y = c
            mm, dd = b, a
        dt = _try_build_date(y, mm, dd)
        if dt:
            # short years / ambiguous ordering get slightly lower confidence
            conf = 0.78 if (a < 1900 and c < 1900) else 0.88
            return dt.isoformat(), conf

    m = _RE_DDMMM.search(t)
    if m:
        dd = int(m.group("d"))
        mm_txt = m.group("m").strip(".")
        yy = int(m.group("y"))
        mm = _MONTHS.get(mm_txt[:4], _MONTHS.get(mm_txt[:3]))
        if mm:
            y = yy if yy >= 1900 else (2000 + yy if yy < 100 else yy)
            dt = _try_build_date(y, mm, dd)
            if dt:
                return dt.isoformat(), 0.9

    m = _RE_MMMDD.search(t)
    if m:
        dd = int(m.group("d"))
        mm_txt = m.group("m").strip(".")
        yy = int(m.group("y"))
        mm = _MONTHS.get(mm_txt[:4], _MONTHS.get(mm_txt[:3]))
        if mm:
            y = yy if yy >= 1900 else (2000 + yy if yy < 100 else yy)
            dt = _try_build_date(y, mm, dd)
            if dt:
                return dt.isoformat(), 0.86

    return None


def parse_date_like(text: str) -> Optional[ParsedDate]:
    raw = _norm_text(text)
    if not raw:
        return None

    up = _upper_alnumish(raw)
    up_sp = f" {up} "

    def best_kw_pos(keywords: list[str]) -> Optional[int]:
        best = None
        for kw in keywords:
            pos = up_sp.find(f" {kw} ")
            if pos >= 0:
                best = pos if best is None else min(best, pos)
        return best

    exp_pos = best_kw_pos(_KW_EXPIRY)
    mfg_pos = best_kw_pos(_KW_MANUFACTURED)

    # Collect all date-like matches with spans.
    found: list[tuple[int, int, str, float]] = []

    for m in _RE_NUM.finditer(up):
        iso_conf = _parse_one_date(m.group(0))
        if iso_conf:
            iso, conf = iso_conf
            found.append((m.start(), m.end(), iso, conf))
    for m in _RE_DDMMM.finditer(up):
        iso_conf = _parse_one_date(m.group(0))
        if iso_conf:
            iso, conf = iso_conf
            found.append((m.start(), m.end(), iso, conf))
    for m in _RE_MMMDD.finditer(up):
        iso_conf = _parse_one_date(m.group(0))
        if iso_conf:
            iso, conf = iso_conf
            found.append((m.start(), m.end(), iso, conf))

    if not found:
        return None

    def pick_nearest(pos: int) -> tuple[int, int, str, float]:
        # Prefer dates after the keyword, else nearest by absolute distance.
        after = [x for x in found if x[0] >= pos]
        if after:
            return min(after, key=lambda x: (x[0] - pos, -x[3]))
        return min(found, key=lambda x: (abs(x[0] - pos), -x[3]))

    # Decide which date we want and type.
    if exp_pos is not None:
        s0, s1, iso, pconf = pick_nearest(exp_pos)
        if "BEST" in up or "BB" in up:
            dt = DateType.best_before
        elif "USE" in up:
            dt = DateType.use_by
        else:
            dt = DateType.expiry
        kw_strength = max(0.75, _contains_any(up, _KW_EXPIRY))
        chosen_raw = raw[s0:s1]
    elif mfg_pos is not None:
        s0, s1, iso, pconf = pick_nearest(mfg_pos)
        dt = DateType.produced_on
        kw_strength = max(0.6, _contains_any(up, _KW_MANUFACTURED))
        chosen_raw = raw[s0:s1]
    else:
        # No keywords: pick highest-confidence parse, but keep unknown type.
        s0, s1, iso, pconf = max(found, key=lambda x: x[3])
        dt = DateType.unknown
        kw_strength = 0.0
        chosen_raw = raw[s0:s1]

    return ParsedDate(
        raw_text=raw,
        date_type=dt,
        normalized_date=iso,
        parser_conf=float(pconf),
        keyword_strength=float(kw_strength),
    )


def _clahe(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _light_denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)


def _light_sharpen(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.0)
    sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)


def _variants_from_bgr(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    v0 = gray
    v1 = _clahe(gray)
    v2 = cv2.normalize(v1, None, 0, 255, cv2.NORM_MINMAX)
    v3 = cv2.adaptiveThreshold(v1, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)
    v4 = cv2.bitwise_not(v3)
    out: list[tuple[str, np.ndarray]] = []
    for tag, g in (
        ("orig", v0),
        ("clahe", v1),
        ("contrast", v2.astype(np.uint8)),
        ("ath", v3),
        ("ath_inv", v4),
    ):
        dn = _light_denoise(g)
        sh = _light_sharpen(dn)
        out.append((tag, cv2.cvtColor(sh, cv2.COLOR_GRAY2BGR)))
    return out


def _rotate_bgr(bgr: np.ndarray, angle: int) -> np.ndarray:
    if angle == 0:
        return bgr
    if angle == 90:
        return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(bgr, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return bgr


def _box_to_rect(poly: np.ndarray, w: int, h: int, *, margin: float = 0.12) -> tuple[int, int, int, int]:
    pts = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
    x0 = float(np.min(pts[:, 0]))
    y0 = float(np.min(pts[:, 1]))
    x1 = float(np.max(pts[:, 0]))
    y1 = float(np.max(pts[:, 1]))
    dx = (x1 - x0) * margin
    dy = (y1 - y0) * margin
    xa = max(0, int(x0 - dx))
    ya = max(0, int(y0 - dy))
    xb = min(w, int(x1 + dx))
    yb = min(h, int(y1 + dy))
    return xa, ya, xb, yb


def _near_edge(x0: int, y0: int, x1: int, y1: int, w: int, h: int) -> bool:
    if w <= 0 or h <= 0:
        return False
    pad_x = int(w * 0.18)
    pad_y = int(h * 0.18)
    return x0 <= pad_x or y0 <= pad_y or x1 >= (w - pad_x) or y1 >= (h - pad_y)


def _looks_like_text_box(x0: int, y0: int, x1: int, y1: int, w: int, h: int) -> bool:
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    area = (bw * bh) / float(max(1, w * h))
    if area < 0.0004 or area > 0.25:
        return False
    ar = bw / float(bh)
    if ar < 1.2 and bh > 0.12 * h:
        return False
    return True


def _ocr_predict_texts(ocr, bgr: np.ndarray) -> list[tuple[str, float]]:
    """
    PaddleOCR v3 ``predict`` returns list[dict] with ``rec_texts`` + ``rec_scores``.
    """
    try:
        res = ocr.predict(bgr)
        if not res:
            return []
        r0 = res[0]
        texts = r0.get("rec_texts") or []
        scores = r0.get("rec_scores") or []
        out: list[tuple[str, float]] = []
        for i, t in enumerate(list(texts)):
            if not t:
                continue
            sc = float(scores[i]) if i < len(scores) else 0.0
            out.append((str(t), sc))
        return out
    except Exception:
        logger.debug("expiry ocr.predict failed", exc_info=True)
        return []


def _get_ocr(lang: str):
    # Lazy import to keep import-time cheap for non-OCR paths.
    from paddleocr import PaddleOCR  # type: ignore

    return PaddleOCR(
        lang=lang,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


_OCR_CACHE: dict[str, Any] = {}


def _ocr_engine(lang: str):
    eng = _OCR_CACHE.get(lang)
    if eng is None:
        eng = _get_ocr(lang)
        _OCR_CACHE[lang] = eng
    return eng


def extract_expiry(images_bgr: list[np.ndarray]) -> ExpiryResult:
    """
    Expiry pipeline:
    - propose text regions via Paddle det boxes on enhanced full frame
    - OCR only expanded crops (variants + rotations) via two passes: kk + en
    - parse and score date-like lines
    - consensus across frames (avoid surfacing low-confidence guesses)
    """
    stages: dict[str, Any] = {}
    if not images_bgr:
        return ExpiryResult(DateType.unknown, None, None, 0.0, {"reason": "no_frames"})

    ocr_kk = _ocr_engine("kk")
    ocr_en = _ocr_engine("en")

    all_best_per_frame: list[dict[str, Any]] = []
    votes: dict[str, int] = {}
    best_candidate: Optional[tuple[float, ParsedDate, OcrHit]] = None

    for fi, frame in enumerate(images_bgr[:5]):
        h, w = frame.shape[:2]
        frame_best: Optional[tuple[float, ParsedDate, OcrHit]] = None

        # Stage C: detect text boxes on a contrast-enhanced variant.
        det_variant = _variants_from_bgr(frame)[1][1]  # "clahe"
        det_res_a = ocr_kk.predict(det_variant)
        det_res_b = ocr_kk.predict(frame)
        r0a = det_res_a[0] if det_res_a else {}
        r0b = det_res_b[0] if det_res_b else {}
        polys_a = r0a.get("rec_polys") or r0a.get("dt_polys") or []
        polys_b = r0b.get("rec_polys") or r0b.get("dt_polys") or []
        polys = list(polys_a) + [p for p in polys_b if p is not None]

        rects: list[tuple[int, int, int, int, bool, str]] = []
        for bi, poly in enumerate(list(polys)[:40]):
            x0, y0, x1, y1 = _box_to_rect(poly, w, h, margin=0.22)
            if not _looks_like_text_box(x0, y0, x1, y1, w, h):
                continue
            ne = _near_edge(x0, y0, x1, y1, w, h)
            rects.append((x0, y0, x1, y1, ne, f"b{bi}"))

        # Cluster neighboring boxes (vertical stack / same seam) to capture keyword + date together.
        rects.sort(key=lambda r: (r[1], r[0]))
        clusters: list[list[tuple[int, int, int, int, bool, str]]] = []
        for r in rects:
            x0, y0, x1, y1, ne, tag = r
            placed = False
            for c in clusters:
                cx0 = min(x[0] for x in c)
                cy0 = min(x[1] for x in c)
                cx1 = max(x[2] for x in c)
                cy1 = max(x[3] for x in c)
                # vertical proximity + some x overlap
                close_y = abs(((y0 + y1) / 2.0) - ((cy0 + cy1) / 2.0)) <= (0.10 * h)
                overlap = min(x1, cx1) - max(x0, cx0)
                if close_y and overlap >= -0.08 * w and len(c) < 5:
                    c.append(r)
                    placed = True
                    break
            if not placed:
                clusters.append([r])

        crops: list[tuple[str, np.ndarray, bool]] = []
        # individual boxes
        for x0, y0, x1, y1, ne, tag in rects[:60]:
            crop = frame[y0:y1, x0:x1].copy()
            if crop.size >= 50:
                crops.append((f"f{fi}_{tag}", crop, ne))
        # clusters (expanded)
        for ci, c in enumerate(clusters[:25]):
            if len(c) < 2:
                continue
            x0 = min(x[0] for x in c)
            y0 = min(x[1] for x in c)
            x1 = max(x[2] for x in c)
            y1 = max(x[3] for x in c)
            ne = any(x[4] for x in c)
            pad_x = int((x1 - x0) * 0.18)
            pad_y = int((y1 - y0) * 0.22)
            xa = max(0, x0 - pad_x)
            ya = max(0, y0 - pad_y)
            xb = min(w, x1 + pad_x)
            yb = min(h, y1 + pad_y)
            crop = frame[ya:yb, xa:xb].copy()
            if crop.size >= 50:
                crops.append((f"f{fi}_grp{ci}", crop, ne))

        stages.setdefault("expiry_boxes_per_frame", []).append(int(len(rects)))
        stages.setdefault("expiry_crops_per_frame", []).append(int(len(crops)))

        # Stage D: OCR each crop with variants + rotations, then parse date-like lines.
        for tag, crop, ne in crops[:28]:
            for vtag, vbgr in _variants_from_bgr(crop):
                for ang in (0, 90, 180, 270):
                    rbgr = _rotate_bgr(vbgr, ang)
                    texts = []
                    texts.extend(_ocr_predict_texts(ocr_kk, rbgr))
                    texts.extend(_ocr_predict_texts(ocr_en, rbgr))
                    if not texts:
                        continue

                    joined = " | ".join(t for t, _ in texts[:6])
                    avg_conf = float(sum(sc for _, sc in texts[:6]) / max(1, len(texts[:6])))
                    parsed = parse_date_like(joined)
                    if not parsed or not parsed.normalized_date:
                        continue

                    kw_bonus = parsed.keyword_strength * 0.25
                    edge_bonus = 0.08 if ne else 0.0
                    type_bonus = 0.12 if parsed.date_type in (DateType.expiry, DateType.best_before, DateType.use_by) else 0.0
                    score = (avg_conf * 0.55) + (parsed.parser_conf * 0.35) + kw_bonus + edge_bonus + type_bonus
                    hit = OcrHit(text=joined, ocr_conf=avg_conf, crop_tag=f"{tag}_{vtag}_{ang}", near_edge=ne)

                    if frame_best is None or score > frame_best[0]:
                        frame_best = (score, parsed, hit)

        if frame_best:
            sc, parsed, hit = frame_best
            all_best_per_frame.append(
                {
                    "score": round(float(sc), 3),
                    "date_type": parsed.date_type.value,
                    "normalized_date": parsed.normalized_date,
                    "raw": parsed.raw_text[:120],
                    "ocr_conf": round(float(hit.ocr_conf), 3),
                    "crop": hit.crop_tag,
                    "near_edge": hit.near_edge,
                    "kw": round(float(parsed.keyword_strength), 3),
                }
            )
            key = f"{parsed.date_type.value}:{parsed.normalized_date}"
            votes[key] = votes.get(key, 0) + 1
            if best_candidate is None or sc > best_candidate[0]:
                best_candidate = (sc, parsed, hit)

    stages["expiry_best_per_frame"] = all_best_per_frame[:10]
    stages["expiry_votes"] = votes

    if not votes or best_candidate is None:
        stages["expiry_consensus"] = {"accepted": False, "rule": "no_votes"}
        exp_log.info("EXPIRY | no date-like candidates")
        return ExpiryResult(DateType.unknown, None, None, 0.0, stages)

    frames_with = max(1, len(all_best_per_frame))
    winner, n = max(votes.items(), key=lambda kv: kv[1])
    ratio = n / float(frames_with)
    accepted = bool(n >= 2 and ratio >= 0.6)
    if frames_with == 1 and n == 1:
        # Still images / single-frame uploads: accept only with strong OCR+parser signal.
        sc, parsed, hit = best_candidate
        accepted = bool(
            sc >= 0.92
            and hit.ocr_conf >= 0.88
            and parsed.parser_conf >= 0.85
            and parsed.date_type in (DateType.expiry, DateType.best_before, DateType.use_by, DateType.produced_on)
        )
    stages["expiry_consensus"] = {
        "accepted": accepted,
        "frames_with_candidate": frames_with,
        "winner": winner,
        "votes": int(n),
        "ratio": round(float(ratio), 3),
    }

    # Choose best candidate matching winner
    _, parsed_best, hit_best = best_candidate
    want_type, want_date = winner.split(":", 1)
    if not (parsed_best.date_type.value == want_type and parsed_best.normalized_date == want_date):
        # Find best matching among per-frame best list
        for item in all_best_per_frame:
            if item.get("date_type") == want_type and item.get("normalized_date") == want_date:
                parsed_best = ParsedDate(
                    raw_text=str(item.get("raw") or ""),
                    date_type=DateType(want_type) if want_type in DateType._value2member_map_ else DateType.unknown,
                    normalized_date=want_date,
                    parser_conf=0.8,
                    keyword_strength=float(item.get("kw") or 0.0),
                )
                hit_best = OcrHit(text=str(item.get("raw") or ""), ocr_conf=float(item.get("ocr_conf") or 0.0), crop_tag=str(item.get("crop") or ""), near_edge=bool(item.get("near_edge")))
                break

    if accepted:
        # Confidence is scaled to stay comparable with barcode path.
        conf = float(min(0.98, max(0.0, best_candidate[0])))
        exp_log.info(
            "EXPIRY | accepted=%s votes=%s ratio=%.2f | %s %s | raw=%s",
            accepted,
            n,
            ratio,
            parsed_best.date_type.value,
            parsed_best.normalized_date,
            parsed_best.raw_text[:80],
        )
        return ExpiryResult(parsed_best.date_type, hit_best.text, parsed_best.normalized_date, conf, stages)

    exp_log.info(
        "EXPIRY | rejected votes=%s ratio=%.2f (need >=2 and >=0.60); best=%s %s",
        n,
        ratio,
        parsed_best.date_type.value,
        parsed_best.normalized_date,
    )
    return ExpiryResult(DateType.unknown, hit_best.text, None, 0.0, stages)

