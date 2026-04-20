"""Import product rows from NCT ОФД integration export (Postman «АПИ для ОФД» collection)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.entities import ProductsMaster
from backend.app.modules.barcode_gtin import normalize_barcode_to_gtin14, strip_to_digits

logger = logging.getLogger(__name__)


def canonical_gtin14_from_ofd(row: dict[str, Any]) -> Optional[str]:
    """Primary key for ``products_master``: GTIN-14 when GS1-valid, else NTIN padded to 14 digits."""
    raw_gtin = row.get("gtin")
    if raw_gtin not in (None, ""):
        g = normalize_barcode_to_gtin14(str(raw_gtin))
        if g.normalized_gtin_14:
            return g.normalized_gtin_14

    ntin = row.get("ntin_code")
    if ntin not in (None, ""):
        digits = strip_to_digits(str(ntin))
        if not digits:
            return None
        if len(digits) <= 14:
            return digits.zfill(14)
        return digits[-14:]
    return None


def fields_from_ofd_payload(row: dict[str, Any], *, canonical: str) -> dict[str, Any]:
    gtin_digits = strip_to_digits(str(row["gtin"])) if row.get("gtin") else None
    ntin_digits = strip_to_digits(str(row["ntin_code"])) if row.get("ntin_code") else None

    measure = row.get("measure") if isinstance(row.get("measure"), dict) else {}
    unit = measure.get("short_name") or measure.get("code") or measure.get("name")

    modified = row.get("modified")
    last_sync = None
    if isinstance(modified, (int, float)):
        last_sync = datetime.fromtimestamp(float(modified), tz=timezone.utc)

    return {
        "canonical": canonical,
        "raw_gtin": gtin_digits,
        "ntin": ntin_digits,
        "brand": None,
        "name_ru": row.get("name_ru"),
        "name_kk": row.get("name_kk"),
        "category_path": None,
        "packaging_type": None,
        "size_value": None,
        "size_unit": str(unit)[:32] if unit else None,
        "last_synced_at": last_sync,
        "source_payload_json": dict(row),
    }


def upsert_products_master_from_ofd(db: Session, row: dict[str, Any]) -> tuple[bool, str]:
    """Insert or update one ``ProductsMaster`` row from an OFD ``result`` item."""
    canon = canonical_gtin14_from_ofd(row)
    if not canon or len(canon) != 14:
        return False, "no_key"

    payload = fields_from_ofd_payload(row, canonical=canon)
    existing = db.get(ProductsMaster, canon)

    name_ru = payload["name_ru"]
    name_kk = payload["name_kk"]
    raw_payload = payload["source_payload_json"]
    synced = payload["last_synced_at"] or now

    if existing:
        if name_ru:
            existing.name_ru = name_ru
        if name_kk:
            existing.name_kk = name_kk
        if payload["raw_gtin"]:
            existing.raw_gtin = payload["raw_gtin"]
        if payload["ntin"]:
            existing.ntin = payload["ntin"]
        if payload["size_unit"]:
            existing.size_unit = payload["size_unit"]
        existing.source_payload_json = raw_payload
        existing.last_synced_at = synced
        existing.source = "ofd"
        return True, "updated"

    db.add(
        ProductsMaster(
            gtin_14=canon,
            raw_gtin=payload["raw_gtin"],
            ntin=payload["ntin"],
            brand=payload["brand"],
            name_ru=name_ru,
            name_kk=name_kk,
            category_path=payload["category_path"],
            packaging_type=payload["packaging_type"],
            size_value=payload["size_value"],
            size_unit=payload["size_unit"],
            source="ofd",
            source_payload_json=raw_payload,
            last_synced_at=synced,
        )
    )
    db.flush()
    return True, "inserted"


def fetch_ofd_page(client: httpx.Client, url: str) -> dict[str, Any]:
    r = client.get(url, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("OFD export expected JSON object")
    return data


def import_ofd_catalog(
    db: Session,
    *,
    start_url: str,
    max_pages: Optional[int] = None,
    max_rows: Optional[int] = None,
    dry_run: bool = False,
    sleep_seconds: float = 0.0,
) -> dict[str, int]:
    """
    Follow paginated ``next`` links from the ОФД export API and upsert into ``products_master``.

    API reference: `<https://documenter.getpostman.com/view/21459402/2sB2cVe1oW>`_ (same contract as live
    ``GET https://nct.gov.kz/api/integration/ofd/ofd/``).
    """
    stats = {"pages": 0, "rows_seen": 0, "upserts": 0, "skipped": 0}

    timeout = settings.ofd_catalog_http_timeout_seconds
    next_url: Optional[str] = start_url

    with httpx.Client(timeout=timeout) as client:
        while next_url:
            if max_pages is not None and stats["pages"] >= max_pages:
                break

            payload = fetch_ofd_page(client, next_url)
            stats["pages"] += 1

            items = payload.get("result")
            if not isinstance(items, list):
                logger.warning("OFD page missing result array")
                break

            reached_row_cap = False
            for row in items:
                if isinstance(row, dict):
                    stats["rows_seen"] += 1
                    ok, _reason = upsert_products_master_from_ofd(db, row)
                    if ok:
                        stats["upserts"] += 1
                    else:
                        stats["skipped"] += 1

                    if max_rows is not None and stats["rows_seen"] >= max_rows:
                        reached_row_cap = True
                        break

            if dry_run:
                db.rollback()
            else:
                db.commit()

            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

            if reached_row_cap:
                break

            nxt = payload.get("next")
            next_url = str(nxt).strip() if nxt else None
            if next_url:
                logger.debug("OFD next page cursor %s …", next_url[:80])

    return stats


def build_initial_export_url(*, from_timestamp: int) -> str:
    base = settings.ofd_catalog_export_url.rstrip("/")
    limit = max(1, min(settings.ofd_catalog_page_limit, 5000))
    return f"{base}/?from={from_timestamp}&limit={limit}"
