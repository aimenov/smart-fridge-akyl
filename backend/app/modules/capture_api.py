from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.entities import DateType, ItemLocation, ScanRecord
from backend.app.modules import inventory_service, vision_pipeline
from backend.app.modules import vlm_fallback
from backend.app.modules.inventory_service import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from backend.app.json_safe import json_safe
from backend.app.observability import begin_trace, end_trace, trace_prefix

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
    trace_token = begin_trace(str(uuid.uuid4()))
    t_request = time.perf_counter()
    try:
        chunks: list[tuple[str, bytes]] = []
        for f in files[:5]:
            raw = await f.read()
            if raw:
                chunks.append((f.filename or "frame.jpg", raw))
        total_bytes = sum(len(c[1]) for c in chunks)
        logger.info(
            "%sPOST /api/scan/upload start | frames=%d total_bytes=%d vlm_enabled=%s vlm_threshold=%.2f",
            trace_prefix(),
            len(chunks),
            total_bytes,
            settings.vlm_enabled,
            settings.vlm_confidence_below,
        )
        paths = vision_pipeline.persist_frames(chunks)
        result = vision_pipeline.run_pipeline(paths)
        logger.info(
            "%sOCR path result: conf=%.3f tier=%s barcode=%s date=%s",
            trace_prefix(),
            result.confidence,
            result.stages.get("tier"),
            result.barcode,
            result.normalized_date,
        )

        if settings.vlm_enabled and result.confidence < settings.vlm_confidence_below:
            raw_resp = await vlm_fallback.describe_product_and_expiry(paths)
            before = result.confidence
            result = vlm_fallback.merge_pipeline_with_vlm(result, raw_resp)
            logger.info(
                "%sAfter VLM: confidence %.3f -> %.3f",
                trace_prefix(),
                before,
                result.confidence,
            )

        safe_stages = json_safe(result.stages)
        scan = ScanRecord(
            captured_image_paths=[str(p) for p in paths],
            ocr_text=result.raw_ocr_text,
            model_outputs={"barcode": result.barcode},
            pipeline_stages=safe_stages if isinstance(safe_stages, dict) else {},
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

        conf = float(result.confidence)
        if math.isnan(conf) or math.isinf(conf):
            conf = 0.0

        return ScanUploadResponse(
            scan_id=scan.id,
            stage="done",
            confidence=conf,
            confidence_tier=_tier(conf),
            product_guess=product_guess,
            date_type=result.date_type.value if result.date_type else None,
            raw_date_text=result.raw_date_text,
            normalized_date=result.normalized_date,
            barcode=result.barcode,
            pipeline=safe_stages if isinstance(safe_stages, dict) else {},
        )
    finally:
        elapsed = time.perf_counter() - t_request
        logger.info(
            "%sPOST /api/scan/upload finished in %.2fs (DB record id above in response scan_id)",
            trace_prefix(),
            elapsed,
        )
        end_trace(trace_token)


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
        try:
            inferred = DateType(body.inferred_date_type)
        except ValueError:
            raise HTTPException(
                422,
                f"invalid inferred_date_type: {body.inferred_date_type!r}",
            ) from None

    try:
        location = ItemLocation(body.location)
    except ValueError:
        raise HTTPException(422, f"invalid location: {body.location!r}") from None

    item, dup = inventory_service.create_item_from_confirm(
        db,
        product=prod,
        quantity=body.quantity,
        unit=body.unit,
        expiry_date=body.expiry_date,
        location=location,
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
