from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.entities import DateType, ItemLocation, ScanAudit, ScanRecord
from backend.app.modules import inventory_service, national_catalog, vision_pipeline
from backend.app.modules.inventory_service import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from backend.app.modules.vision_pipeline import PipelineResult
from backend.app.json_safe import json_safe
from backend.app.logging_config import get_summary_logger
from backend.app.observability import begin_trace, end_trace

from backend.app.schemas.dto import (
    ConfirmScanRequest,
    ItemOut,
    ProductCreate,
    ScanUploadResponse,
)

logger = logging.getLogger(__name__)
summary = get_summary_logger()

router = APIRouter(prefix="/api", tags=["capture"])


def _tier(conf: float) -> str:
    if conf >= CONFIDENCE_HIGH:
        return "high"
    if conf >= CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


def _ocr_preview(raw: str, limit: int = 420) -> str:
    if not raw:
        return ""
    collapsed = " ".join(raw.split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1] + "…"
    return collapsed


def _degraded_pipeline_result(reason: str) -> PipelineResult:
    """Vision pipeline failed; return a safe empty result so the client still gets HTTP 200."""
    return PipelineResult(
        barcode=None,
        barcode_raw=None,
        barcode_symbology=None,
        normalized_gtin_14=None,
        raw_ocr_text="",
        date_type=DateType.unknown,
        raw_date_text=None,
        normalized_date=None,
        confidence=0.0,
        stages={"error": reason},
        product_name_guess=None,
    )

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
        logger.debug(
            "POST /api/scan/upload start | frames=%d total_bytes=%d",
            len(chunks),
            total_bytes,
        )
        if not chunks:
            raise HTTPException(400, "no image bytes in upload (empty frames)")

        # CPU-heavy OpenCV must not block the asyncio loop (keeps TLS/proxy idle
        # behaviour healthier and avoids starving other requests on single-worker uvicorn).
        paths = await asyncio.to_thread(vision_pipeline.persist_frames, chunks)
        try:
            result = await asyncio.to_thread(vision_pipeline.run_pipeline, paths)
        except Exception:
            logger.exception("run_pipeline crashed; returning degraded scan")
            result = _degraded_pipeline_result("pipeline_exception")

        logger.debug(
            "pipeline tier=%s barcode=%s gtin14=%s date=%s conf=%.3f",
            result.stages.get("tier"),
            result.barcode,
            result.normalized_gtin_14,
            result.normalized_date,
            result.confidence,
        )

        master_row, catalog_name, lookup_key = national_catalog.resolve_product_for_scan(
            db,
            normalized_gtin_14=result.normalized_gtin_14,
            raw_barcode=(result.barcode_raw or result.barcode or "").strip(),
            symbology=result.barcode_symbology,
        )
        product_label = catalog_name or result.product_name_guess or "Unknown product"
        catalog_hit = master_row is not None

        safe_stages = json_safe(result.stages)
        if isinstance(safe_stages, dict):
            safe_stages["catalog_match"] = catalog_hit
            safe_stages["catalog_lookup_key"] = lookup_key

        scan_name = catalog_name or "—"
        summary.info(
            "SCAN | barcode=%s | gtin14=%s | lookup_key=%s | product=%s | catalog=%s",
            result.barcode_raw or "—",
            result.normalized_gtin_14 or "—",
            lookup_key or "—",
            product_label,
            scan_name if catalog_hit else ("miss" if lookup_key else "skip"),
        )

        scan = ScanRecord(
            captured_image_paths=[str(p) for p in paths],
            ocr_text=result.raw_ocr_text,
            model_outputs={
                "barcode": result.barcode,
                "barcode_raw": result.barcode_raw,
                "normalized_gtin_14": lookup_key or result.normalized_gtin_14,
                "catalog_name_ru": catalog_name,
                "catalog_lookup_key": lookup_key,
                "catalog_match": catalog_hit,
            },
            pipeline_stages=safe_stages if isinstance(safe_stages, dict) else {},
            parsed_date_type=result.date_type if result.date_type != DateType.unknown else None,
            raw_date_text=result.raw_date_text,
            normalized_date=result.normalized_date,
            confidence=result.confidence,
        )
        db.add(scan)
        db.flush()

        db.add(
            ScanAudit(
                scan_record_id=scan.id,
                decoded_barcode=result.barcode_raw,
                normalized_gtin_14=lookup_key or result.normalized_gtin_14,
                symbology=result.barcode_symbology,
                ocr_text=result.raw_ocr_text,
                parsed_date=result.normalized_date,
                confidence=result.confidence,
                user_corrections={},
            )
        )

        cat_path = master_row.category_path[:128] if master_row and master_row.category_path else None
        product_guess = ProductCreate(
            canonical_name=product_label,
            barcode=(lookup_key or result.normalized_gtin_14 or result.barcode),
            brand=master_row.brand if master_row else None,
            category=cat_path,
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
            catalog_lookup_key=lookup_key,
            catalog_match=catalog_hit,
            ocr_text_preview=_ocr_preview(result.raw_ocr_text or ""),
            pipeline=safe_stages if isinstance(safe_stages, dict) else {},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("scan/upload failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        elapsed = time.perf_counter() - t_request
        logger.debug(
            "POST /api/scan/upload finished in %.2fs scan_id implicit in response",
            elapsed,
        )
        end_trace(trace_token)


@router.post("/scan/confirm", response_model=ItemOut)
def confirm_scan(body: ConfirmScanRequest, db: Session = Depends(get_db)):
    summary.info(
        "CONFIRM | scan_id=%s | product=%s | qty=%s | expiry=%s",
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
