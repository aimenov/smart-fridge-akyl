from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    canonical_name: str = Field(..., max_length=512)
    brand: Optional[str] = Field(None, max_length=256)
    barcode: Optional[str] = Field(None, max_length=64)
    default_unit: Optional[str] = Field(None, max_length=32)
    category: Optional[str] = Field(None, max_length=128)


class ItemOut(BaseModel):
    id: int
    product_id: int
    canonical_name: str
    quantity: float
    unit: str
    expiry_date: Optional[date]
    opened_at: Optional[datetime]
    status: str
    location: str
    inferred_date_type: Optional[str]

    model_config = {"from_attributes": True}


class ItemPatch(BaseModel):
    quantity: Optional[float] = Field(None, gt=0)
    status: Optional[str] = None
    location: Optional[str] = None
    expiry_date: Optional[date] = None
    opened_now: Optional[bool] = None
    inferred_date_type: Optional[str] = None


class ScanUploadResponse(BaseModel):
    scan_id: int
    stage: Literal["finding_product", "reading_date", "done"]
    confidence: float
    confidence_tier: Literal["high", "medium", "low"]
    product_guess: Optional[ProductCreate] = None
    date_type: Optional[str] = None
    raw_date_text: Optional[str] = None
    normalized_date: Optional[str] = None
    barcode: Optional[str] = None
    pipeline: dict[str, Any] = Field(default_factory=dict)


class ConfirmScanRequest(BaseModel):
    scan_id: int
    product: ProductCreate
    quantity: float = 1.0
    unit: str = "each"
    expiry_date: Optional[date] = None
    location: str = "fridge"
    inferred_date_type: Optional[str] = None


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: list[str]
    prep_minutes: int
    missing_from_pantry: list[str]
    uses_expiring: list[str]
    pantry_coverage: float


class RecipeSuggestResponse(BaseModel):
    can_cook_now: list[RecipeOut]
    need_one_or_two_items: list[RecipeOut]
    best_for_expiring_soon: list[RecipeOut]
    pantry_note: str


class PantryLine(BaseModel):
    name: str
    quantity_hint: Optional[str] = None
    expiring_soon: bool = False


class TelegramSettingsRequest(BaseModel):
    chat_id: str
