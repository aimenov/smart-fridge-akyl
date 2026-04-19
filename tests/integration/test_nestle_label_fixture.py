"""End-to-end OCR on the Nestlé NAN fixture image (optional — requires image + OCR engines)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.modules.product_from_ocr import title_similarity
from backend.app.modules.vision_pipeline import run_pipeline

EXPECTED_TITLE = "Nestle NAN На козьем молоке 3"

FIXTURE_NAMES = (
    "Nestle NAN На козьем молоке 3.jpg",
    "Nestle NAN На козьем молоке 3.jpeg",
    "Nestle NAN На козьем молоке 3.jfif",
    "Nestle NAN На козьем молоке 3.png",
    "nestle_nan_goat.jpg",
)


def _resolve_fixture() -> Path | None:
    here = Path(__file__).resolve().parent
    bases = (here / "fixtures", here)
    for name in FIXTURE_NAMES:
        for base in bases:
            p = base / name
            if p.is_file():
                return p
    # Allow the same photo placed loose under tests/integration/
    for base in bases:
        for p in sorted(base.glob("*Nestle*NAN*")):
            if p.is_file() and p.suffix.lower() in (
                ".jpg",
                ".jpeg",
                ".jfif",
                ".png",
                ".webp",
            ):
                return p
    return None


@pytest.mark.integration
def test_fixture_image_product_name_close_to_expected():
    path = _resolve_fixture()
    if path is None:
        pytest.skip(
            "Drop tests/integration/fixtures/Nestle NAN На козьем молоке 3.jpg into the repo "
            "(see fixtures/README.txt)",
        )

    result = run_pipeline([path])
    if result.stages.get("error") == "no_images_loaded":
        pytest.fail(f"could not decode image bytes at {path}")

    if not (result.raw_ocr_text or "").strip():
        pytest.skip(
            "OCR produced no text — install OCR deps: pip install -e \".[dev]\" and the Tesseract binary "
            "(Paddle needs Python 3.11–3.12 on Windows; see README).",
        )

    guess = (result.product_name_guess or "").strip()
    assert guess, "pipeline returned empty product_name_guess despite OCR text"

    ratio = title_similarity(guess, EXPECTED_TITLE)
    assert ratio >= 0.62, (
        f"product_name_guess too far from expected title.\n"
        f"  expected (approx): {EXPECTED_TITLE!r}\n"
        f"  got:               {guess!r}\n"
        f"  similarity:        {ratio:.3f}\n"
        f"  stages:            {result.stages.get('product_line_scores', [])[:5]!r}"
    )
