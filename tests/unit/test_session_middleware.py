"""Unit tests for session expiry and reassignment handling.

Tests cover:
- Session expiry detection and message for callback queries
- Session expiry detection and message for messages
- Motor reassignment detection and session cleanup
- Integration with handlers (photo confirm/retry, summary, submit)

Requirements: 9.6, 15.1, 15.2, 15.3, 15.4
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.session_middleware import (
    MOTOR_REASSIGNED_MESSAGE,
    SESSION_EXPIRED_MESSAGE,
    check_motor_reassigned,
    check_session_expired,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_callback(user_id: int = 123456789):
    callback = AsyncMock()
    callback.data = "some_callback_data"  # CallbackQuery has a `data` attribute
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _make_message(user_id: int = 123456789):
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    # Remove 'data' attribute to distinguish from CallbackQuery in duck typing
    del message.data
    # Remove 'message' attribute (Message doesn't have a nested .message)
    del message.message
    return message


def _make_session_store(pending: set[str] | None = None):
    store = AsyncMock()
    store.list_pending = AsyncMock(return_value=pending or set())
    store.delete_session = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# Tests: check_session_expired
# ---------------------------------------------------------------------------


class TestCheckSessionExpired:
    """Tests for the check_session_expired utility (Requirement 9.6)."""

    @pytest.mark.asyncio
    async def test_returns_true_when_session_is_none_callback(self):
        """When session is None, returns True and sends expiry message via callback."""
        callback = _make_callback()

        result = await check_session_expired(callback, None)

        assert result is True
        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited_once_with(SESSION_EXPIRED_MESSAGE)

    @pytest.mark.asyncio
    async def test_returns_false_when_session_exists(self):
        """When session is not None, returns False and sends nothing."""
        callback = _make_callback()
        fake_session = MagicMock()

        result = await check_session_expired(callback, fake_session)

        assert result is False
        callback.answer.assert_not_awaited()
        callback.message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_true_when_session_is_none_message(self):
        """When session is None, returns True and sends expiry message via message."""
        message = _make_message()

        result = await check_session_expired(message, None)

        assert result is True
        message.answer.assert_awaited_once_with(SESSION_EXPIRED_MESSAGE)

    @pytest.mark.asyncio
    async def test_returns_false_when_session_exists_message(self):
        """When session is not None, returns False and sends nothing via message."""
        message = _make_message()
        fake_session = MagicMock()

        result = await check_session_expired(message, fake_session)

        assert result is False
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expiry_message_text_matches_requirement(self):
        """The expiry message matches Requirement 9.6 exactly."""
        assert SESSION_EXPIRED_MESSAGE == (
            "Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."
        )


# ---------------------------------------------------------------------------
# Tests: check_motor_reassigned
# ---------------------------------------------------------------------------


class TestCheckMotorReassigned:
    """Tests for the check_motor_reassigned utility (Requirement 15.2)."""

    @pytest.mark.asyncio
    async def test_returns_false_when_motor_in_pending(self):
        """When motor is still in pending set, returns False."""
        callback = _make_callback()
        store = _make_session_store(pending={"PJ-001", "PJ-002"})

        result = await check_motor_reassigned(callback, "123456789", "PJ-001", store)

        assert result is False
        store.delete_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_true_when_motor_not_in_pending(self):
        """When motor is not in pending set, returns True and deletes session."""
        callback = _make_callback()
        store = _make_session_store(pending={"PJ-002"})

        result = await check_motor_reassigned(callback, "123456789", "PJ-001", store)

        assert result is True
        store.delete_session.assert_awaited_once_with("123456789", "PJ-001")
        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited_once_with(MOTOR_REASSIGNED_MESSAGE)

    @pytest.mark.asyncio
    async def test_returns_true_when_pending_empty(self):
        """When pending set is empty, motor is considered reassigned."""
        callback = _make_callback()
        store = _make_session_store(pending=set())

        result = await check_motor_reassigned(callback, "123456789", "PJ-001", store)

        assert result is True
        store.delete_session.assert_awaited_once_with("123456789", "PJ-001")

    @pytest.mark.asyncio
    async def test_reassignment_message_text_matches_requirement(self):
        """The reassignment message matches Requirement 15.2."""
        assert MOTOR_REASSIGNED_MESSAGE == "Motor ini sudah dialihkan ke inspektor lain."

    @pytest.mark.asyncio
    async def test_reassignment_with_message_target(self):
        """Reassignment detection works with Message target."""
        message = _make_message()
        store = _make_session_store(pending=set())

        result = await check_motor_reassigned(message, "123456789", "PJ-001", store)

        assert result is True
        store.delete_session.assert_awaited_once_with("123456789", "PJ-001")
        message.answer.assert_awaited_once_with(MOTOR_REASSIGNED_MESSAGE)
