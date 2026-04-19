"""Optional LM Studio / OpenAI-compatible multimodal fallback."""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.app.config import settings
from backend.app.models.entities import DateType
from backend.app.modules import date_parse
from backend.app.modules.vision_pipeline import PipelineResult

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
        logger.debug("could not parse VLM JSON from response", exc_info=True)
        return None


async def describe_product_and_expiry(image_paths: list[Path]) -> Optional[dict[str, Any]]:
    """
    Call a local VLM when PaddleOCR + heuristics are insufficient.
    Expects an OpenAI-compatible `/v1/chat/completions` with vision content support.
    """
    if not settings.vlm_enabled or not settings.vlm_endpoint:
        return None
    parts: list[dict[str, Any]] = []
    for p in image_paths[:3]:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
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
    try:
        logger.info("VLM request to %s (%d images)", settings.vlm_endpoint, len(parts))
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(settings.vlm_endpoint, json=body)
            if not r.is_success:
                logger.warning("VLM HTTP %s: %s", r.status_code, r.text[:200])
                return None
            return r.json()
    except Exception:
        logger.exception("VLM request failed")
        return None


def merge_pipeline_with_vlm(
    base: PipelineResult,
    api_json: Optional[dict[str, Any]],
) -> PipelineResult:
    """Blend specialist OCR pipeline with VLM hints when API returned usable JSON."""
    if not api_json:
        return base

    extracted = _extract_json_from_chat_response(api_json)
    if not extracted:
        logger.info("VLM response had no parsable JSON; keeping OCR-only result")
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

            # Accept YYYY-MM-DD
            d = date_cls.fromisoformat(expiry_iso.strip()[:10])
            norm = d.isoformat()
            raw_txt = expiry_iso
            conf = max(conf, 0.55)
        except ValueError:
            logger.debug("VLM expiry_iso not parseable: %s", expiry_iso)

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
        "merged VLM hints: product=%s expiry=%s conf=%.2f",
        name,
        norm,
        conf,
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
