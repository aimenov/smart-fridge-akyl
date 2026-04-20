"""Golden-barcode regression: filenames under ``tests/fixtures/barcode`` are digit-only GTIN/EAN stems."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.modules.barcode_gtin import normalize_barcode_to_gtin14
from backend.app.modules.vision_pipeline import run_pipeline


def _barcode_fixture_paths() -> list[Path]:
    root = Path(__file__).resolve().parent / "fixtures" / "barcode"
    if not root.is_dir():
        return []
    exts = {".jfif", ".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts)


def expected_gtin14_from_stem(path: Path) -> str | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    if len(digits) < 8:
        return None
    gn = normalize_barcode_to_gtin14(digits)
    return gn.normalized_gtin_14


@pytest.mark.parametrize("image_path", _barcode_fixture_paths())
def test_barcode_image_matches_filename_gtin(image_path: Path):
    expected = expected_gtin14_from_stem(image_path)
    assert expected is not None, f"fixture name must encode >=8 digits: {image_path.name}"

    result = run_pipeline([image_path], run_expiry=False)
    assert result.normalized_gtin_14 == expected, (
        f"{image_path.name}: expected normalized GTIN-14 {expected}, "
        f"got {result.normalized_gtin_14!r}; decoded={result.stages.get('barcodes_decoded')}"
    )


def test_fixture_directory_exists_or_skip_notice():
    assert _barcode_fixture_paths(), (
        "Add JPEG/PNG barcode crops under tests/fixtures/barcode/ named with the expected numeric code "
        "(e.g. 7613287295798.jfif)."
    )
