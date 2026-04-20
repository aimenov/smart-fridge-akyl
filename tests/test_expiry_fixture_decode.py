from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from backend.app.modules.vision_pipeline import run_pipeline


def _expiry_fixture_paths() -> list[Path]:
    root = Path(__file__).resolve().parent / "fixtures" / "expiry"
    if not root.is_dir():
        return []
    exts = {".jfif", ".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts)


def expected_date_from_stem(path: Path) -> str | None:
    s = path.stem.strip()
    parts = [p for p in re.split(r"[^0-9]", s) if p]
    if len(parts) < 3:
        return None

    # Accept DD-MM-YYYY or YYYY-MM-DD.
    if len(parts[0]) == 4:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:
            y = 2000 + y

    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


@pytest.mark.parametrize("image_path", _expiry_fixture_paths())
def test_expiry_image_matches_filename_date(image_path: Path):
    expected = expected_date_from_stem(image_path)
    assert expected is not None, f"fixture name must encode a date: {image_path.name}"

    result = run_pipeline([image_path])
    assert result.normalized_date == expected, (
        f"{image_path.name}: expected {expected}, got {result.normalized_date!r}; "
        f"raw={result.raw_date_text!r} stages_expiry={result.stages.get('expiry')}"
    )


def test_expiry_fixture_directory_exists_or_skip_notice():
    assert _expiry_fixture_paths(), (
        "Add JPEG/PNG expiry crops under tests/fixtures/expiry/ named with the expected date "
        "(e.g. 28-04-2026.jfif)."
    )

