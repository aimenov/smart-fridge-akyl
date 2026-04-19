"""PaddleOCR result parsing (2.x nested layout vs 3.x rec_texts)."""

from types import SimpleNamespace

from backend.app.modules.vision_pipeline import _extract_text_lines_from_paddle


def test_paddle_v2_nested_lines():
    page = [[[[0, 0], [1, 1], [2, 2], [3, 3]], ("Nestle", 0.92)], [[[0, 0], [1, 1], [2, 2], [3, 3]], ("Привет", 0.88)]]
    lines, confs = _extract_text_lines_from_paddle([page])
    assert lines == ["Nestle", "Привет"]
    assert len(confs) == 2


def test_paddle_v3_rec_texts_dict():
    result = [{"rec_texts": ["A", "Б"], "rec_scores": [0.9, 0.8]}]
    lines, confs = _extract_text_lines_from_paddle(result)
    assert lines == ["A", "Б"]


def test_paddle_v3_ocr_result_object():
    obj = SimpleNamespace(rec_texts=["X", "Ю"], rec_scores=[0.99, 0.77])
    lines, confs = _extract_text_lines_from_paddle([obj])
    assert "Ю" in lines
