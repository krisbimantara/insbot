"""Unit tests for the webhook server (src/bot/webhook.py).

Tests cover:
- Valid webhook processing (200 response, Redis updated, Telegram notified)
- Invalid event (400 "Unknown event")
- Missing fields (400 "Missing field: ...")
- Failed Telegram send (still 200, Redis not rolled back)
- Shared secret validation (403 on mismatch, accept when not configured)
- Healthz endpoint (200 when Redis healthy, 503 when not)

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 11.5, 12.6
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from bot.webhook import create_webhook_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(shared_secret: str | None = "test-secret"):
    """Create a mock Settings object."""
    settings = MagicMock()
    if shared_secret is not None:
        settings.webhook_shared_secret = MagicMock(spec=SecretStr)
        settings.webhook_shared_secret.get_secret_value.return_value = shared_secret
    else:
        settings.webhook_shared_secret = None
    return settings


def _make_session_store(ping_result: bool = True):
    """Create a mock RedisSessionStore."""
    store = AsyncMock()
    store.add_pending = AsyncMock()
    store.ping = AsyncMock(return_value=ping_result)
    return store


def _make_bot(send_raises: Exception | None = None):
    """Create a mock aiogram Bot."""
    bot = AsyncMock()
    if send_raises:
        bot.send_message = AsyncMock(side_effect=send_raises)
    else:
        bot.send_message = AsyncMock()
    return bot


def _valid_payload() -> dict:
    """Return a valid webhook payload."""
    return {
        "event": "inspection_requested",
        "motor_tarikan": "PJ-001",
        "nopol": "B 1234 XYZ",
        "merk": "Honda",
        "model": "Beat",
        "tahun": "2022",
        "warna": "Merah",
        "tipe_inspeksi": "Inspeksi",
        "inspector_chat_id": "123456789",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings():
    return _make_settings()


@pytest.fixture
def session_store():
    return _make_session_store()


@pytest.fixture
def bot():
    return _make_bot()


@pytest.fixture
async def client(settings, session_store, bot):
    """Create an aiohttp TestClient for the webhook app."""
    app = create_webhook_app(settings, session_store, bot)
    async with TestClient(TestServer(app)) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /webhook/inspection-request tests
# ---------------------------------------------------------------------------


class TestWebhookEndpoint:
    """Tests for POST /webhook/inspection-request."""

    async def test_valid_webhook_returns_200(self, client: TestClient):
        """Valid payload returns 200 OK."""
        resp = await client.post(
            "/webhook/inspection-request",
            json=_valid_payload(),
            headers={"X-Inspection-Webhook-Secret": "test-secret"},
        )
        assert resp.status == 200
        text = await resp.text()
        assert text == "OK"

    async def test_valid_webhook_adds_to_pending(self, session_store):
        """Valid payload adds motor_tarikan to pending via Redis."""
        settings = _make_settings()
        bot = _make_bot()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            await c.post(
                "/webhook/inspection-request",
                json=_valid_payload(),
                headers={"X-Inspection-Webhook-Secret": "test-secret"},
            )
        session_store.add_pending.assert_awaited_once_with("123456789", "PJ-001")

    async def test_valid_webhook_sends_telegram_notification(self, bot):
        """Valid payload sends Telegram notification to inspector_chat_id."""
        settings = _make_settings()
        session_store = _make_session_store()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            await c.post(
                "/webhook/inspection-request",
                json=_valid_payload(),
                headers={"X-Inspection-Webhook-Secret": "test-secret"},
            )
        bot.send_message.assert_awaited_once()
        call_args = bot.send_message.call_args
        # Check chat_id was passed correctly
        assert call_args.kwargs.get("chat_id") == 123456789

    async def test_notification_contains_motor_info(self, bot):
        """Notification text includes merk, model, tahun, nopol, tipe_inspeksi."""
        settings = _make_settings()
        session_store = _make_session_store()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            await c.post(
                "/webhook/inspection-request",
                json=_valid_payload(),
                headers={"X-Inspection-Webhook-Secret": "test-secret"},
            )
        call_args = bot.send_message.call_args
        text = call_args.kwargs.get("text")
        assert "Honda" in text
        assert "Beat" in text
        assert "2022" in text
        assert "B 1234 XYZ" in text
        assert "Inspeksi" in text
        assert "/mulai" in text

    async def test_unknown_event_returns_400(self, client: TestClient):
        """Payload with event != 'inspection_requested' returns 400."""
        payload = _valid_payload()
        payload["event"] = "something_else"
        resp = await client.post(
            "/webhook/inspection-request",
            json=payload,
            headers={"X-Inspection-Webhook-Secret": "test-secret"},
        )
        assert resp.status == 400
        text = await resp.text()
        assert text == "Unknown event"

    async def test_missing_event_returns_400(self, client: TestClient):
        """Payload without event field returns 400 'Unknown event'."""
        payload = _valid_payload()
        del payload["event"]
        resp = await client.post(
            "/webhook/inspection-request",
            json=payload,
            headers={"X-Inspection-Webhook-Secret": "test-secret"},
        )
        assert resp.status == 400
        text = await resp.text()
        assert text == "Unknown event"

    async def test_missing_motor_tarikan_returns_400(self, session_store):
        """Payload without motor_tarikan returns 400 with field name."""
        settings = _make_settings()
        bot = _make_bot()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            payload = _valid_payload()
            del payload["motor_tarikan"]
            resp = await c.post(
                "/webhook/inspection-request",
                json=payload,
                headers={"X-Inspection-Webhook-Secret": "test-secret"},
            )
            assert resp.status == 400
            text = await resp.text()
            assert "motor_tarikan" in text
        # Redis should NOT be modified
        session_store.add_pending.assert_not_awaited()

    async def test_missing_inspector_chat_id_returns_400(self, session_store):
        """Payload without inspector_chat_id returns 400 with field name."""
        settings = _make_settings()
        bot = _make_bot()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            payload = _valid_payload()
            del payload["inspector_chat_id"]
            resp = await c.post(
                "/webhook/inspection-request",
                json=payload,
                headers={"X-Inspection-Webhook-Secret": "test-secret"},
            )
            assert resp.status == 400
            text = await resp.text()
            assert "inspector_chat_id" in text
        session_store.add_pending.assert_not_awaited()

    async def test_missing_both_fields_returns_400(self, client: TestClient):
        """Payload missing both required fields lists both in error."""
        payload = _valid_payload()
        del payload["motor_tarikan"]
        del payload["inspector_chat_id"]
        resp = await client.post(
            "/webhook/inspection-request",
            json=payload,
            headers={"X-Inspection-Webhook-Secret": "test-secret"},
        )
        assert resp.status == 400
        text = await resp.text()
        assert "motor_tarikan" in text
        assert "inspector_chat_id" in text

    async def test_telegram_send_failure_still_returns_200(self):
        """If Telegram sendMessage fails, still return 200 (Requirement 1.6)."""
        settings = _make_settings()
        session_store = _make_session_store()
        bot = _make_bot(send_raises=Exception("Telegram API error"))
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(
                "/webhook/inspection-request",
                json=_valid_payload(),
                headers={"X-Inspection-Webhook-Secret": "test-secret"},
            )
            assert resp.status == 200
        # Redis should still have the pending entry
        session_store.add_pending.assert_awaited_once_with("123456789", "PJ-001")

    async def test_invalid_secret_returns_403(self, client: TestClient):
        """Wrong shared secret returns 403 Forbidden."""
        resp = await client.post(
            "/webhook/inspection-request",
            json=_valid_payload(),
            headers={"X-Inspection-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status == 403
        text = await resp.text()
        assert text == "Forbidden"

    async def test_missing_secret_header_returns_403(self, client: TestClient):
        """Missing shared secret header returns 403 when secret is configured."""
        resp = await client.post(
            "/webhook/inspection-request",
            json=_valid_payload(),
        )
        assert resp.status == 403

    async def test_no_configured_secret_accepts_all(self):
        """When WEBHOOK_SHARED_SECRET is not set, accept all requests (Requirement 12.6)."""
        settings = _make_settings(shared_secret=None)
        session_store = _make_session_store()
        bot = _make_bot()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(
                "/webhook/inspection-request",
                json=_valid_payload(),
                # No secret header
            )
            assert resp.status == 200

    async def test_invalid_json_returns_400(self, client: TestClient):
        """Non-JSON body returns 400."""
        resp = await client.post(
            "/webhook/inspection-request",
            data="not json",
            headers={
                "X-Inspection-Webhook-Secret": "test-secret",
                "Content-Type": "application/json",
            },
        )
        assert resp.status == 400
        text = await resp.text()
        assert "Invalid JSON" in text


# ---------------------------------------------------------------------------
# GET /healthz tests
# ---------------------------------------------------------------------------


class TestHealthzEndpoint:
    """Tests for GET /healthz."""

    async def test_healthz_returns_200_when_redis_healthy(self, client: TestClient):
        """Returns 200 with status ok when Redis PING succeeds."""
        resp = await client.get("/healthz")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"status": "ok"}

    async def test_healthz_returns_503_when_redis_unhealthy(self):
        """Returns 503 with status unavailable when Redis PING fails."""
        settings = _make_settings()
        session_store = _make_session_store(ping_result=False)
        bot = _make_bot()
        app = create_webhook_app(settings, session_store, bot)
        async with TestClient(TestServer(app)) as c:
            resp = await c.get("/healthz")
            assert resp.status == 503
            body = await resp.json()
            assert body == {"status": "unavailable"}
