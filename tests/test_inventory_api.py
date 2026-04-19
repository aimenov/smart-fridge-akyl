from datetime import date

from backend.app.models.entities import ItemLocation, ItemStatus, Product
from backend.app.modules.inventory_service import reconcile_item_status


def test_items_empty(client):
    r = client.get("/api/items")
    assert r.status_code == 200
    assert r.json() == []


def test_patch_item_not_found(client):
    r = client.patch("/api/items/99999", json={"status": "consumed"})
    assert r.status_code == 404


def test_inventory_flow(client, db_session):
    p = Product(canonical_name="Test Milk", barcode="123")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)

    from backend.app.models.entities import Item

    item = Item(
        product_id=p.id,
        quantity=1,
        unit="L",
        expiry_date=date(2099, 1, 1),
        status=ItemStatus.fresh,
        location=ItemLocation.fridge,
    )
    reconcile_item_status(db_session, item)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    r = client.get("/api/items")
    assert len(r.json()) == 1
    row = r.json()[0]
    assert row["canonical_name"] == "Test Milk"

    r2 = client.patch(f"/api/items/{item.id}", json={"status": "consumed"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "consumed"
