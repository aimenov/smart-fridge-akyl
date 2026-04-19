import pytest

from backend.app.modules.notification_service import send_telegram


@pytest.mark.asyncio
async def test_send_telegram_false_without_credentials():
    assert await send_telegram("hello") is False
