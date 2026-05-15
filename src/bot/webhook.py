"""Webhook server for the Telegram Inspection Bot.

Provides an aiohttp web application with two endpoints:

- ``POST /webhook/inspection-request`` — receives inspection requests from
  Frappe, validates the shared secret and payload, updates Redis pending queue,
  and sends a Telegram notification to the assigned inspector.
- ``GET /healthz`` — returns 200 if Redis is reachable, 503 otherwise.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 11.5, 12.6
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiohttp import web

from bot.logging import get_logger, log_inspection_requested

if TYPE_CHECKING:
    from aiogram import Bot

    from bot.adapters.redis_store import RedisSessionStore
    from bot.config import Settings

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Required fields in the webhook payload (Requirement 1.1, 1.5)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = ("motor_tarikan", "inspector_chat_id")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_inspection_webhook(request: web.Request) -> web.Response:
    """Handle ``POST /webhook/inspection-request``.

    Validation order:
    1. Shared secret header (Requirement 12.6)
    2. Event type (Requirement 1.4)
    3. Required fields (Requirement 1.5)

    On valid payload:
    - Add motor_tarikan to pending via Redis (Requirement 1.2, 1.8)
    - Send Telegram notification (Requirement 1.3)
    - If sendMessage fails: log WARNING, don't rollback Redis, still return 200
      (Requirement 1.6)
    - Return 200 (Requirement 1.7)
    """
    settings: Settings = request.app["settings"]
    session_store: RedisSessionStore = request.app["session_store"]
    bot: Bot = request.app["bot"]

    # 1. Validate shared secret header (Requirement 12.6)
    if settings.webhook_shared_secret is not None:
        provided_secret = request.headers.get("X-Inspection-Webhook-Secret", "")
        if provided_secret != settings.webhook_shared_secret.get_secret_value():
            return web.Response(status=403, text="Forbidden")

    # Parse JSON body
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    # 2. Validate event type (Requirement 1.4)
    if data.get("event") != "inspection_requested":
        return web.Response(status=400, text="Unknown event")

    # 3. Validate required fields (Requirement 1.5)
    missing = [f for f in _REQUIRED_FIELDS if not data.get(f)]
    if missing:
        return web.Response(status=400, text=f"Missing field: {', '.join(missing)}")

    chat_id = str(data["inspector_chat_id"])
    motor = str(data["motor_tarikan"])

    # 4. Add to pending via Redis — idempotent SADD (Requirement 1.2, 1.8)
    await session_store.add_pending(chat_id, motor)

    # 5. Audit log (Requirement 13.1)
    received_at = datetime.now(tz=timezone.utc)
    log_inspection_requested(
        motor_tarikan=motor,
        inspector_chat_id=chat_id,
        tipe_inspeksi=str(data.get("tipe_inspeksi", "")),
        received_at=received_at,
    )

    # 6. Send Telegram notification (Requirement 1.3)
    try:
        notification_text = _build_notification_text(data)
        await bot.send_message(chat_id=int(chat_id), text=notification_text)
    except Exception as exc:
        # Requirement 1.6: log WARNING, don't rollback Redis, still return 200
        log.warning(
            "notify_failed",
            error=str(exc),
            chat_id=chat_id,
            motor_tarikan=motor,
        )

    return web.Response(status=200, text="OK")


async def handle_healthz(request: web.Request) -> web.Response:
    """Handle ``GET /healthz``.

    Returns 200 with ``{"status":"ok"}`` if Redis PING succeeds,
    503 with ``{"status":"unavailable"}`` otherwise (Requirement 11.5).
    """
    session_store: RedisSessionStore = request.app["session_store"]

    healthy = await session_store.ping()
    if healthy:
        return web.json_response({"status": "ok"}, status=200)
    else:
        return web.json_response({"status": "unavailable"}, status=503)


# ---------------------------------------------------------------------------
# Notification text builder
# ---------------------------------------------------------------------------


def _build_notification_text(data: dict) -> str:
    """Build the Telegram notification message for an inspection request.

    Includes merk, model, tahun, nopol, tipe_inspeksi, and instruction to
    type /mulai (Requirement 1.3).
    """
    merk = data.get("merk", "-")
    model = data.get("model", "-")
    tahun = data.get("tahun", "-")
    nopol = data.get("nopol", "-")
    tipe_inspeksi = data.get("tipe_inspeksi", "-")

    return (
        "🔔 Permintaan Inspeksi Baru\n\n"
        f"Motor: {merk} {model} {tahun}\n"
        f"Nopol: {nopol}\n"
        f"Tipe: {tipe_inspeksi}\n\n"
        "Ketik /mulai untuk melihat daftar motor."
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_webhook_app(
    settings: Settings,
    session_store: RedisSessionStore,
    bot: Bot,
) -> web.Application:
    """Create and return the aiohttp web application for the webhook server.

    The app exposes:
    - ``POST /webhook/inspection-request``
    - ``GET /healthz``

    Parameters
    ----------
    settings:
        Application settings (used for shared secret validation).
    session_store:
        Redis session store (used for add_pending and health check).
    bot:
        aiogram Bot instance (used for sending Telegram notifications).

    Returns
    -------
    web.Application
        A configured aiohttp application ready to be run.
    """
    app = web.Application()

    # Store dependencies in app dict for handler access
    app["settings"] = settings
    app["session_store"] = session_store
    app["bot"] = bot

    # Register routes
    app.router.add_post("/webhook/inspection-request", handle_inspection_webhook)
    app.router.add_get("/healthz", handle_healthz)

    return app
