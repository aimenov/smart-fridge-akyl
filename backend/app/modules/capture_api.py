from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.entities import DateType, ItemLocation, ScanRecord
from backend.app.modules import inventory_service, vision_pipeline
from backend.app.modules import vlm_fallback
from backend.app.modules.inventory_service import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM

from backend.app.schemas.dto import (
    ConfirmScanRequest,
    ItemOut,
    ProductCreate,
    ScanUploadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["capture"])


def _tier(conf: float) -> str:
    if conf >= CONFIDENCE_HIGH:
        return "high"
    if conf >= CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


@router.post("/scan/upload", response_model=ScanUploadResponse)
async def upload_scan(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    chunks: list[tuple[str, bytes]] = []
    for f in files[:5]:
        raw = await f.read()
        if raw:
            chunks.append((f.filename or "frame.jpg", raw))
    logger.info("scan upload: accepted %d non-empty frame(s)", len(chunks))
    paths = vision_pipeline.persist_frames(chunks)
    result = vision_pipeline.run_pipeline(paths)
    logger.info(
        "vision pipeline conf=%.3f tier=%s barcode=%s date=%s",
        result.confidence,
        result.stages.get("tier"),
        result.barcode,
        result.normalized_date,
    )

    if settings.vlm_enabled and result.confidence < settings.vlm_confidence_below:
        raw_resp = await vlm_fallback.describe_product_and_expiry(paths)
        before = result.confidence
        result = vlm_fallback.merge_pipeline_with_vlm(result, raw_resp)
        logger.info("post-VLM confidence %.3f -> %.3f", before, result.confidence)

    scan = ScanRecord(
        captured_image_paths=[str(p) for p in paths],
        ocr_text=result.raw_ocr_text,
        model_outputs={"barcode": result.barcode},
        pipeline_stages=result.stages,
        parsed_date_type=result.date_type if result.date_type != DateType.unknown else None,
        raw_date_text=result.raw_date_text,
        normalized_date=result.normalized_date,
        confidence=result.confidence,
    )
    db.add(scan)
    db.flush()

    product_guess = ProductCreate(
        canonical_name=result.product_name_guess or "Unknown product",
        barcode=result.barcode,
    )

    return ScanUploadResponse(
        scan_id=scan.id,
        stage="done",
        confidence=result.confidence,
        confidence_tier=_tier(result.confidence),
        product_guess=product_guess,
        date_type=result.date_type.value if result.date_type else None,
        raw_date_text=result.raw_date_text,
        normalized_date=result.normalized_date,
        barcode=result.barcode,
        pipeline=result.stages,
    )


@router.post("/scan/confirm", response_model=ItemOut)
def confirm_scan(body: ConfirmScanRequest, db: Session = Depends(get_db)):
    logger.info(
        "confirm scan_id=%s product=%r qty=%s expiry=%s",
        body.scan_id,
        body.product.canonical_name,
        body.quantity,
        body.expiry_date,
    )
    scan = db.query(ScanRecord).filter(ScanRecord.id == body.scan_id).first()
    if not scan:
        raise HTTPException(404, "scan not found")

    prod = inventory_service.get_or_create_product(
        db,
        canonical_name=body.product.canonical_name,
        brand=body.product.brand,
        barcode=body.product.barcode,
        default_unit=body.product.default_unit,
        category=body.product.category,
    )

    inferred = None
    if body.inferred_date_type:
        inferred = DateType(body.inferred_date_type)

    item, dup = inventory_service.create_item_from_confirm(
        db,
        product=prod,
        quantity=body.quantity,
        unit=body.unit,
        expiry_date=body.expiry_date,
        location=ItemLocation(body.location),
        inferred_date_type=inferred,
        scan=scan,
    )

    scan.user_corrections = {
        "duplicate_of": dup.reason if dup else None,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    db.refresh(item)

    return ItemOut(
        id=item.id,
        product_id=item.product_id,
        canonical_name=prod.canonical_name,
        quantity=item.quantity,
        unit=item.unit,
        expiry_date=item.expiry_date,
        opened_at=item.opened_at,
        status=item.status.value,
        location=item.location.value,
        inferred_date_type=item.inferred_date_type.value if item.inferred_date_type else None,
    )


@router.get("/scans/recent")
def recent_scans(limit: int = 30, db: Session = Depends(get_db)):
    rows = (
        db.query(ScanRecord).order_by(ScanRecord.created_at.desc()).limit(min(limit, 100)).all()
    )
    out = []
    for s in rows:
        out.append(
            {
                "id": s.id,
                "item_id": s.item_id,
                "confidence": s.confidence,
                "normalized_date": s.normalized_date,
                "ocr_excerpt": (s.ocr_text or "")[:400],
                "created_at": s.created_at.isoformat(),
                "user_corrections": s.user_corrections,
            }
        )
    return out
