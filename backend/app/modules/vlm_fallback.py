"""Optional LM Studio / OpenAI-compatible multimodal fallback (stub)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.app.config import settings


async def describe_product_and_expiry(image_paths: list[Path]) -> Optional[dict[str, Any]]:
    """
    Call a local VLM when PaddleOCR + heuristics are insufficient.
    Expects an OpenAI-compatible `/v1/chat/completions` with vision content support.
    """
    if not settings.vlm_enabled or not settings.vlm_endpoint:
        return None
    # Minimal placeholder payload — extend with real multimodal messages for your runtime.
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
                            "Reply JSON: {\"product\": str, \"expiry_iso\": str|null, \"date_type\": str}"
                        ),
                    }
                ],
            }
        ],
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(settings.vlm_endpoint, json=body)
            if not r.is_success:
                return None
            return r.json()
    except Exception:
        return None
