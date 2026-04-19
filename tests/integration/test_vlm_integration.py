"""Integration tests for the VLM HTTP path (mocked LM Studio-compatible server)."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import httpx
import pytest
import respx

from backend.app.config import settings


@pytest.fixture
def vlm_on(monkeypatch):
    ep = "http://vlm-test.local/v1/chat/completions"
    monkeypatch.setattr(settings, "vlm_enabled", True)
    monkeypatch.setattr(settings, "vlm_confidence_below", 1.0)
    monkeypatch.setattr(settings, "vlm_endpoint", ep)
    monkeypatch.setattr(settings, "vlm_log_preview_chars", 2000)
    return ep


@pytest.mark.integration
@respx.mock
def test_vlm_called_when_enabled_low_confidence(client, vlm_on, caplog):
    """Low OCR confidence triggers VLM; logs contain timing and response preview."""
    import logging

    caplog.set_level(logging.INFO)

    respx.post(vlm_on).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"product": "Mock Milk", "expiry_iso": "2032-08-01", "date_type": "best_before"}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
        )
    )

    from tests.conftest import make_test_jpeg_bytes

    jpeg = make_test_jpeg_bytes(text="")
    with patch("backend.app.modules.vision_pipeline._get_paddle_ocr", return_value=None):
        with patch("backend.app.modules.vision_pipeline.cv2.imread") as imread:
            import numpy as np

            imread.return_value = np.zeros((40, 40, 3), dtype=np.uint8)
            r = client.post(
                "/api/scan/upload",
                files=[("files", ("z.jpg", BytesIO(jpeg), "image/jpeg"))],
            )

    assert r.status_code == 200
    data = r.json()
    assert data["product_guess"]["canonical_name"] == "Mock Milk"
    assert data["normalized_date"] == "2032-08-01"

    joined = caplog.text
    assert "VLM POST" in joined or "VLM OK" in joined
    assert "trace=" in joined
    assert "VLM merge applied" in joined or "After VLM" in joined


@pytest.mark.integration
@respx.mock
def test_vlm_http_error_logged(client, vlm_on, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    respx.post(vlm_on).mock(return_value=httpx.Response(500, text="boom"))

    from tests.conftest import make_test_jpeg_bytes

    jpeg = make_test_jpeg_bytes()
    with patch("backend.app.modules.vision_pipeline._get_paddle_ocr", return_value=None):
        with patch("backend.app.modules.vision_pipeline.cv2.imread") as imread:
            import numpy as np

            imread.return_value = np.zeros((30, 30, 3), dtype=np.uint8)
            r = client.post(
                "/api/scan/upload",
                files=[("files", ("z.jpg", BytesIO(jpeg), "image/jpeg"))],
            )

    assert r.status_code == 200
    assert "VLM HTTP 500" in caplog.text
