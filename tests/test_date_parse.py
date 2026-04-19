from datetime import date

from backend.app.modules import date_parse
from backend.app.modules.date_parse import infer_date_type_from_context
from backend.app.models.entities import DateType


def test_iso_date_high_confidence():
    rows = date_parse.parse_dates_from_text("Made in EU Best before 2030-04-19 factory")
    assert rows
    assert rows[0][0] == date(2030, 4, 19)


def test_infer_use_by():
    assert infer_date_type_from_context("USE BY 12/05/26") == DateType.use_by


def test_infer_best_before():
    assert infer_date_type_from_context("BB 12 MAY 2026") == DateType.best_before


def test_ambiguous_numeric():
    rows = date_parse.parse_dates_from_text("05/06/26", locale_day_first=True)
    assert rows
    assert rows[0][0].month == 6
    assert rows[0][0].day == 5
