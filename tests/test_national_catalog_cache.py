from sqlalchemy.orm import Session

from backend.app.models.entities import ProductsMaster
from backend.app.modules import national_catalog


def test_resolve_inserts_master_when_remote_returns_payload(monkeypatch, db_session: Session):
    def fake_fetch(*, gtin14=None, ntin=None):
        if gtin14 == "04006381333931":
            return {"tradeNameRu": "Тест йогурт", "brand": "DemoBrand", "categoryPath": "Молочные"}
        return None

    monkeypatch.setattr(national_catalog, "fetch_product_json", fake_fetch)

    gtin = "04006381333931"
    row, name, key = national_catalog.resolve_product_for_scan(
        db_session,
        normalized_gtin_14=gtin,
        raw_barcode="4006381333931",
        symbology="EAN_13",
    )
    assert key == gtin
    db_session.commit()

    assert name == "Тест йогурт"
    assert row is not None
    assert row.brand == "DemoBrand"
    stored = db_session.query(ProductsMaster).filter_by(gtin_14=gtin).one()
    assert stored.name_ru == "Тест йогурт"


def test_resolve_uses_local_cache_without_second_fetch(monkeypatch, db_session: Session):
    calls = {"n": 0}

    def counting_fetch(*, gtin14=None, ntin=None):
        calls["n"] += 1
        return {"tradeNameRu": "Cached", "brand": "B"}

    monkeypatch.setattr(national_catalog, "fetch_product_json", counting_fetch)

    gtin = "05901234123457"
    national_catalog.resolve_product_for_scan(
        db_session,
        normalized_gtin_14=gtin,
        raw_barcode="5901234123457",
        symbology="EAN_13",
    )
    db_session.commit()
    assert calls["n"] == 1

    calls["n"] = 0
    national_catalog.resolve_product_for_scan(
        db_session,
        normalized_gtin_14=gtin,
        raw_barcode="5901234123457",
        symbology="EAN_13",
    )
    db_session.commit()
    assert calls["n"] == 0
