from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ItemStatus(str, enum.Enum):
    fresh = "fresh"
    expiring = "expiring"
    expired = "expired"
    consumed = "consumed"
    discarded = "discarded"


class ItemLocation(str, enum.Enum):
    fridge = "fridge"
    freezer = "freezer"
    pantry = "pantry"


class DateType(str, enum.Enum):
    best_before = "best_before"
    use_by = "use_by"
    packed_on = "packed_on"
    produced_on = "produced_on"
    expiry = "expiry"
    unknown = "unknown"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    default_unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    items: Mapped[list["Item"]] = relationship(back_populates="product")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="each")
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ItemStatus.fresh,
    )
    location: Mapped[ItemLocation] = mapped_column(
        Enum(ItemLocation, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ItemLocation.fridge,
    )

    inferred_date_type: Mapped[Optional[DateType]] = mapped_column(
        Enum(DateType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )

    last_expiry_notification_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_daily_summary_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    product: Mapped["Product"] = relationship(back_populates="items")
    scans: Mapped[list["ScanRecord"]] = relationship(back_populates="item")


class ScanRecord(Base):
    __tablename__ = "scan_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)

    captured_image_paths: Mapped[list[str]] = mapped_column(SQLiteJSON, nullable=False, default=list)
    ocr_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_outputs: Mapped[dict[str, Any]] = mapped_column(SQLiteJSON, nullable=False, default=dict)
    pipeline_stages: Mapped[dict[str, Any]] = mapped_column(SQLiteJSON, nullable=False, default=dict)

    parsed_date_type: Mapped[Optional[DateType]] = mapped_column(
        Enum(DateType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    raw_date_text: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    normalized_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    user_corrections: Mapped[dict[str, Any]] = mapped_column(SQLiteJSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    item: Mapped[Optional["Item"]] = relationship(back_populates="scans")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
