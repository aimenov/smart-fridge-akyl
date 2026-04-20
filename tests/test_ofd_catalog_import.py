from sqlalchemy.orm import Session

from backend.app.models.entities import ProductsMaster
from backend.app.modules.ofd_catalog_import import (
    canonical_gtin14_from_ofd,
    import_ofd_catalog,
    upsert_products_master_from_ofd,
)


def test_canonical_from_ntin_only():
    row = {"gtin": None, "ntin_code": "0200087847493", "name_ru": "X"}
    assert canonical_gtin14_from_ofd(row) == "00200087847493"


def test_upsert_ntin_rows(db_session: Session):
    row = {
        "gtin": None,
        "ntin_code": "0200087847493",
        "name_ru": "Товар",
        "name_kk": None,
        "modified": 1700000000,
        "measure": {"short_name": "796", "code": "796"},
    }
    ok, state = upsert_products_master_from_ofd(db_session, row)
    db_session.commit()
    assert ok and state == "inserted"
    pk = "00200087847493"
    m = db_session.query(ProductsMaster).filter_by(gtin_14=pk).one()
    assert m.name_ru == "Товар"
    assert m.source == "ofd"
    assert m.ntin == "0200087847493"


def test_import_follows_next(monkeypatch, db_session: Session):
    pages = [
        {
            "next": "https://example.invalid/ofd?page=2",
            "result": [
                {
                    "gtin": None,
                    "ntin_code": "0200087847493",
                    "name_ru": "A",
                    "modified": 1,
                }
            ],
        },
        {
            "next": None,
            "result": [
                {
                    "gtin": None,
                    "ntin_code": "0200087847494",
                    "name_ru": "B",
                    "modified": 2,
                }
            ],
        },
    ]

    def fake_fetch(_client, url):
        return pages.pop(0)

    monkeypatch.setattr(
        "backend.app.modules.ofd_catalog_import.fetch_ofd_page",
        fake_fetch,
    )

    stats = import_ofd_catalog(
        db_session,
        start_url="https://example.invalid/start",
        max_pages=10,
        dry_run=False,
    )
    db_session.commit()

    assert stats["pages"] == 2
    assert stats["upserts"] == 2
    assert db_session.query(ProductsMaster).count() == 2


def test_duplicate_canonical_same_page_updates_not_second_insert(db_session: Session):
    """API pages may repeat the same NTIN/GTIN; session must see pending PK before insert."""
    row = {
        "gtin": None,
        "ntin_code": "0200087847493",
        "name_ru": "First",
        "modified": 1,
    }
    dup = {
        **row,
        "name_ru": "Second revision",
        "modified": 2,
    }
    upsert_products_master_from_ofd(db_session, row)
    upsert_products_master_from_ofd(db_session, dup)
    db_session.commit()
    assert db_session.query(ProductsMaster).count() == 1
    m = db_session.query(ProductsMaster).one()
    assert m.name_ru == "Second revision"


def test_import_respects_max_rows(monkeypatch, db_session: Session):
    big_page = {
        "next": None,
        "result": [
            {
                "gtin": None,
                "ntin_code": f"020008784749{i}",
                "name_ru": str(i),
                "modified": i,
            }
            for i in range(5)
        ],
    }

    def fake_fetch(_client, url):
        return big_page

    monkeypatch.setattr(
        "backend.app.modules.ofd_catalog_import.fetch_ofd_page",
        fake_fetch,
    )

    stats = import_ofd_catalog(
        db_session,
        start_url="https://example.invalid/start",
        max_pages=5,
        max_rows=3,
        dry_run=False,
    )
    db_session.commit()
    assert stats["rows_seen"] == 3
    assert db_session.query(ProductsMaster).count() == 3
