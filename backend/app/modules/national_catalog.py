from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.entities import BarcodeAlias, ProductsMaster
from backend.app.modules.barcode_gtin import loose_gtin14_storage_key, strip_to_digits

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digits_tail(s: str, n: int = 13) -> str:
    d = strip_to_digits(s or "")
    return d[-n:] if len(d) >= n else d


def extract_catalog_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Map portal ``PortalProductDto`` and legacy integration JSON shapes."""
    name_ru = (
        payload.get("nameRu")
        or payload.get("shortNameRu")
        or payload.get("tradeNameRu")
        or payload.get("trade_name_ru")
        or payload.get("name_ru")
        or payload.get("productNameRu")
        or payload.get("fullNameRu")
    )
    name_kk = (
        payload.get("nameKk")
        or payload.get("shortNameKk")
        or payload.get("tradeNameKk")
        or payload.get("trade_name_kk")
        or payload.get("name_kk")
        or payload.get("productNameKk")
    )
    brand = payload.get("brand") or payload.get("brandName") or payload.get("tradeMark")
    gtin_raw = (
        str(payload.get("gtin") or payload.get("GTIN") or payload.get("gtinCode") or "").strip()
    )
    ntin = str(payload.get("ntin") or payload.get("NTIN") or payload.get("ntinCode") or "").strip()
    category_path = (
        payload.get("categoryPath")
        or payload.get("category_path")
        or payload.get("oktruPath")
        or payload.get("classifierPath")
    )
    ancestors = payload.get("categoryAncestors")
    if isinstance(ancestors, list) and ancestors:
        parts: list[str] = []
        for node in ancestors:
            if isinstance(node, dict):
                lab = node.get("nameRu") or node.get("nameKk") or node.get("nameEn") or ""
                if lab:
                    parts.append(str(lab))
        if parts:
            category_path = " / ".join(parts)
    elif isinstance(category_path, list):
        category_path = " / ".join(str(x) for x in category_path)

    packaging = payload.get("packagingType") or payload.get("packaging_type") or payload.get("package")
    size_value = payload.get("sizeValue") or payload.get("size_value") or payload.get("volume")
    size_unit = payload.get("sizeUnit") or payload.get("size_unit") or payload.get("unit")

    attrs = payload.get("attributes")
    if isinstance(attrs, list):
        by_code: dict[str, Any] = {}
        for item in attrs:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
                if code:
                    by_code[code] = item.get("value")
        if not name_ru:
            name_ru = by_code.get("name_ru") or by_code.get("short_name_ru")
        if not name_kk:
            name_kk = by_code.get("name_kk") or by_code.get("short_name_kk")
        if brand is None:
            brand = by_code.get("brand")

    if name_ru and not isinstance(name_ru, str):
        name_ru = str(name_ru)
    if name_kk and not isinstance(name_kk, str):
        name_kk = str(name_kk)

    return {
        "name_ru": name_ru if isinstance(name_ru, str) else None,
        "name_kk": name_kk if isinstance(name_kk, str) else None,
        "brand": brand if isinstance(brand, str) else (str(brand) if brand is not None else None),
        "raw_gtin": gtin_raw or None,
        "ntin": ntin or None,
        "category_path": category_path if isinstance(category_path, str) else None,
        "packaging_type": packaging if isinstance(packaging, str) else None,
        "size_value": _safe_float(size_value),
        "size_unit": size_unit if isinstance(size_unit, str) else None,
    }


def portal_path_tin_from_gtin14(gtin14: str) -> str:
    """
    Path segment for ``/portal/api/v*/products/{{tin}}`` — portal examples use 13-digit GTIN (drop
    leading packaging digit when it is ``0`` on a 14-digit GTIN-14).
    """
    if len(gtin14) == 14 and gtin14.isdigit() and gtin14[0] == "0":
        return gtin14[1:]
    return gtin14


def _card_matches_tin(card: dict[str, Any], tin_query: str) -> bool:
    """Match portal card to the path ``tin`` we requested (GTIN / NTIN digit identity)."""
    q = strip_to_digits(tin_query)
    if not q:
        return False
    q13 = _digits_tail(q, 13)
    for key in ("gtin", "GTIN", "ntin", "NTIN", "kztin"):
        raw = card.get(key)
        if raw in (None, ""):
            continue
        d = strip_to_digits(str(raw))
        if not d:
            continue
        if d == q or d.endswith(q) or q.endswith(d):
            return True
        if len(d) >= 13 and len(q13) == 13 and _digits_tail(d, 13) == q13:
            return True
    return False


def unwrap_product_payload(
    remote: Any, *, tin_query: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """
    Portal may return ``PortalProductDto``, ``{"data": {...}}``, or (v2) an **array** of DTOs.
    When multiple cards match a GTIN, pick the row whose ``gtin``/``ntin`` matches ``tin_query``.
    """
    if remote is None:
        return None
    if isinstance(remote, list):
        dict_rows = [x for x in remote if isinstance(x, dict)]
        if not dict_rows:
            return None
        if tin_query:
            for card in dict_rows:
                if _card_matches_tin(card, tin_query):
                    return card
        return dict_rows[0]
    if isinstance(remote, dict):
        inner = remote.get("data")
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return unwrap_product_payload(inner, tin_query=tin_query)
        return remote
    return None


def _http_get_json(url: str, *, headers: dict[str, str]) -> Any | None:
    try:
        with httpx.Client(timeout=settings.national_catalog_timeout_seconds) as client:
            r = client.get(url, headers=headers)
            if r.status_code == 404:
                logger.info("national catalog miss: %s", url.split("?")[0][:80])
                return None
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return data
            logger.warning(
                "national catalog: expected JSON object or array, got %s",
                type(data).__name__,
            )
            return None
    except httpx.HTTPError as e:
        logger.warning("national catalog HTTP error for %s: %s", url[:80], e)
        return None


def fetch_product_json(*, gtin14: Optional[str] = None, ntin: Optional[str] = None) -> Any | None:
    """Call remote НКТ when templates (and credentials, if required) are configured."""
    key = settings.national_catalog_api_key.strip()
    if settings.national_catalog_auth_scheme != "none" and not key:
        return None

    hdrs: dict[str, str] = {"Accept": "application/json"}
    auth_header = settings.national_catalog_auth_header.strip()
    if settings.national_catalog_auth_scheme == "bearer":
        hdrs["Authorization"] = f"Bearer {key}"
    elif settings.national_catalog_auth_scheme == "api_key":
        hdrs[auth_header or "X-API-KEY"] = key

    base = settings.national_catalog_base_url.rstrip("/")
    if gtin14:
        tmpl = settings.national_catalog_gtin_url_template.strip()
        if not tmpl:
            return None
        tin_path = portal_path_tin_from_gtin14(gtin14)
        url = tmpl.format(
            base=base,
            tin=tin_path,
            gtin14=gtin14,
            gtin=gtin14,
            ntin="",
        )
        return _http_get_json(url, headers=hdrs)
    if ntin:
        tmpl = (
            settings.national_catalog_ntin_url_template.strip()
            or settings.national_catalog_gtin_url_template.strip()
        )
        if not tmpl:
            return None
        nt = ntin.strip()
        url = tmpl.format(base=base, tin=nt, ntin=nt, gtin14="", gtin="")
        return _http_get_json(url, headers=hdrs)
    return None


def get_master_local(db: Session, gtin_14: str) -> Optional[ProductsMaster]:
    return db.get(ProductsMaster, gtin_14)


def upsert_alias(
    db: Session,
    *,
    code: str,
    normalized_gtin_14: str,
    symbology: Optional[str],
) -> None:
    code = code.strip()
    if not code:
        return
    existing = db.query(BarcodeAlias).filter(BarcodeAlias.code == code).first()
    if existing:
        existing.normalized_gtin_14 = normalized_gtin_14
        existing.symbology = symbology
        return
    db.add(
        BarcodeAlias(
            code=code,
            normalized_gtin_14=normalized_gtin_14,
            symbology=symbology,
        )
    )


def upsert_master_from_payload(
    db: Session,
    *,
    gtin_14: str,
    payload: dict[str, Any],
    raw_response: Any,
) -> ProductsMaster:
    fields = extract_catalog_fields(payload)
    row = get_master_local(db, gtin_14)
    if isinstance(raw_response, dict):
        payload_json = dict(raw_response)
    else:
        payload_json = {"_raw": raw_response}
    now = _utcnow()
    if row:
        row.raw_gtin = fields.get("raw_gtin") or row.raw_gtin
        row.ntin = fields.get("ntin") or row.ntin
        row.brand = fields.get("brand") if fields.get("brand") is not None else row.brand
        row.name_ru = fields.get("name_ru") if fields.get("name_ru") else row.name_ru
        row.name_kk = fields.get("name_kk") if fields.get("name_kk") else row.name_kk
        row.category_path = fields.get("category_path") if fields.get("category_path") else row.category_path
        row.packaging_type = fields.get("packaging_type") if fields.get("packaging_type") else row.packaging_type
        row.size_value = fields.get("size_value") if fields.get("size_value") is not None else row.size_value
        row.size_unit = fields.get("size_unit") if fields.get("size_unit") else row.size_unit
        row.source_payload_json = payload_json
        row.last_synced_at = now
        return row

    row = ProductsMaster(
        gtin_14=gtin_14,
        raw_gtin=fields.get("raw_gtin"),
        ntin=fields.get("ntin"),
        brand=fields.get("brand"),
        name_ru=fields.get("name_ru"),
        name_kk=fields.get("name_kk"),
        category_path=fields.get("category_path"),
        packaging_type=fields.get("packaging_type"),
        size_value=fields.get("size_value"),
        size_unit=fields.get("size_unit"),
        source="nct",
        source_payload_json=payload_json,
        last_synced_at=now,
    )
    db.add(row)
    db.flush()
    return row


def resolve_product_for_scan(
    db: Session,
    *,
    normalized_gtin_14: Optional[str],
    raw_barcode: str,
    symbology: Optional[str],
) -> tuple[Optional[ProductsMaster], Optional[str], Optional[str]]:
    """
    Pull-through cache (local ``products_master`` + НКТ portal). Uses strict GTIN-14 when valid;
    otherwise falls back to a padded digit key so partial reads still query the catalog.

    Returns ``(master_row, display_name, lookup_key_14)``.
    """
    lookup_key = normalized_gtin_14 or loose_gtin14_storage_key(raw_barcode)
    if not lookup_key:
        return None, None, None

    row = get_master_local(db, lookup_key)
    if row is None:
        tin_path = portal_path_tin_from_gtin14(lookup_key)
        remote = fetch_product_json(gtin14=lookup_key)
        root = unwrap_product_payload(remote, tin_query=tin_path)
        if isinstance(root, dict):
            raw_store = remote if remote is not None else root
            upsert_master_from_payload(db, gtin_14=lookup_key, payload=root, raw_response=raw_store)
            row = get_master_local(db, lookup_key)

    upsert_alias(db, code=raw_barcode, normalized_gtin_14=lookup_key, symbology=symbology)
    if row:
        display = row.name_ru or row.name_kk or row.brand
        return row, display, lookup_key
    return None, None, lookup_key


def refresh_known_gtins(db: Session, *, limit: int = 500) -> int:
    """Re-fetch stored GTINs (oldest ``last_synced_at`` first). Used by scheduler."""
    if settings.national_catalog_auth_scheme != "none" and not settings.national_catalog_api_key.strip():
        return 0
    if not settings.national_catalog_gtin_url_template.strip():
        return 0
    rows = (
        db.query(ProductsMaster)
        .order_by(ProductsMaster.last_synced_at.asc().nullsfirst())
        .limit(limit)
        .all()
    )
    n = 0
    for row in rows:
        data = fetch_product_json(gtin14=row.gtin_14)
        tin_path = portal_path_tin_from_gtin14(row.gtin_14)
        root = unwrap_product_payload(data, tin_query=tin_path)
        if isinstance(root, dict):
            raw_store = data if data is not None else root
            upsert_master_from_payload(db, gtin_14=row.gtin_14, payload=root, raw_response=raw_store)
            n += 1
    db.commit()
    return n
