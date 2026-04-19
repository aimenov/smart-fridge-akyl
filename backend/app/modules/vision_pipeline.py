from __future__ import annotations

import logging
import os
import re
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
from backend.app.modules.product_from_ocr import expand_ocr_lines, pick_product_name
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


_MISSING = object()
_paddle_singleton: Any = None


def _ensure_paddle_runtime_env() -> None:
    """PaddleOCR 3.x / PaddleX checks “model hosts” unless this is set — slow and fails offline."""
    if settings.paddle_pdx_disable_model_source_check:
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


_ensure_paddle_runtime_env()


def _get_paddle_ocr():
    """Single shared PaddleOCR instance (lazy); None if unavailable."""
    global _paddle_singleton
    if _paddle_singleton is _MISSING:
        return None
    if _paddle_singleton is not None:
        return _paddle_singleton
    _ensure_paddle_runtime_env()
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        logger.warning("%spaddleocr package not installed — use pip install -e \".[dev]\" (Python <3.14 for Paddle wheels)", trace_prefix())
        _paddle_singleton = _MISSING
        return None
    langs: list[str] = []
    if settings.ocr_lang and str(settings.ocr_lang).strip():
        langs.append(str(settings.ocr_lang).strip())
    for fb in ("multilingual", "ru", "en"):
        if fb not in langs:
            langs.append(fb)

    last_err: Optional[Exception] = None
    for lang in langs:
        try:
            inst = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            _paddle_singleton = inst
            logger.info("%sPaddleOCR engine initialised (singleton) lang=%s", trace_prefix(), lang)
            return inst
        except Exception as e:
            last_err = e
            logger.warning("%sPaddleOCR(lang=%r) failed (%s); trying next language", trace_prefix(), lang, e)

    logger.error(
        "%sPaddleOCR() failed for all languages %r — restart after fixing install. Last error: %r",
        trace_prefix(),
        langs,
        last_err,
    )
    _paddle_singleton = _MISSING
    return None


def _lines_from_paddle_result(result: Any) -> tuple[list[str], list[float]]:
    chunks: list[str] = []
    confs: list[float] = []
    if not result or not result[0]:
        return chunks, confs
    for line in result[0]:
        text = line[1][0]
        conf = float(line[1][1])
        chunks.append(text)
        confs.append(conf)
    return chunks, confs


def _run_paddle_on_gray(ocr: Any, gray: np.ndarray) -> tuple[list[str], list[float]]:
    try:
        result = ocr.ocr(gray, cls=True)
        return _lines_from_paddle_result(result)
    except Exception:
        return [], []


def _full_frame_pass(ocr: Any, images_bgr: list[np.ndarray]) -> tuple[str, float]:
    """Run OCR on resized full frames + light sharpen — best for packaging labels."""
    all_lines: list[str] = []
    all_confs: list[float] = []
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)

    for im in images_bgr:
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        m = max(h, w)
        if m > 1280:
            scale = 1280 / m
            gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))))
        sharp = cv2.filter2D(gray, -1, kernel)

        for variant in (gray, sharp):
            lines, confs = _run_paddle_on_gray(ocr, variant)
            all_lines.extend(lines)
            all_confs.extend(confs)

    text = "\n".join(all_lines)
    avg = sum(all_confs) / len(all_confs) if all_confs else 0.0
    return text.strip(), avg


def _tile_variant_pass(ocr: Any, images_bgr: list[np.ndarray]) -> tuple[str, float]:
    """Dense tiles + preprocess variants — slower; used when full-frame text is thin."""
    ocr_inputs: list[np.ndarray] = []
    for im in images_bgr:
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        m = max(h, w)
        if m > 960:
            scale = 960 / m
            gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))))
        ocr_inputs.extend(_preprocess_variants(im))
        ocr_inputs.extend(_tiles(gray))

    all_lines: list[str] = []
    all_confs: list[float] = []
    t0 = time.perf_counter()
    for img in ocr_inputs:
        lines, confs = _run_paddle_on_gray(ocr, img)
        all_lines.extend(lines)
        all_confs.extend(confs)
    wall_s = time.perf_counter() - t0
    text = "\n".join(all_lines)
    avg = sum(all_confs) / len(all_confs) if all_confs else 0.0
    return text.strip(), avg, wall_s


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


