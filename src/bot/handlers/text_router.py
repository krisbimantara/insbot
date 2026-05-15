"""Unified text message router for the Telegram Inspection Bot.

Routes text messages to the appropriate handler based on the current session phase.
This solves the problem of multiple routers with @router.message(F.text) where
only the first registered one gets called in aiogram 3.x.

The router checks the user's active session phase and delegates to:
- CHECKLIST → checklist handler
- STNK_CONDITIONAL → STNK handler
- REVISION → summary/revision handler
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message
from redis.exceptions import RedisError

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.models import Phase, Session

logger = logging.getLogger(__name__)

router = Router(name="text_router")


async def _find_active_session_any_phase(
    telegram_id: str,
    store: RedisSessionStore,
) -> Session | None:
    """Find any active session for the user (any phase)."""
    try:
        pending = await store.list_pending(telegram_id)
    except RedisError:
        return None

    for motor_id in pending:
        try:
            session = await store.get_session(telegram_id, motor_id)
        except RedisError:
            continue
        if session is not None and session.phase in (
            Phase.CHECKLIST,
            Phase.STNK_CONDITIONAL,
            Phase.REVISION,
        ):
            return session
    return None


@router.message(F.text)
async def handle_text_by_phase(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Route text messages based on the active session's phase."""
    telegram_id = str(message.from_user.id)

    session = await _find_active_session_any_phase(telegram_id, session_store)
    if session is None:
        return  # No active text-input session, ignore

    if session.phase == Phase.CHECKLIST:
        from bot.handlers.checklist import handle_checklist_answer
        await handle_checklist_answer(message, session_store)

    elif session.phase == Phase.STNK_CONDITIONAL:
        from bot.handlers.stnk import handle_stnk_conditional_text
        await handle_stnk_conditional_text(message, session_store)

    elif session.phase == Phase.REVISION:
        from bot.handlers.summary import handle_revision_answer
        await handle_revision_answer(message, session_store)
