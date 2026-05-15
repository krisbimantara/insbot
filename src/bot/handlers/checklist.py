"""Checklist handler for the Telegram Inspection Bot.

Handles text messages during phase=CHECKLIST:
- Display component questions one by one with Reply Keyboard (Baik/Cukup/Rusak or fuel options)
- Validate answer against valid option set; re-display on invalid
- Save answer to Redis before advancing; handle Redis failure
- Show progress bar [████████░░] {done}/{total}
- Handle category transitions and completion
- Send ReplyKeyboardRemove when leaving checklist phase

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from redis.exceptions import RedisError

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.checklist import (
    CATEGORY_FOR_FIELD,
    Done,
    apply_answer,
    next_question,
)
from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    COMPONENT_OPTIONS,
    Phase,
    Session,
)
from bot.domain.progress import compute_overall_progress, render_progress_bar

logger = logging.getLogger(__name__)

router = Router(name="checklist")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _build_reply_keyboard(options: tuple[str, ...]) -> ReplyKeyboardMarkup:
    """Build a Reply Keyboard from the given options.

    Uses one_time_keyboard=True and resize_keyboard=True per Requirement 4.3.
    """
    buttons = [[KeyboardButton(text=opt) for opt in options]]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        one_time_keyboard=True,
        resize_keyboard=True,
    )


def _format_question_message(
    label: str,
    category: str,
    done: int,
    total: int,
) -> str:
    """Format the question message with label, category, and progress bar.

    Format per Requirement 4.3:
    - Category name
    - Component label
    - Progress bar [████████░░] {done}/{total}
    """
    progress_bar = render_progress_bar(done, total)
    return (
        f"📋 *{category}*\n\n"
        f"Komponen: *{label}*\n\n"
        f"Progress: {progress_bar}"
    )


async def _find_active_checklist_session(
    telegram_id: str,
    store: RedisSessionStore,
) -> Session | None:
    """Find the active session in CHECKLIST phase for the given telegram_id.

    Iterates through pending motors to find a session with phase=CHECKLIST.
    Returns None if no active checklist session is found.
    """
    try:
        pending = await store.list_pending(telegram_id)
    except RedisError:
        return None

    for motor_id in pending:
        try:
            session = await store.get_session(telegram_id, motor_id)
        except RedisError:
            continue
        if session is not None and session.phase == Phase.CHECKLIST:
            return session
    return None


async def _display_question(
    message: Message,
    session: Session,
) -> None:
    """Display the current question with Reply Keyboard and progress bar."""
    question = next_question(session)

    if isinstance(question, Done):
        # Should not happen if called correctly, but handle gracefully
        return

    # Compute overall progress
    done, total = compute_overall_progress(session)

    # Get category for the current field
    category = session.current_category or CATEGORY_FOR_FIELD.get(question.field, "")

    # Format and send the question
    text = _format_question_message(
        label=question.label,
        category=category,
        done=done,
        total=total,
    )
    keyboard = _build_reply_keyboard(question.options)

    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


async def _display_category_transition(
    message: Message,
    completed_category: str,
    next_category: str | None,
) -> None:
    """Display a category transition message (Requirement 4.7)."""
    text = f"✅ Kategori *{completed_category}* selesai!"
    if next_category:
        text += f"\n\n➡️ Lanjut ke kategori: *{next_category}*"
    await message.answer(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Handler: text messages during CHECKLIST phase
# ---------------------------------------------------------------------------


@router.message(F.text)
async def handle_checklist_answer(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Handle text input during the CHECKLIST phase.

    Flow:
    1. Find active checklist session
    2. Get current question
    3. Validate answer against valid option set
    4. If invalid: re-display same question (Requirement 4.5, 4.8)
    5. If valid: save to Redis BEFORE advancing (Requirement 4.6)
    6. If Redis fails: show error and re-display (Requirement 4.6)
    7. Show progress and advance to next question
    8. Handle category transitions (Requirement 4.7)
    9. Handle checklist completion → transition to STNK_CONDITIONAL or PHOTOS
    """
    telegram_id = str(message.from_user.id)

    # Find active checklist session
    session = await _find_active_checklist_session(telegram_id, session_store)
    if session is None:
        # No active checklist session — ignore (let other handlers deal with it)
        return

    # Get current question
    question = next_question(session)
    if isinstance(question, Done):
        # Checklist already complete — should not happen normally
        return

    # Validate answer (Requirement 4.5, 4.8)
    answer_text = message.text.strip() if message.text else ""
    valid_options = question.options

    if answer_text not in valid_options:
        # Invalid answer: re-display same question (Requirement 4.5, 4.8)
        await _display_question(message, session)
        return

    # Apply answer using domain logic
    try:
        updated_session = apply_answer(session, question.field, answer_text)
    except ValueError:
        # Should not happen since we validated above, but handle gracefully
        await _display_question(message, session)
        return

    # Track if we completed a category
    old_completed = set(session.completed_categories)
    new_completed = set(updated_session.completed_categories)
    newly_completed_categories = new_completed - old_completed

    # Save to Redis BEFORE advancing (Requirement 4.6)
    try:
        await session_store.save_session(updated_session)
    except RedisError:
        # Redis failure: show error and re-display same question (Requirement 4.6)
        logger.warning(
            "redis_save_failed: telegram_id=%s motor_id=%s field=%s",
            telegram_id,
            session.motor_id,
            question.field,
        )
        await message.answer(
            "Gagal menyimpan jawaban, silakan coba lagi.",
            reply_markup=_build_reply_keyboard(valid_options),
        )
        return

    # Handle category transition (Requirement 4.7)
    if newly_completed_categories:
        completed_cat = list(newly_completed_categories)[0]
        next_cat = updated_session.current_category
        await _display_category_transition(message, completed_cat, next_cat)

    # Check if checklist is complete
    next_q = next_question(updated_session)

    if isinstance(next_q, Done):
        # All 66 mandatory fields answered — determine next phase
        # Store stnk_answer for conditional logic
        stnk_value = updated_session.answers.get("stnk")

        if stnk_value and stnk_value != "Baik":
            # Transition to STNK_CONDITIONAL (Requirement 4.10 for keyboard)
            from bot.domain.fsm import transition_to_stnk_conditional

            final_session = transition_to_stnk_conditional(updated_session)
            final_session = final_session.model_copy(update={"stnk_answer": stnk_value})

            try:
                await session_store.save_session(final_session)
            except RedisError:
                logger.warning(
                    "redis_save_failed_transition: telegram_id=%s motor_id=%s",
                    telegram_id,
                    session.motor_id,
                )
                # Session was already saved with answers, just inform user
                await message.answer(
                    "Gagal menyimpan jawaban, silakan coba lagi.",
                )
                return

            await message.answer(
                "✅ Checklist komponen selesai!\n\n"
                "Karena STNK tidak dalam kondisi Baik, "
                "ada beberapa pertanyaan tambahan.",
                parse_mode="Markdown",
            )

            # Send the first STNK conditional question
            from bot.handlers.stnk import send_next_stnk_question
            await send_next_stnk_question(message, final_session)
        else:
            # stnk == "Baik" or not answered yet (shouldn't happen for complete checklist)
            # Transition to PHOTOS (Requirement 4.10)
            from bot.domain.fsm import transition_to_photos

            final_session = transition_to_photos(updated_session)
            if stnk_value:
                final_session = final_session.model_copy(update={"stnk_answer": stnk_value})

            try:
                await session_store.save_session(final_session)
            except RedisError:
                logger.warning(
                    "redis_save_failed_transition: telegram_id=%s motor_id=%s",
                    telegram_id,
                    session.motor_id,
                )
                await message.answer(
                    "Gagal menyimpan jawaban, silakan coba lagi.",
                )
                return

            # Send ReplyKeyboardRemove when leaving checklist phase (Requirement 4.10)
            await message.answer(
                "✅ Checklist komponen selesai!\n\n"
                "Lanjut ke pengambilan foto.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )

            # Send the first photo prompt
            from bot.handlers.photos import send_photo_prompt
            await send_photo_prompt(message, final_session)
    else:
        # More questions to answer — display next question
        await _display_question(message, updated_session)
