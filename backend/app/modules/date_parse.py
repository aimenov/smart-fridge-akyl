from __future__ import annotations

import re
from datetime import date
from typing import Optional

from backend.app.models.entities import DateType


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_int_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _two_digit_year(y: int, pivot: int = 2010) -> int:
    if y >= 100:
        return y
    return y + (2000 if y + 2000 >= pivot else 1900)


def infer_date_type_from_context(text: str) -> DateType:
    t = text.upper()
    if re.search(r"\bUSE\s*BY\b", t) or re.search(r"\bU\s*BY\b", t):
        return DateType.use_by
    if re.search(r"\bBEST\s*BEFORE\b|\bBB\b|\bB\.B\.|\bBBE\b", t):
        return DateType.best_before
    if re.search(r"\bEXP\b|\bEXP\.\b|\bEXP\s*DATE\b|\bEX:\b", t):
        return DateType.expiry
    if re.search(r"\bPACK(?:ED)?\s*ON\b|\bPKG\b", t):
        return DateType.packed_on
    if re.search(r"\bPROD(?:UCED)?\s*ON\b|\bMFR\b|\bMAN\b", t):
        return DateType.produced_on
    return DateType.unknown


def parse_dates_from_text(text: str, *, locale_day_first: bool = True) -> list[tuple[date, float, str]]:
    """
    Return list of (parsed_date, confidence 0..1, matched_snippet).
    Ambiguous dd/mm vs mm/dd uses locale_day_first when both <= 12.
    """
    out: list[tuple[date, float, str]] = []
    if not text:
        return out

    # ISO-like 2026-04-19
    for m in re.finditer(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        parsed = _parse_int_date(y, mo, d)
        if parsed:
            out.append((parsed, 0.95, m.group(0)))

    # dd.mm.yyyy or dd/mm/yyyy (also yy)
    for m in re.finditer(
        r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b",
        text,
    ):
        a, b, y_raw = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y_raw)
        if len(y_raw) == 2:
            y = _two_digit_year(y)
        if locale_day_first:
            d, mo = a, b
        else:
            mo, d = a, b
        parsed = _parse_int_date(y, mo, d)
        if parsed:
            amb = 0.75 if a <= 12 and b <= 12 and a != b else 0.88
            out.append((parsed, amb, m.group(0)))

    # 12 MAY 26 / 12-May-2026
    for m in re.finditer(
        r"\b(\d{1,2})[\s\-]+([A-Za-z]{3,9})[\s\-]+(\d{2,4})\b",
        text,
    ):
        d = int(m.group(1))
        mon_raw = m.group(2).lower()[:3]
        y_raw = m.group(3)
        mo = _MONTHS.get(mon_raw)
        if not mo:
            continue
        y = int(y_raw)
        if len(y_raw) == 2:
            y = _two_digit_year(y)
        parsed = _parse_int_date(y, mo, d)
        if parsed:
            out.append((parsed, 0.90, m.group(0)))

    # De-dupe same date keeping highest confidence
    best: dict[date, tuple[float, str]] = {}
    for dt, conf, snip in out:
        prev = best.get(dt)
        if not prev or conf > prev[0]:
            best[dt] = (conf, snip)
    return sorted(((d, best[d][0], best[d][1]) for d in best), key=lambda x: (-x[1], x[0]))
