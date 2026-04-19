from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session, joinedload

from backend.app.config import settings
from backend.app.models.entities import AppSetting, Item, ItemStatus


async def send_telegram(text: str) -> bool:
    token = settings.telegram_bot_token.strip()
    chat = settings.telegram_chat_id.strip()
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json={"chat_id": chat, "text": text})
        return r.is_success


def _chat_id_from_db(db: Session) -> Optional[str]:
    row = db.query(AppSetting).filter(AppSetting.key == "telegram_chat_id").first()
    if row and row.value.strip():
        return row.value.strip()
    cid = settings.telegram_chat_id.strip()
    return cid or None


async def notify_digest_if_needed(db: Session, *, today: Optional[date] = None) -> None:
    """Morning-style summary (deduped once per calendar day via tag)."""
    chat = _chat_id_from_db(db)
    token = settings.telegram_bot_token.strip()
    if not chat or not token:
        return
    day = today or datetime.now(timezone.utc).date()
    tag = f"daily-{day.isoformat()}"
    row = db.query(AppSetting).filter(AppSetting.key == "last_daily_digest_tag").first()
    if row and row.value == tag:
        return

    tomorrow = day + timedelta(days=1)
    soon_cutoff = day + timedelta(days=settings.expiring_warning_days)

    items = (
        db.query(Item)
        .options(joinedload(Item.product))
        .filter(Item.status.not_in([ItemStatus.consumed, ItemStatus.discarded]))
        .all()
    )

    tomorrow_names: list[str] = []
    expired_today: list[str] = []
    soon_names: list[str] = []

    for item in items:
        name = item.product.canonical_name
        ed = item.expiry_date
        if ed is None:
            continue
        if ed == tomorrow:
            tomorrow_names.append(name)
        if ed < day:
            expired_today.append(name)
        if day < ed <= soon_cutoff:
            soon_names.append(name)

    parts: list[str] = []
    if tomorrow_names:
        parts.append(f"{len(tomorrow_names)} item(s) expire tomorrow: {', '.join(sorted(set(tomorrow_names)))}")
    if expired_today:
        parts.append(f"{len(expired_today)} item(s) already expired: {', '.join(sorted(set(expired_today)))}")
    if soon_names:
        parts.append(f"Use soon: {', '.join(sorted(set(soon_names)))}")

    if not parts:
        parts.append("No urgent expiry items today.")

    text = "Smart Fridge — " + day.isoformat() + "\n" + "\n".join(parts)
    ok = await send_telegram(text)
    if ok:
        if row:
            row.value = tag
        else:
            db.add(AppSetting(key="last_daily_digest_tag", value=tag))
        db.commit()


async def notify_immediate_events(db: Session, *, today: Optional[date] = None) -> None:
    """Alerts for newly expired / expiring-soon transitions with per-item dedupe."""
    chat = _chat_id_from_db(db)
    token = settings.telegram_bot_token.strip()
    if not chat or not token:
        return

    day = today or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)

    items = (
        db.query(Item)
        .options(joinedload(Item.product))
        .filter(Item.status.not_in([ItemStatus.consumed, ItemStatus.discarded]))
        .all()
    )

    expired_names: list[str] = []
    warn_names: list[str] = []

    for item in items:
        ed = item.expiry_date
        if ed is None:
            continue
        name = item.product.canonical_name

        if item.status == ItemStatus.expired and ed <= day:
            last = item.last_expiry_notification_at
            if last is None or last.date() != day:
                expired_names.append(name)
                item.last_expiry_notification_at = now

        warn_until = day + timedelta(days=settings.expiring_warning_days)
        if item.status == ItemStatus.expiring and day < ed <= warn_until:
            last = item.last_expiry_notification_at
            if last is None or last.date() != day:
                warn_names.append(name)
                item.last_expiry_notification_at = now

    msgs: list[str] = []
    if expired_names:
        msgs.append(
            f"{len(expired_names)} item(s) expired (action needed): {', '.join(sorted(set(expired_names)))}"
        )
    if warn_names:
        msgs.append(f"Expiring soon: {', '.join(sorted(set(warn_names)))}")

    if msgs:
        await send_telegram("Smart Fridge alert\n" + "\n".join(msgs))
        db.commit()
