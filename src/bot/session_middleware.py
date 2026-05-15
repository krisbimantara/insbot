"""Session expiry and reassignment middleware for the Telegram Inspection Bot.

Provides:
- A utility function to check for expired sessions and respond with the
  standard expiry message (Requirement 9.6).
- A utility function to detect motor reassignment (Requirement 15.2).
- A middleware that injects `active_session` into handler data for callback
  queries that reference a motor_id.

Requirements: 9.6, 15.1, 15.2, 15.3, 15.4
"""

from __future__ import annotations

import logging

from aiogram.types import CallbackQuery, Message

from bot.adapters.redis_store import RedisSessionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_EXPIRED_MESSAGE = (
    "Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."
)

MOTOR_REASSIGNED_MESSAGE = (
    "Motor ini sudah dialihkan ke inspektor lain."
)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


async def check_session_expired(
    target: CallbackQuery | Message,
    session_or_none,
) -> bool:
    """Check if a session is expired (None) and send the expiry message.

    Returns True if the session is expired (caller should stop processing).
    Returns False if the session is valid (caller should continue).

    Requirement 9.6: When session expired (TTL habis) or deleted, if inspector
    sends callback referencing that session, show the expiry message.
    """
    if session_or_none is not None:
        return False

    # Session is expired — send the standard message
    # Use duck typing: CallbackQuery has a `message` attribute and a no-arg `answer()`
    # while Message's `answer()` takes text directly.
    if hasattr(target, "message") and hasattr(target, "data"):
        # CallbackQuery-like: acknowledge the callback, then send message
        await target.answer()
        if target.message is not None:
            await target.message.answer(SESSION_EXPIRED_MESSAGE)  # type: ignore[union-attr]
    else:
        # Message-like: reply directly
        await target.answer(SESSION_EXPIRED_MESSAGE)

    return True


async def check_motor_reassigned(
    target: CallbackQuery | Message,
    telegram_id: str,
    motor_id: str,
    store: RedisSessionStore,
) -> bool:
    """Check if a motor has been reassigned (no longer in pending set).

    Returns True if the motor was reassigned (caller should stop processing).
    Returns False if the motor is still in the pending set.

    Requirement 15.2: If inspector has an active session for a motor that's
    been reassigned, show the reassignment message and delete the session.
    """
    pending = await store.list_pending(telegram_id)

    if motor_id in pending:
        return False

    # Motor is no longer in pending — it was reassigned
    # Delete the stale session
    await store.delete_session(telegram_id, motor_id)

    logger.info(
        "motor_reassigned",
        extra={
            "telegram_id": telegram_id,
            "motor_id": motor_id,
        },
    )

    # Use duck typing: CallbackQuery has `data` attribute, Message does not
    if hasattr(target, "message") and hasattr(target, "data"):
        # CallbackQuery-like
        await target.answer()
        if target.message is not None:
            await target.message.answer(MOTOR_REASSIGNED_MESSAGE)  # type: ignore[union-attr]
    else:
        # Message-like
        await target.answer(MOTOR_REASSIGNED_MESSAGE)

    return True
