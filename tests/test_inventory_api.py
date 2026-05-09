from datetime import date

from backend.app.models.entities import ItemLocation, ItemStatus, Product, ScanRecord
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


def test_scan_confirm_creates_item(client, db_session):
    """Saving from the scan flow: POST /api/scan/confirm attaches an inventory item to a scan row."""
    scan = ScanRecord(captured_image_paths=[], confidence=0.92, normalized_date="2030-06-01")
    db_session.add(scan)
    db_session.commit()
    db_session.refresh(scan)

    r = client.post(
        "/api/scan/confirm",
        json={
            "scan_id": scan.id,
            "product": {"canonical_name": "Shelf Test Cheese", "barcode": "999888777"},
            "quantity": 1.5,
            "unit": "pack",
            "expiry_date": "2030-06-15",
            "location": "fridge",
            "inferred_date_type": "best_before",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["canonical_name"] == "Shelf Test Cheese"
    assert body["quantity"] == 1.5
    assert body["unit"] == "pack"
    assert body["expiry_date"] == "2030-06-15"
    assert body["location"] == "fridge"

    listed = client.get("/api/items").json()
    assert len(listed) == 1
    assert listed[0]["canonical_name"] == "Shelf Test Cheese"
    assert listed[0]["id"] == body["id"]
