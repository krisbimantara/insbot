"""Unit tests for FrappeAuthMiddleware.

Tests cover:
- Authorized user (cache miss → Frappe returns OK → handler called)
- Unauthorized user (FrappePermissionError → "Akses ditolak" message)
- Frappe unavailable (FrappeUnavailable → "Sistem sedang sibuk" message)
- Cache hit (no Frappe call on second request within TTL)
- Unknown event type (no from_user → handler called without auth check)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.adapters.exceptions import FrappePermissionError, FrappeUnavailable
from bot.auth_middleware import FrappeAuthMiddleware, _extract_telegram_id, _reply


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings():
    """Create a mock Settings object with auth_cache_ttl_seconds = 60."""
    settings = MagicMock()
    settings.auth_cache_ttl_seconds = 60
    return settings


@pytest.fixture
def mock_frappe():
    """Create a mock FrappeClient."""
    frappe = AsyncMock()
    frappe.get_pending_list = AsyncMock(return_value=[])
    return frappe


@pytest.fixture
def middleware(mock_frappe, mock_settings):
    """Create a FrappeAuthMiddleware instance with mocked dependencies."""
    return FrappeAuthMiddleware(frappe=mock_frappe, settings=mock_settings)


@pytest.fixture
def mock_message():
    """Create a mock Message event with a from_user."""
    message = MagicMock()
    message.from_user = MagicMock()
    message.from_user.id = 123456789
    message.answer = AsyncMock()
    # Make isinstance checks work
    message.__class__ = _make_message_class()
    return message


@pytest.fixture
def mock_callback_query():
    """Create a mock CallbackQuery event with a from_user."""
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = 987654321
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.__class__ = _make_callback_query_class()
    return cb


@pytest.fixture
def mock_handler():
    """Create a mock handler that returns a sentinel value."""
    handler = AsyncMock(return_value="handler_result")
    return handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message_class():
    """Return a class that passes isinstance check for Message."""
    from aiogram.types import Message
    return Message


def _make_callback_query_class():
    """Return a class that passes isinstance check for CallbackQuery."""
    from aiogram.types import CallbackQuery
    return CallbackQuery


def _make_message_event(telegram_id: int = 123456789) -> MagicMock:
    """Create a proper mock that passes isinstance(event, Message)."""
    from aiogram.types import Message

    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = telegram_id
    msg.answer = AsyncMock()
    return msg


def _make_callback_event(telegram_id: int = 987654321) -> MagicMock:
    """Create a proper mock that passes isinstance(event, CallbackQuery)."""
    from aiogram.types import CallbackQuery

    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = MagicMock()
    cb.from_user.id = telegram_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    return cb


# ---------------------------------------------------------------------------
# Tests: _extract_telegram_id
# ---------------------------------------------------------------------------


class TestExtractTelegramId:
    def test_extracts_from_message(self):
        event = _make_message_event(123456)
        result = _extract_telegram_id(event)
        assert result == "123456"

    def test_extracts_from_callback_query(self):
        event = _make_callback_event(789012)
        result = _extract_telegram_id(event)
        assert result == "789012"

    def test_returns_none_for_message_without_from_user(self):
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.from_user = None
        result = _extract_telegram_id(event)
        assert result is None

    def test_returns_none_for_unknown_event_type(self):
        from aiogram.types import TelegramObject

        event = MagicMock(spec=TelegramObject)
        result = _extract_telegram_id(event)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: FrappeAuthMiddleware — authorized user
# ---------------------------------------------------------------------------


class TestAuthMiddlewareAuthorized:
    async def test_authorized_user_handler_called(self, middleware, mock_frappe, mock_handler):
        """When Frappe returns OK, the handler should be called."""
        event = _make_message_event(123456789)
        data: dict = {}

        result = await middleware(mock_handler, event, data)

        mock_frappe.get_pending_list.assert_awaited_once_with("123456789")
        mock_handler.assert_awaited_once_with(event, data)
        assert result == "handler_result"

    async def test_authorized_user_cached(self, middleware, mock_frappe, mock_handler):
        """Second call should use cache and not call Frappe again."""
        event = _make_message_event(123456789)
        data: dict = {}

        # First call — hits Frappe
        await middleware(mock_handler, event, data)
        assert mock_frappe.get_pending_list.await_count == 1

        # Second call — should use cache
        await middleware(mock_handler, event, data)
        assert mock_frappe.get_pending_list.await_count == 1  # still 1
        assert mock_handler.await_count == 2


# ---------------------------------------------------------------------------
# Tests: FrappeAuthMiddleware — unauthorized user
# ---------------------------------------------------------------------------


class TestAuthMiddlewareUnauthorized:
    async def test_permission_error_sends_denial_message(self, middleware, mock_frappe, mock_handler):
        """When Frappe raises FrappePermissionError, deny access."""
        mock_frappe.get_pending_list.side_effect = FrappePermissionError("No access")
        event = _make_message_event(111222333)
        data: dict = {}

        result = await middleware(mock_handler, event, data)

        mock_handler.assert_not_awaited()
        event.answer.assert_awaited_once_with("Akses ditolak. Hubungi admin.")
        assert result is None

    async def test_unauthorized_cached(self, middleware, mock_frappe, mock_handler):
        """Unauthorized result should be cached too."""
        mock_frappe.get_pending_list.side_effect = FrappePermissionError("No access")
        event = _make_message_event(111222333)
        data: dict = {}

        # First call — hits Frappe
        await middleware(mock_handler, event, data)
        assert mock_frappe.get_pending_list.await_count == 1

        # Reset side_effect to verify cache is used (not Frappe)
        mock_frappe.get_pending_list.side_effect = None
        mock_frappe.get_pending_list.return_value = []

        # Second call — should use cache (still unauthorized)
        result = await middleware(mock_handler, event, data)
        assert mock_frappe.get_pending_list.await_count == 1  # still 1
        assert result is None


# ---------------------------------------------------------------------------
# Tests: FrappeAuthMiddleware — Frappe unavailable
# ---------------------------------------------------------------------------


class TestAuthMiddlewareFrappeUnavailable:
    async def test_unavailable_sends_busy_message(self, middleware, mock_frappe, mock_handler):
        """When Frappe is unavailable, send busy message and don't call handler."""
        mock_frappe.get_pending_list.side_effect = FrappeUnavailable("Server down")
        event = _make_message_event(444555666)
        data: dict = {}

        result = await middleware(mock_handler, event, data)

        mock_handler.assert_not_awaited()
        event.answer.assert_awaited_once_with(
            "Sistem sedang sibuk, silakan coba lagi sebentar."
        )
        assert result is None

    async def test_unavailable_not_cached(self, middleware, mock_frappe, mock_handler):
        """FrappeUnavailable should NOT be cached — retry on next request."""
        mock_frappe.get_pending_list.side_effect = FrappeUnavailable("Server down")
        event = _make_message_event(444555666)
        data: dict = {}

        # First call — Frappe unavailable
        await middleware(mock_handler, event, data)

        # Now Frappe recovers
        mock_frappe.get_pending_list.side_effect = None
        mock_frappe.get_pending_list.return_value = []

        # Second call — should hit Frappe again (not cached)
        result = await middleware(mock_handler, event, data)
        assert mock_frappe.get_pending_list.await_count == 2
        assert result == "handler_result"

    async def test_unavailable_callback_query(self, middleware, mock_frappe, mock_handler):
        """FrappeUnavailable with CallbackQuery should answer callback and send message."""
        mock_frappe.get_pending_list.side_effect = FrappeUnavailable("Server down")
        event = _make_callback_event(777888999)
        data: dict = {}

        result = await middleware(mock_handler, event, data)

        mock_handler.assert_not_awaited()
        event.answer.assert_awaited_once()
        event.message.answer.assert_awaited_once_with(
            "Sistem sedang sibuk, silakan coba lagi sebentar."
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: FrappeAuthMiddleware — event without telegram_id
# ---------------------------------------------------------------------------


class TestAuthMiddlewareNoTelegramId:
    async def test_no_from_user_passes_through(self, middleware, mock_frappe, mock_handler):
        """Events without a determinable telegram_id should pass through."""
        from aiogram.types import TelegramObject

        event = MagicMock(spec=TelegramObject)
        data: dict = {}

        result = await middleware(mock_handler, event, data)

        mock_frappe.get_pending_list.assert_not_awaited()
        mock_handler.assert_awaited_once_with(event, data)
        assert result == "handler_result"
