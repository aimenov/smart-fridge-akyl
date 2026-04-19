from backend.app.models.entities import DateType
from backend.app.modules.vision_pipeline import PipelineResult
from backend.app.modules.vlm_fallback import merge_pipeline_with_vlm


def test_merge_pipeline_with_vlm_updates_fields():
    base = PipelineResult(
        barcode=None,
        raw_ocr_text="",
        date_type=DateType.unknown,
        raw_date_text=None,
        normalized_date=None,
        confidence=0.2,
        stages={},
        product_name_guess="old",
    )
    api_json = {
        "choices": [
            {
                "message": {
                    "content": '{"product": "New Name", "expiry_iso": "2031-07-15", "date_type": "best_before"}'
                }
            }
        ]
    }
    out = merge_pipeline_with_vlm(base, api_json)
    assert out.product_name_guess == "New Name"
    assert out.normalized_date == "2031-07-15"
    assert out.confidence >= 0.55


def test_merge_keeps_base_when_json_invalid():
    base = PipelineResult(
        barcode="111",
        raw_ocr_text="x",
        date_type=DateType.expiry,
        raw_date_text="1",
        normalized_date="2030-01-01",
        confidence=0.9,
        stages={},
        product_name_guess="keep",
    )
    out = merge_pipeline_with_vlm(base, {"choices": []})
    assert out.product_name_guess == "keep"
    assert out.confidence == 0.9
