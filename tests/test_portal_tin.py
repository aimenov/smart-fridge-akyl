from backend.app.modules.national_catalog import portal_path_tin_from_gtin14, unwrap_product_payload


def test_portal_path_strips_leading_indicator_digit():
    assert portal_path_tin_from_gtin14("04006381333931") == "4006381333931"


def test_portal_path_keeps_nonzero_indicator():
    assert portal_path_tin_from_gtin14("14006381333938") == "14006381333938"


def test_unwrap_array_response():
    assert unwrap_product_payload([{"nameRu": "X"}]) == {"nameRu": "X"}


def test_unwrap_nested_data():
    assert unwrap_product_payload({"data": {"nameRu": "Y"}}) == {"nameRu": "Y"}


def test_unwrap_array_picks_matching_gtin():
    cards = [
        {"gtin": "1111111111111", "nameRu": "wrong"},
        {"gtin": "7613287295798", "nameRu": "right"},
    ]
    picked = unwrap_product_payload(cards, tin_query="7613287295798")
    assert picked is not None
    assert picked["nameRu"] == "right"


def test_extract_catalog_reads_attribute_codes():
    from backend.app.modules.national_catalog import extract_catalog_fields

    fields = extract_catalog_fields(
        {
            "attributes": [
                {"code": "name_ru", "value": "Из атрибутов"},
                {"code": "brand", "value": "AttrBrand"},
            ]
        }
    )
    assert fields["name_ru"] == "Из атрибутов"
    assert fields["brand"] == "AttrBrand"
