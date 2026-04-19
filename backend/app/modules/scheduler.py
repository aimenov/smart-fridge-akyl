from __future__ import annotations

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from backend.app.database import SessionLocal
from backend.app.modules import inventory_service, notification_service

logger = logging.getLogger(__name__)


def _session() -> Session:
    return SessionLocal()


async def tick_inventory_and_notifications() -> None:
    db = _session()
    try:
        inventory_service.reconcile_all_items(db)
        await notification_service.notify_immediate_events(db)
    except Exception:
        logger.exception("scheduler tick failed")
    finally:
        db.close()


async def morning_digest() -> None:
    db = _session()
    try:
        inventory_service.reconcile_all_items(db)
        await notification_service.notify_digest_if_needed(db)
    except Exception:
        logger.exception("morning digest failed")
    finally:
        db.close()


def start_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        tick_inventory_and_notifications,
        "interval",
        minutes=15,
        id="inventory_notify",
        replace_existing=True,
    )
    sched.add_job(
        morning_digest,
        "cron",
        hour=7,
        minute=30,
        id="morning_digest",
        replace_existing=True,
    )
    sched.start()
    return sched
