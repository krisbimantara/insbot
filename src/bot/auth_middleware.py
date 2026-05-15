"""Frappe authentication middleware for aiogram 3.x.

Validates every incoming Telegram update against Frappe's ``get_pending_list``
endpoint before allowing handler execution. Results are cached in-memory with
a TTL of 60 seconds to avoid excessive Frappe calls during message bursts.

Webhook requests (handled by the aiohttp server, not the aiogram dispatcher)
bypass this middleware entirely — they are authenticated via the shared secret
header on the HTTP layer.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from cachetools import TTLCache

from bot.adapters.exceptions import FrappePermissionError, FrappeUnavailable
from bot.adapters.frappe import FrappeClient
from bot.config import Settings

logger = logging.getLogger(__name__)


def _extract_telegram_id(event: TelegramObject) -> str | None:
    """Extract the sender's telegram_id from an aiogram event.

    Returns the string telegram_id for Message and CallbackQuery events,
    or None if the event type doesn't carry user information (e.g. channel
    posts, chat member updates without a clear sender).
    """
    if isinstance(event, Message):
        if event.from_user is not None:
            return str(event.from_user.id)
    elif isinstance(event, CallbackQuery):
        if event.from_user is not None:
            return str(event.from_user.id)
    return None


async def _reply(event: TelegramObject, text: str) -> None:
    """Send a reply message to the user who triggered the event."""
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        # Answer the callback to dismiss the loading indicator, then send a message
        await event.answer()
        if event.message is not None:
            await event.message.answer(text)  # type: ignore[union-attr]


class FrappeAuthMiddleware(BaseMiddleware):
    """aiogram middleware that validates telegram_id against Frappe.

    On cache miss, calls ``FrappeClient.get_pending_list(telegram_id)`` to
    verify the user is a registered inspector. The result (authorized or not)
    is cached for ``auth_cache_ttl_seconds`` (default 60s) to reduce load on
    Frappe during burst interactions.

    Error handling:
    - FrappePermissionError → user is unauthorized → "Akses ditolak. Hubungi admin."
    - FrappeUnavailable → Frappe is down → "Sistem sedang sibuk, silakan coba lagi sebentar."
    """

    def __init__(self, frappe: FrappeClient, settings: Settings) -> None:
        self._frappe = frappe
        self._cache: TTLCache[str, bool] = TTLCache(
            maxsize=1024,
            ttl=settings.auth_cache_ttl_seconds,
        )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Execute the auth check before passing control to the handler."""
        telegram_id = _extract_telegram_id(event)

        # If we can't determine the sender, let the handler decide what to do
        if telegram_id is None:
            return await handler(event, data)

        try:
            ok = await self._is_authorized(telegram_id)
        except FrappeUnavailable:
            logger.warning(
                "frappe_unavailable_during_auth",
                extra={"telegram_id": telegram_id},
            )
            await _reply(event, "Sistem sedang sibuk, silakan coba lagi sebentar.")
            return None

        if not ok:
            logger.info(
                "auth_denied",
                extra={"telegram_id": telegram_id},
            )
            await _reply(event, "Akses ditolak. Hubungi admin.")
            return None

        return await handler(event, data)

    async def _is_authorized(self, telegram_id: str) -> bool:
        """Check if the telegram_id is authorized via Frappe.

        Uses the in-memory TTLCache to avoid repeated Frappe calls.
        A successful ``get_pending_list`` call (no PermissionError) means
        the user is registered as an inspector in Frappe.

        Raises:
            FrappeUnavailable: If Frappe is unreachable (5xx / network error).
        """
        cached = self._cache.get(telegram_id)
        if cached is not None:
            return cached

        try:
            await self._frappe.get_pending_list(telegram_id)
            self._cache[telegram_id] = True
            return True
        except FrappePermissionError:
            self._cache[telegram_id] = False
            return False
        # FrappeUnavailable is NOT caught here — it propagates to __call__
