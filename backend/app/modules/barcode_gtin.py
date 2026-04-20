from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_NON_DIGIT = re.compile(r"\D+")


@dataclass(frozen=True, slots=True)
class GtinNormalization:
    raw_digits: str
    normalized_gtin_14: Optional[str]
    valid_check_digit: bool


def strip_to_digits(value: str) -> str:
    return _NON_DIGIT.sub("", value or "")


def _gs1_check_digit(body_without_check: str) -> int:
    """GS1 mod-10 over body digits (check digit excluded)."""
    total = 0
    for i, ch in enumerate(reversed(body_without_check)):
        d = int(ch)
        w = 3 if (i % 2 == 0) else 1
        total += d * w
    return (10 - (total % 10)) % 10


def validate_gtin_digits(digits: str) -> bool:
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    return _gs1_check_digit(body) == check


def pad_to_gtin_14(digits: str) -> Optional[str]:
    """Left-pad variable-length retail GTINs to 14 digits (canonical storage key)."""
    if not digits.isdigit():
        return None
    n = len(digits)
    if n == 14:
        return digits
    if n == 13:
        return digits.zfill(14)
    if n == 12:
        # GS1: GTIN-14 = two zero digits + 12-digit UPC-A (do not use ``zfill`` — ambiguous with leading digit 0).
        return "00" + digits
    if n == 8:
        return digits.zfill(14)
    return None


def normalize_barcode_to_gtin14(decoded: str) -> GtinNormalization:
    """
    Convert a scanner/OCR string to a canonical GTIN-14 key when possible.
    Accepts EAN-13, UPC-A, EAN-8, or GTIN-14 after stripping non-digits.
    """
    raw_digits = strip_to_digits(decoded)
    if not raw_digits:
        return GtinNormalization(raw_digits="", normalized_gtin_14=None, valid_check_digit=False)

    candidates: list[str] = []
    if len(raw_digits) > 14:
        # Keep longest trailing slice that looks like a GTIN length (handles stray prefix digits).
        for ln in (14, 13, 12, 8):
            if len(raw_digits) >= ln:
                candidates.append(raw_digits[-ln:])
    else:
        candidates.append(raw_digits)

    best: Optional[GtinNormalization] = None
    for cand in candidates:
        padded = pad_to_gtin_14(cand)
        valid = validate_gtin_digits(cand) if padded else False
        gn = GtinNormalization(
            raw_digits=cand,
            normalized_gtin_14=padded if valid else None,
            valid_check_digit=valid,
        )
        if valid:
            best = gn
            break
        if best is None:
            best = gn

    return best or GtinNormalization(
        raw_digits=raw_digits, normalized_gtin_14=None, valid_check_digit=False
    )


def loose_gtin14_storage_key(raw: Optional[str]) -> Optional[str]:
    """
    14-digit ``products_master`` key when the barcode read is not GS1-valid but digits are usable
    for catalog lookup (same padding rules as validated GTINs, without checksum).
    """
    d = strip_to_digits(raw or "")
    if not d:
        return None
    if len(d) > 14:
        d = d[-14:]
    if len(d) == 14:
        return d
    if len(d) == 13:
        return d.zfill(14)
    if len(d) == 12:
        return "00" + d
    if len(d) == 8:
        return d.zfill(14)
    return d.zfill(14)
