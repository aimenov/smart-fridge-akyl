from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.modules import inventory_service
from backend.app.modules.recipe_service import suggest_recipes
from backend.app.schemas.dto import ItemOut, ItemPatch, RecipeSuggestResponse, TelegramSettingsRequest

router = APIRouter(prefix="/api", tags=["inventory"])


def _item_out(db: Session, item_id: int) -> ItemOut:
    from sqlalchemy.orm import joinedload

    from backend.app.models.entities import Item

    item = db.query(Item).options(joinedload(Item.product)).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404, "item not found")
    return ItemOut(
        id=item.id,
        product_id=item.product_id,
        canonical_name=item.product.canonical_name,
        quantity=item.quantity,
        unit=item.unit,
        expiry_date=item.expiry_date,
        opened_at=item.opened_at,
        status=item.status.value,
        location=item.location.value,
        inferred_date_type=item.inferred_date_type.value if item.inferred_date_type else None,
    )


@router.get("/items", response_model=list[ItemOut])
def list_inventory(db: Session = Depends(get_db)):
    rows = inventory_service.list_items_with_product(db, expiring_only=False)
    return [
        ItemOut(
            id=i.id,
            product_id=i.product_id,
            canonical_name=i.product.canonical_name,
            quantity=i.quantity,
            unit=i.unit,
            expiry_date=i.expiry_date,
            opened_at=i.opened_at,
            status=i.status.value,
            location=i.location.value,
            inferred_date_type=i.inferred_date_type.value if i.inferred_date_type else None,
        )
        for i in rows
    ]


@router.get("/items/expiring", response_model=list[ItemOut])
def list_expiring(db: Session = Depends(get_db)):
    inventory_service.reconcile_all_items(db)
    rows = inventory_service.list_items_with_product(db, expiring_only=True)
    return [
        ItemOut(
            id=i.id,
            product_id=i.product_id,
            canonical_name=i.product.canonical_name,
            quantity=i.quantity,
            unit=i.unit,
            expiry_date=i.expiry_date,
            opened_at=i.opened_at,
            status=i.status.value,
            location=i.location.value,
            inferred_date_type=i.inferred_date_type.value if i.inferred_date_type else None,
        )
        for i in rows
    ]


@router.patch("/items/{item_id}", response_model=ItemOut)
def patch_item_route(item_id: int, patch: ItemPatch, db: Session = Depends(get_db)):
    kwargs = patch.model_dump(exclude_unset=True)
    opened_now = kwargs.pop("opened_now", None)
    kwargs["opened_now"] = opened_now

    item = inventory_service.patch_item(db, item_id, **kwargs)
    if not item:
        raise HTTPException(404, "item not found")
    db.commit()
    return _item_out(db, item_id)


@router.post("/settings/telegram")
def save_telegram(settings_body: TelegramSettingsRequest, db: Session = Depends(get_db)):
    inventory_service.set_telegram_chat_id(db, settings_body.chat_id)
    db.commit()
    return {"ok": True}


@router.get("/recipes/suggest", response_model=RecipeSuggestResponse)
def recipes_suggest(
    include_expired: bool = Query(False),
    db: Session = Depends(get_db),
):
    inventory_service.reconcile_all_items(db)
    return suggest_recipes(db, include_expired=include_expired)
