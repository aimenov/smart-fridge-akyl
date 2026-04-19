"""Pick a plausible product name from noisy multilingual OCR (Latin / Cyrillic / Kazakh Cyrillic)."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

# Letters from common scripts on packaging (Latin, Cyrillic incl. Kazakh)
_LETTER_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo"})

# Cyrillic block used for mixed-script product titles (RU/KZ product copy next to Latin brands)
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")


def _is_letter(ch: str) -> bool:
    if len(ch) != 1:
        return False
    return unicodedata.category(ch) in _LETTER_CATEGORIES


def _letter_count(s: str) -> int:
    return sum(1 for c in s if _is_letter(c))


def _normalize_for_compare(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s.strip()).casefold()
    return s


def _normalize_display_line(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[|_]{2,}", " ", s)
    s = re.sub(r"[—–\-]{2,}", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def expand_ocr_lines(combined: str) -> list[str]:
    """Split OCR blob into line candidates; split pipe-heavy rows into fragments."""
    out: list[str] = []
    for ln in combined.splitlines():
        ln = ln.strip()
        if len(ln) < 2:
            continue
        if ln.count("|") >= 2 and len(ln) > 35:
            for part in re.split(r"\s*\|\s*", ln):
                p = part.strip()
                if len(p) >= 4:
                    out.append(p)
            continue
        out.append(ln)
    return out


def dedupe_lines(lines: list[str]) -> list[str]:
    """Drop exact duplicates (after normalize) while keeping order."""
    seen: set[str] = set()
    uniq: list[str] = []
    for ln in lines:
        key = _normalize_for_compare(ln)
        if len(key) < 3:
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ln.strip())
    return uniq


def _drop_noisy_lines(lines: list[str]) -> list[str]:
    """Remove lines that are mostly punctuation / OCR junk."""
    kept: list[str] = []
    for ln in lines:
        L = len(ln)
        if L < 3:
            continue
        letters = _letter_count(ln)
        if letters == 0:
            continue
        letter_ratio = letters / L
        # Mostly decorative / broken OCR
        punct_like = sum(1 for c in ln if c in "|_~`^•·…\\/—–-" or unicodedata.category(c).startswith("P"))
        if punct_like / L > 0.42 and letter_ratio < 0.18:
            continue
        if letter_ratio < 0.08 and L > 25:
            continue
        # Single repeating garbage
        if len(set(ln.replace(" ", ""))) <= 4 and L > 30:
            continue
        kept.append(ln)
    return kept


_WEIGHT_ONLY = re.compile(
    r"^\s*\d+[.,]?\d*\s*(g|г|kg|кг|ml|мл|l|л| oz)\s*$",
    re.IGNORECASE | re.UNICODE,
)
_BATCH_CODE = re.compile(r"^[A-Z0-9]{10,}$")
_LOTS_OF_SINGLE_CHARS = re.compile(r"(\b\w\b\s*){8,}")


def score_product_line(line: str) -> float:
    """Higher = more likely a product / brand line (not nutrition tables, codes, junk)."""
    s = _normalize_display_line(line)
    if len(s) < 3:
        return -999.0
    if _WEIGHT_ONLY.match(s):
        return -80.0
    if _BATCH_CODE.match(s.replace(" ", "")):
        return -40.0

    letters = _letter_count(s)
    L = len(s)
    letter_ratio = letters / max(L, 1)

    if letters < 3:
        return -100.0

    score = letter_ratio * 55.0 + min(letters, 48) * 0.85

    words = [w for w in re.split(r"\s+", s) if w]
    word_n = len(words)
    if word_n >= 2:
        score += 18.0
    if word_n >= 5:
        # Long nutrition tables — but multilingual titles can be wordy; soften if mixed script
        pen = min(22.0, (word_n - 4) * 5.0)
        if _LATIN_LETTER_RE.search(s) and _CYRILLIC_RE.search(s):
            pen *= 0.35
        score -= pen

    short_tokens = sum(1 for w in words if len(w) == 1)
    if words and short_tokens / len(words) > 0.55:
        score -= 35.0

    digit_ratio = sum(c.isdigit() for c in s) / L
    score -= digit_ratio * 38.0

    punct_ratio = sum(
        1 for c in s if unicodedata.category(c).startswith("P") or c in "|_~…"
    ) / L
    score -= punct_ratio * 42.0

    if 10 <= L <= 72:
        score += 14.0
    elif L > 110:
        score -= (L - 110) * 0.35

    # Looks like URL / www
    if re.search(r"\bwww\.|\.com\b|http", s, re.I):
        score -= 60.0

    # Starts with digit-heavy SKU row
    if re.match(r"^\d[\d\s./\\-]{6,}", s):
        score -= 18.0

    if _LOTS_OF_SINGLE_CHARS.search(s):
        score -= 45.0

    # Bonus: any uppercase letter (brand / Cyrillic caps)
    if any(c.isupper() for c in s if _is_letter(c)):
        score += 6.0

    return score


def _script_mix_bonus(s: str) -> float:
    if _LATIN_LETTER_RE.search(s) and _CYRILLIC_RE.search(s):
        return 32.0
    return 0.0


def _brand_keyword_bonus(s: str) -> float:
    t = unicodedata.normalize("NFKC", s).casefold()
    b = 0.0
    if "nestle" in t or "nestlé" in t or "нестле" in t:
        b += 22.0
    if re.search(r"(^|\s)nan(\s|$)", t) or re.search(r"(^|\s)нан(\s|$)", t):
        b += 14.0
    return b


def score_title_candidate(s: str) -> float:
    """Score a full product title (possibly merged lines)."""
    return (
        score_product_line(s)
        + _script_mix_bonus(s)
        + _brand_keyword_bonus(s)
    )


def title_similarity(guess: str, expected: str) -> float:
    """0..1 after normalizing spaces / case (for tests)."""
    a = _normalize_for_compare(guess)
    b = _normalize_for_compare(expected)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _line_ok_for_merge(st: str, sc: float) -> bool:
    """Include short stage/marketing fragments (e.g. \"3\") in merged titles."""
    s = st.strip()
    if len(s) <= 4 and (s.isdigit() or re.match(r"^\d+[.,]\d+$", s)):
        return True
    return sc > -28.0


def pick_product_name(
    lines_split: list[str],
    date_snippets: set[str],
) -> tuple[Optional[str], list[tuple[float, str]], str]:
    """
    Choose one concise product guess and a short preview for the UI.

    Returns:
        canonical_name or None,
        ranked (score, line) longest-first tie-break,
        machine_read_preview — short human-readable excerpt (not full OCR dump).
    """
    lines = dedupe_lines(_drop_noisy_lines(list(lines_split)))

    # Per-line scores in **document order** (critical for Nestlé-style: brand / line / Cyrillic).
    ordered: list[tuple[str, float]] = []
    for ln in lines:
        st = ln.strip()
        if len(st) < 2:
            continue
        if st in date_snippets:
            continue
        if any(len(sn) > 5 and sn in st for sn in date_snippets):
            continue
        if re.match(r"^[\d\s./\-:]+$", st) and len(st) < 22:
            # Keep short stage numbers like "3" for merging into full titles.
            if not (len(st.strip()) <= 4 and st.strip().isdigit()):
                continue
        sc = score_product_line(st)
        ordered.append((st, sc))

    seen_r: set[str] = set()
    ranked: list[tuple[float, str]] = []
    for st, _ in sorted(ordered, key=lambda x: -score_title_candidate(x[0])):
        if st in seen_r:
            continue
        seen_r.add(st)
        ranked.append((score_title_candidate(st), st))

    best: Optional[str] = None
    best_title_score = -9999.0

    # Candidates: every contiguous window of ordered lines (product title usually runs top-to-bottom).
    n = len(ordered)
    for i in range(n):
        for j in range(i + 1, min(i + 8, n + 1)):
            chunk = ordered[i:j]
            if not chunk:
                continue
            if not _line_ok_for_merge(chunk[0][0], chunk[0][1]):
                continue
            parts: list[str] = []
            for st, sc in chunk:
                if not _line_ok_for_merge(st, sc):
                    continue
                parts.append(_normalize_display_line(st))
            if not parts:
                continue
            merged = " ".join(parts).strip()
            if len(merged) < 4 or len(merged) > 140:
                continue
            ts = score_title_candidate(merged)
            if ts > best_title_score:
                best_title_score = ts
                best = merged[:512]

    # Strong single-line fallback
    for st, sc in ordered:
        ts = score_title_candidate(st)
        if ts > best_title_score:
            best_title_score = ts
            best = st[:512]

    if best is None and ordered:
        st, sc = max(ordered, key=lambda x: score_title_candidate(x[0]))
        if score_title_candidate(st) > -35.0:
            best = st[:512]

    preview = format_machine_read_preview(ranked)
    return best, ranked, preview


def format_machine_read_preview(ranked: list[tuple[float, str]], *, limit: int = 420) -> str:
    """Top candidate lines only — not the entire OCR blob."""
    if not ranked:
        return ""
    parts: list[str] = []
    n = 0
    for sc, text in ranked[:6]:
        if sc < -15:
            continue
        t = re.sub(r"\s+", " ", text.strip())
        if len(t) > 130:
            t = t[:127] + "…"
        parts.append(t)
        n += len(t)
        if n >= limit:
            break
    out = " · ".join(parts)
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out
