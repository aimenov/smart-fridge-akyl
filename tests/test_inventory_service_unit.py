from datetime import date, timedelta

from backend.app.models.entities import Item, ItemLocation, ItemStatus, Product
from backend.app.modules.inventory_service import reconcile_item_status


def test_reconcile_marks_expired(db_session):
    p = Product(canonical_name="Old Cream")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    item = Item(
        product_id=p.id,
        quantity=1,
        unit="each",
        expiry_date=date.today() - timedelta(days=2),
        status=ItemStatus.fresh,
        location=ItemLocation.fridge,
    )
    reconcile_item_status(db_session, item)
    assert item.status == ItemStatus.expired


def test_reconcile_expiring_window(db_session):
    p = Product(canonical_name="Soon")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    item = Item(
        product_id=p.id,
        quantity=1,
        unit="each",
        expiry_date=date.today() + timedelta(days=1),
        status=ItemStatus.fresh,
        location=ItemLocation.fridge,
    )
    reconcile_item_status(db_session, item)
    assert item.status == ItemStatus.expiring
