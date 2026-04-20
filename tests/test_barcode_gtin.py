from backend.app.modules.barcode_gtin import (
    loose_gtin14_storage_key,
    normalize_barcode_to_gtin14,
    strip_to_digits,
    validate_gtin_digits,
)


def test_strip_digits():
    assert strip_to_digits("123-456;78a") == "12345678"


def test_validate_ean13_known():
    # Valid EAN-13 (consumer goods example dataset)
    assert validate_gtin_digits("4006381333931")
    assert validate_gtin_digits("5901234123457")


def test_normalize_to_gtin14_from_ean13():
    r = normalize_barcode_to_gtin14("4006381333931")
    assert r.valid_check_digit
    assert r.normalized_gtin_14 == "04006381333931"


def test_normalize_rejects_bad_check():
    r = normalize_barcode_to_gtin14("4006381333930")
    assert not r.valid_check_digit


def test_loose_key_without_checksum():
    assert loose_gtin14_storage_key("4870235770032") == "04870235770032"


def test_upc_a_pads_to_gtin14():
    # UPC-A (12 digits) → GTIN-14 = ``00`` + full UPC string (includes leading system digit).
    r = normalize_barcode_to_gtin14("012345678905")
    assert r.valid_check_digit
    assert r.normalized_gtin_14 == "00012345678905"
