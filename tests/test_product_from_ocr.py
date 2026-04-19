"""Product name extraction from noisy OCR."""

from backend.app.modules.product_from_ocr import (
    expand_ocr_lines,
    pick_product_name,
    score_product_line,
    title_similarity,
)


def test_nestle_nan_multiline_merge_matches_expected_title():
    """Regression: Lat brand + NAN + Cyrillic descriptor + stage on separate OCR lines."""
    txt = """Nestle
NAN
На козьем молоке
3"""
    lines = expand_ocr_lines(txt)
    name, _, _ = pick_product_name(lines, set())
    assert name is not None
    expected = "Nestle NAN На козьем молоке 3"
    assert title_similarity(name, expected) >= 0.88


def test_prefers_clean_brand_over_junk_wall():
    noise = "| _ ~ --- ...."
    lines = expand_ocr_lines(
        "\n".join(
            [
                noise,
                "Nestlé condensed milk",
                "400 г жирность",
                noise * 5,
            ],
        ),
    )
    name, ranked, preview = pick_product_name(lines, set())
    assert name is not None
    assert "Nestlé" in name or "Nestle" in name or "condensed" in name.lower()
    assert preview
    assert noise.strip()[:5] not in preview


def test_scores_cyrillic_line():
    s = "Молоко пастеризованное 3.2%"
    assert score_product_line(s) > score_product_line("| _ ~ — — —")


def test_filters_weight_only_row():
    lines = ["|||", "200 г", "Product Name Here"]
    name, ranked, _ = pick_product_name(lines, set())
    assert name is not None
    assert "Product" in name


def test_date_snippet_excluded():
    lines = ["Best before 2030-12-31", "Real Product Title"]
    name, _, _ = pick_product_name(lines, {"2030-12-31"})
    assert name and "Real Product" in name
