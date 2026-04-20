import pytest

from backend.app.modules.notification_service import notify_digest_if_needed, notify_immediate_events


@pytest.mark.asyncio
async def test_notify_digest_skips_without_telegram(db_session):
    await notify_digest_if_needed(db_session)


@pytest.mark.asyncio
async def test_notify_immediate_skips_without_telegram(db_session):
    await notify_immediate_events(db_session)


def test_scans_recent_empty(client):
    r = client.get("/api/scans/recent")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
