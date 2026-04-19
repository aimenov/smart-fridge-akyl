from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from backend.app.config import settings

logger = logging.getLogger(__name__)
from backend.app.models.entities import (
    AppSetting,
    DateType,
    Item,
    ItemLocation,
    ItemStatus,
    Product,
    ScanRecord,
)


CONFIDENCE_HIGH = 0.85
CONFIDENCE_MEDIUM = 0.50


def _today() -> date:
    return datetime.now(timezone.utc).date()


def reconcile_item_status(db: Session, item: Item, *, today: Optional[date] = None) -> Item:
    """Set fresh / expiring / expired from expiry_date (non consumed/discarded)."""
    if item.status in (ItemStatus.consumed, ItemStatus.discarded):
        return item
    day = today or _today()
    if item.expiry_date is None:
        item.status = ItemStatus.fresh
        return item
    warn_before = day + timedelta(days=settings.expiring_warning_days)
    if item.expiry_date < day:
        item.status = ItemStatus.expired
    elif item.expiry_date <= warn_before:
        item.status = ItemStatus.expiring
    else:
        item.status = ItemStatus.fresh
    return item


def reconcile_all_items(db: Session) -> int:
    items = db.query(Item).filter(
        Item.status.not_in([ItemStatus.consumed, ItemStatus.discarded])
    )
    n = 0
    for item in items:
        reconcile_item_status(db, item)
        n += 1
    db.commit()
    logger.debug("reconcile_all_items updated %d row(s)", n)
    return n


def get_or_create_product(
    db: Session,
    *,
    canonical_name: str,
    brand: Optional[str],
    barcode: Optional[str],
    default_unit: Optional[str],
    category: Optional[str],
) -> Product:
    q = db.query(Product).filter(Product.canonical_name == canonical_name.strip())
    if barcode:
        existing = db.query(Product).filter(Product.barcode == barcode.strip()).first()
        if existing:
            return existing
    product = q.first()
    if product:
        return product
    product = Product(
        canonical_name=canonical_name.strip(),
        brand=brand.strip() if brand else None,
        barcode=barcode.strip() if barcode else None,
        default_unit=default_unit,
        category=category,
    )
    db.add(product)
    db.flush()
    logger.debug("created product id=%s name=%r", product.id, product.canonical_name)
    return product


@dataclass
class DuplicateHit:
    item_id: int
    reason: str


def find_recent_duplicate(
    db: Session,
    *,
    product_id: int,
    expiry_date: Optional[date],
    since: datetime,
) -> Optional[DuplicateHit]:
    """Same product + same expiry scanned again within the duplicate window."""
    recent_items = (
        db.query(Item)
        .filter(
            Item.product_id == product_id,
            Item.created_at >= since,
            Item.status.not_in([ItemStatus.discarded]),
        )
        .order_by(Item.created_at.desc())
        .all()
    )
    for it in recent_items:
        if it.expiry_date == expiry_date:
            return DuplicateHit(item_id=it.id, reason="same_product_same_expiry_recent_scan")
    return None


def create_item_from_confirm(
    db: Session,
    *,
    product: Product,
    quantity: float,
    unit: str,
    expiry_date: Optional[date],
    location: ItemLocation,
    inferred_date_type: Optional[DateType],
    scan: ScanRecord,
) -> tuple[Item, Optional[DuplicateHit]]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=settings.duplicate_scan_window_seconds)
    dup = find_recent_duplicate(
        db,
        product_id=product.id,
        expiry_date=expiry_date,
        since=window_start,
    )
    if dup:
        item = db.query(Item).filter(Item.id == dup.item_id).one()
        item.quantity += quantity
        scan.item_id = item.id
        db.flush()
        logger.info(
            "duplicate scan merged into item_id=%s (+qty=%s reason=%s)",
            item.id,
            quantity,
            dup.reason,
        )
        return item, dup

    item = Item(
        product_id=product.id,
        quantity=quantity,
        unit=unit,
        expiry_date=expiry_date,
        location=location,
        inferred_date_type=inferred_date_type,
    )
    reconcile_item_status(db, item)
    db.add(item)
    db.flush()
    scan.item_id = item.id
    db.flush()
    logger.info(
        "created item id=%s product_id=%s expiry=%s location=%s",
        item.id,
        product.id,
        expiry_date,
        location,
    )
    return item, None


def list_items_with_product(db: Session, *, expiring_only: bool = False) -> list[Item]:
    q = db.query(Item).options(joinedload(Item.product)).filter(
        Item.status.not_in([ItemStatus.consumed, ItemStatus.discarded])
    )
    if expiring_only:
        q = q.filter(Item.status == ItemStatus.expiring)
    return q.order_by(Item.expiry_date.asc().nullslast()).all()


def patch_item(db: Session, item_id: int, **fields) -> Optional[Item]:
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        return None
    if "quantity" in fields and fields["quantity"] is not None:
        item.quantity = fields["quantity"]
    if "status" in fields and fields["status"]:
        item.status = ItemStatus(fields["status"])
    if "location" in fields and fields["location"]:
        item.location = ItemLocation(fields["location"])
    if "expiry_date" in fields:
        item.expiry_date = fields["expiry_date"]
    if fields.get("opened_now"):
        item.opened_at = datetime.now(timezone.utc)
    if "inferred_date_type" in fields and fields["inferred_date_type"]:
        item.inferred_date_type = DateType(fields["inferred_date_type"])
    reconcile_item_status(db, item)
    db.flush()
    return item


def set_telegram_chat_id(db: Session, chat_id: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == "telegram_chat_id").first()
    if row:
        row.value = chat_id
    else:
        db.add(AppSetting(key="telegram_chat_id", value=chat_id))
    db.flush()
