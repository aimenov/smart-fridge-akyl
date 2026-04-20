from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    barcode_audit: Mapped[Optional["ScanAudit"]] = relationship(
        back_populates="scan_record",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ProductsMaster(Base):
    """Pull-through cache of Национальный каталог товаров (НКТ) — keyed by canonical GTIN-14."""

    __tablename__ = "products_master"

    gtin_14: Mapped[str] = mapped_column(String(14), primary_key=True)
    raw_gtin: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ntin: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    name_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_kk: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    packaging_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size_unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="nct")
    source_payload_json: Mapped[dict[str, Any]] = mapped_column(SQLiteJSON, nullable=False, default=dict)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BarcodeAlias(Base):
    """Maps a scanned symbology-specific string to a canonical ``gtin_14``."""

    __tablename__ = "barcode_aliases"
    __table_args__ = (UniqueConstraint("code", name="uq_barcode_aliases_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    normalized_gtin_14: Mapped[str] = mapped_column(String(14), nullable=False, index=True)
    symbology: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class ScanAudit(Base):
    """Barcode / decode audit row tied to one ``ScanRecord`` (processing metadata)."""

    __tablename__ = "scan_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_record_id: Mapped[int] = mapped_column(
        ForeignKey("scan_records.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    decoded_barcode: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    normalized_gtin_14: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    symbology: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ocr_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    user_corrections: Mapped[dict[str, Any]] = mapped_column(SQLiteJSON, nullable=False, default=dict)

    scan_record: Mapped["ScanRecord"] = relationship(back_populates="barcode_audit")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