def _tesseract_gray_variants(gray: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Several preprocess paths — labels are for diagnostics only."""
    h, w = gray.shape[:2]
    if max(h, w) < 960:
        gray = cv2.resize(
            gray,
            (max(1, int(w * 2.0)), max(1, int(h * 2.0))),
            interpolation=cv2.INTER_CUBIC,
        )
    out: list[tuple[str, np.ndarray]] = [("gray", gray)]
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("otsu", otsu))
    at = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )
    out.append(("adaptive", at))
    return out


def _try_tesseract_text(images_bgr: list[np.ndarray]) -> tuple[str, float, dict[str, Any]]:
    """Fallback OCR when Paddle is unavailable or nearly empty (needs ``pytesseract`` + Tesseract binary)."""
    diag: dict[str, Any] = {}
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        diag["error"] = "pytesseract_not_installed"
        logger.warning(
            "%sTesseract fallback skipped — install pytesseract + Pillow (included in pip install -e \".[dev]\")",
            trace_prefix(),
        )
        return "", 0.0, diag

    if settings.tesseract_cmd is not None:
        pytesseract.pytesseract.tesseract_cmd = str(settings.tesseract_cmd.expanduser().resolve())
        diag["tesseract_cmd"] = str(settings.tesseract_cmd)

    try:
        ver = pytesseract.get_tesseract_version()
        diag["tesseract_version"] = str(ver)
    except Exception as e:
        diag["error"] = "tesseract_binary_missing_or_failed"
        diag["detail"] = str(e)
        logger.warning(
            "%sTesseract executable not found or failed (%s). Add it to PATH or set SMART_FRIDGE_TESSERACT_CMD "
            "to tesseract.exe (Windows: winget install UB-Mannheim.TesseractOCR).",
            trace_prefix(),
            e,
        )
        return "", 0.0, diag

    collected: list[str] = []
    for im in images_bgr[:3]:
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        for vlabel, arr in _tesseract_gray_variants(gray):
            pil = Image.fromarray(arr)
            for psm in ("--psm 6", "--psm 11"):
                try:
                    txt = pytesseract.image_to_string(
                        pil,
                        lang=settings.tesseract_langs.replace(" ", ""),
                        config=psm,
                    )
                    if txt and txt.strip():
                        collected.append(txt.strip())
                except Exception:
                    logger.debug("%stesseract %s %s failed", trace_prefix(), vlabel, psm, exc_info=True)

    # Dedupe similar chunks while keeping order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in collected:
        key = c[:200]
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    text = "\n".join(uniq).strip()
    diag["variants_tried"] = len(collected)
    if not text:
        diag["error"] = "no_text_extracted"
        logger.warning(
            "%sTesseract ran but read no text — improve lighting, hold the label flat, or install Paddle on Python 3.11–3.12",
            trace_prefix(),
        )
        return "", 0.0, diag

    conf = 0.42 if len(text) >= 12 else 0.28
    diag["chars"] = len(text)
    return text, conf, diag


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


def _confidence_score(
    *,
    date_conf: float,
    paddle_conf: float,
    combined_len: int,
    barcode: Optional[str],
    product_guess: Optional[str],
    had_ocr_engine: bool,
) -> float:
    """Blend date-parse score with OCR trust; avoid flat zero when partial signals exist.

    Floors ensure a decoded barcode or a substantive product line can reach **medium** tier
    (>= CONFIDENCE_MEDIUM) without requiring a parsed expiry date — previously product-only
    reads topped out ~0.4 and stayed "low" forever.
    """
    text_density = min(1.0, combined_len / 200.0)
    paddle_eff = paddle_conf
    if paddle_eff <= 0.02 and combined_len >= 15:
        paddle_eff = max(paddle_eff, 0.25 + 0.35 * text_density)
    elif not had_ocr_engine and combined_len >= 20:
        paddle_eff = max(paddle_eff, 0.18 + 0.25 * text_density)

    base = date_conf * (0.42 + 0.58 * min(1.0, max(paddle_eff, 0.12)))

    pg = (product_guess or "").strip()
    alpha_n = sum(1 for c in pg if c.isalpha())

    # Decoded barcode is a strong product anchor — always allow medium tier.
    if barcode:
        base = max(base, 0.58 if combined_len >= 8 else 0.55)

    if len(pg) >= 5:
        base = max(base, 0.18 + min(0.22, len(pg) / 120.0))

    if date_conf <= 0.01 and combined_len > 80 and paddle_eff > 0.2:
        base = max(base, 0.12)

    # Clear product-like line from OCR → medium without needing date_conf.
    if alpha_n >= 10 and len(pg) >= 12:
        base = max(base, 0.54 + min(0.14, alpha_n / 180.0))
    elif alpha_n >= 6 and len(pg) >= 8:
        base = max(base, 0.50 + min(0.06, alpha_n / 200.0))

    if had_ocr_engine and combined_len >= 45 and alpha_n >= 10:
        base = max(base, 0.52)

    return float(min(1.0, max(0.0, base)))


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
    paddle_conf = 0.0
    ocr_wall_s = 0.0

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

    ocr = _get_paddle_ocr()
    combined = ""
    used_full_then_tiles = False

    if ocr is not None:
        t_o0 = time.perf_counter()
        full_text, full_conf = _full_frame_pass(ocr, images_bgr)
        combined = full_text
        paddle_conf = full_conf
        if len(combined) < 35:
            tile_text, tile_conf, tile_wall = _tile_variant_pass(ocr, images_bgr)
            ocr_wall_s = tile_wall
            if tile_text:
                combined = (combined + "\n" + tile_text).strip()
                paddle_conf = max(paddle_conf, tile_conf)
            used_full_then_tiles = True
        timing_ms["paddle_ocr"] = (time.perf_counter() - t_o0) * 1000.0
        stages["ocr_strategy"] = "full_frame_plus_tiles" if used_full_then_tiles else "full_frame"
    else:
        timing_ms["paddle_ocr"] = 0.0
        stages["ocr_strategy"] = "none"

    tess_merged = False
    tess_diag: dict[str, Any] = {}
    if ocr is None or len(combined.strip()) < 15:
        tess_text, tess_conf, tess_diag = _try_tesseract_text(images_bgr)
        stages["tesseract_diag"] = tess_diag
        if tess_text.strip():
            tess_merged = True
            combined = (combined + "\n" + tess_text).strip() if combined.strip() else tess_text.strip()
            paddle_conf = max(paddle_conf, tess_conf)
            stages["ocr_tesseract_supplement"] = True
            if ocr is not None:
                stages["ocr_strategy"] = str(stages.get("ocr_strategy", "")) + "+tesseract"
            else:
                stages["ocr_strategy"] = "tesseract"

    if tess_merged and ocr is not None:
        stages["ocr_engine"] = "paddleocr+tesseract"
    elif tess_merged:
        stages["ocr_engine"] = "tesseract"
    else:
        stages["ocr_engine"] = "paddleocr" if ocr is not None else "none"

    had_ocr_engine = ocr is not None or tess_merged

    expanded_lines = expand_ocr_lines(combined)
    lines = [ln for ln in expanded_lines if len(ln.strip()) > 2]

    t_parse0 = time.perf_counter()
    parsed_list = date_parse.parse_dates_from_text(combined)
    timing_ms["date_parse_heuristic"] = (time.perf_counter() - t_parse0) * 1000.0
    stages["parsed_dates"] = [(d.isoformat(), c, s) for d, c, s in parsed_list]

    date_snippets = {s for _, _, s in parsed_list}

    best_date: Optional[date] = None
    date_conf = 0.0
    raw_snip: Optional[str] = None
    if parsed_list:
        best_date, date_conf, raw_snip = parsed_list[0]

    date_type = date_parse.infer_date_type_from_context(combined)
    if date_type == DateType.unknown and raw_snip:
        date_type = date_parse.infer_date_type_from_context(raw_snip)

    norm = best_date.isoformat() if best_date else None

    product_guess, product_ranked, machine_read_preview = pick_product_name(expanded_lines, date_snippets)
    stages["product_line_scores"] = [(round(s, 2), t[:120]) for s, t in product_ranked[:16]]
    stages["machine_read_preview"] = machine_read_preview

    conf = _confidence_score(
        date_conf=date_conf,
        paddle_conf=paddle_conf,
        combined_len=len(combined),
        barcode=barcode,
        product_guess=product_guess,
        had_ocr_engine=had_ocr_engine,
    )

    stages["confidence_breakdown"] = {
        "date_parse": date_conf,
        "paddle_avg": paddle_conf,
        "text_chars": len(combined),
        "ocr_wall_tile_s": ocr_wall_s,
    }

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
        "%svision pipeline: total=%.2fs paddle_conf=%.3f date_conf=%.3f conf=%.3f tier=%s chars=%d barcode=%s",
        trace_prefix(),
        elapsed,
        paddle_conf,
        date_conf,
        float(conf),
        tier,
        len(combined),
        barcode,
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
