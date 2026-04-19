"""Optional LM Studio / OpenAI-compatible multimodal fallback."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.app.config import settings
from backend.app.models.entities import DateType
from backend.app.modules import date_parse
from backend.app.modules.vision_pipeline import PipelineResult
from backend.app.observability import json_preview, redact_openai_style_body, trace_prefix

logger = logging.getLogger(__name__)


def _extract_json_from_chat_response(api_json: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Parse JSON from OpenAI-compatible chat completion body."""
    try:
        choices = api_json.get("choices") or []
        if not choices:
            return None
        msg = (choices[0].get("message") or {}).get("content")
        if not msg or not isinstance(msg, str):
            return None
        msg = msg.strip()
        if msg.startswith("```"):
            msg = re.sub(r"^```(?:json)?\s*", "", msg)
            msg = re.sub(r"\s*```$", "", msg)
        return json.loads(msg)
    except Exception:
        logger.debug("%scould not parse VLM JSON from response", trace_prefix(), exc_info=True)
        return None


def _summarize_vlm_response(api_json: dict[str, Any]) -> dict[str, Any]:
    """Lightweight summary for INFO logs (model, finish_reason, content length)."""
    out: dict[str, Any] = {}
    choice0 = (api_json.get("choices") or [{}])[0]
    out["finish_reason"] = choice0.get("finish_reason")
    msg = choice0.get("message") or {}
    out["role"] = msg.get("role")
    content = msg.get("content")
    if isinstance(content, str):
        out["assistant_content_chars"] = len(content)
        out["assistant_preview"] = content[:400].replace("\n", "\\n") + (
            "..." if len(content) > 400 else ""
        )
    usage = api_json.get("usage")
    if isinstance(usage, dict):
        out["usage"] = usage
    out["model"] = api_json.get("model")
    return out


async def describe_product_and_expiry(image_paths: list[Path]) -> Optional[dict[str, Any]]:
    """
    Call a local VLM when PaddleOCR + heuristics are insufficient.
    Expects an OpenAI-compatible `/v1/chat/completions` with vision content support.
    """
    if not settings.vlm_enabled or not settings.vlm_endpoint:
        logger.debug("%sVLM skipped (disabled or empty endpoint)", trace_prefix())
        return None
    parts: list[dict[str, Any]] = []
    raw_bytes = 0
    for p in image_paths[:3]:
        blob = p.read_bytes()
        raw_bytes += len(blob)
        b64 = base64.b64encode(blob).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )
    body = {
        "model": "local",
        "messages": [
            {
                "role": "user",
                "content": parts
                + [
                    {
                        "type": "text",
                        "text": (
                            "Identify packaged food product name and expiry date from images. "
                            'Reply JSON only: {"product": str, "expiry_iso": str|null, "date_type": str}'
                        ),
                    }
                ],
            }
        ],
        "temperature": 0.2,
    }

    redacted = redact_openai_style_body(body)
    preview_limit = settings.vlm_log_preview_chars
    logger.info(
        "%sVLM POST %s | images=%d raw_bytes≈%d | request_preview=%s",
        trace_prefix(),
        settings.vlm_endpoint,
        len(parts),
        raw_bytes,
        json_preview(redacted, max_chars=preview_limit),
    )

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(settings.vlm_endpoint, json=body)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if not r.is_success:
                logger.warning(
                    "%sVLM HTTP %s in %.1fms | body=%s",
                    trace_prefix(),
                    r.status_code,
                    elapsed_ms,
                    r.text[:500],
                )
                return None

            api_json = r.json()
            summary = _summarize_vlm_response(api_json)
            logger.info(
                "%sVLM OK in %.1fms | summary=%s | response_preview=%s",
                trace_prefix(),
                elapsed_ms,
                json_preview(summary, max_chars=800),
                json_preview(api_json, max_chars=preview_limit),
            )
            return api_json
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.exception("%sVLM request failed after %.1fms", trace_prefix(), elapsed_ms)
        return None


def merge_pipeline_with_vlm(
    base: PipelineResult,
    api_json: Optional[dict[str, Any]],
) -> PipelineResult:
    """Blend specialist OCR pipeline with VLM hints when API returned usable JSON."""
    if not api_json:
        logger.info("%sVLM merge skipped (no API payload)", trace_prefix())
        return base

    extracted = _extract_json_from_chat_response(api_json)
    if not extracted:
        logger.info(
            "%sVLM merge: no structured JSON in assistant message; keeping OCR-only | raw_keys=%s",
            trace_prefix(),
            list(api_json.keys()),
        )
        return base

    product = extracted.get("product")
    expiry_iso = extracted.get("expiry_iso")
    dt_raw = extracted.get("date_type")

    stages = dict(base.stages)
    stages["vlm_extracted"] = extracted

    name = base.product_name_guess
    if isinstance(product, str) and product.strip():
        name = product.strip()[:512]

    norm = base.normalized_date
    raw_txt = base.raw_date_text
    dtype = base.date_type
    conf = base.confidence

    if isinstance(expiry_iso, str) and expiry_iso.strip():
        try:
            from datetime import date as date_cls

            d = date_cls.fromisoformat(expiry_iso.strip()[:10])
            norm = d.isoformat()
            raw_txt = expiry_iso
            conf = max(conf, 0.55)
        except ValueError:
            logger.debug("%sVLM expiry_iso not parseable: %s", trace_prefix(), expiry_iso)

    if isinstance(dt_raw, str) and dt_raw.strip():
        key = dt_raw.strip().lower().replace(" ", "_").replace("-", "_")
        try:
            dtype = DateType(key)
        except ValueError:
            parsed = date_parse.infer_date_type_from_context(dt_raw)
            if parsed != DateType.unknown:
                dtype = parsed

    stages["tier_after_vlm"] = "medium" if conf >= 0.50 else "low"

    logger.info(
        "%sVLM merge applied: product=%r expiry=%s date_type=%s confidence %.3f -> %.3f | extracted=%s",
        trace_prefix(),
        name,
        norm,
        dtype.value if dtype else None,
        base.confidence,
        min(1.0, conf),
        json_preview(extracted, max_chars=600),
    )

    return PipelineResult(
        barcode=base.barcode,
        raw_ocr_text=base.raw_ocr_text,
        date_type=dtype,
        raw_date_text=raw_txt,
        normalized_date=norm,
        confidence=min(1.0, conf),
        stages=stages,
        product_name_guess=name,
    )
