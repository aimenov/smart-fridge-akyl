"""Confidence floors for scan pipeline (product without date must reach medium tier)."""

from backend.app.modules.inventory_service import CONFIDENCE_MEDIUM
from backend.app.modules.vision_pipeline import _confidence_score


def test_barcode_only_hits_medium_tier():
    c = _confidence_score(
        date_conf=0.0,
        paddle_conf=0.0,
        combined_len=0,
        barcode="5901234123457",
        product_guess=None,
        had_ocr_engine=False,
    )
    assert c >= CONFIDENCE_MEDIUM


def test_product_line_without_date_hits_medium_tier():
    c = _confidence_score(
        date_conf=0.0,
        paddle_conf=0.35,
        combined_len=180,
        barcode=None,
        product_guess="Organic Greek Style Yogurt 450g",
        had_ocr_engine=True,
    )
    assert c >= CONFIDENCE_MEDIUM


def test_short_guess_stays_below_high():
    c = _confidence_score(
        date_conf=0.95,
        paddle_conf=0.9,
        combined_len=400,
        barcode=None,
        product_guess="AB",
        had_ocr_engine=True,
    )
    assert c < 0.99
