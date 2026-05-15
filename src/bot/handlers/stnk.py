"""STNK conditional question handler for the Telegram Inspection Bot.

Handles text messages during phase=STNK_CONDITIONAL:
- Display conditional questions based on stnk answer (Cukup: 3 questions, Rusak: 4 questions)
- Ya/Tidak/Skip Reply Keyboard for boolean fields
- Date input with validation (YYYY-MM-DD regex) for stnk_mati_tanggal
- Skip saves null and advances
- Store stnk_answer separately in session
- Transition to PHOTOS phase when all conditional questions answered/skipped

Uses aiogram 3.x Router pattern.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.fsm import transition_to_photos
from bot.domain.models import Phase, Session
from bot.domain.progress import render_progress_bar
from bot.domain.stnk import (
    STNK_FIELD_LABELS,
    apply_stnk_answer,
    next_stnk_question,
    stnk_relevant_fields,
    validate_stnk_date,
)

from redis.exceptions import RedisError

router = Router(name="stnk_conditional")


# ---------------------------------------------------------------------------
# Helper: find active STNK_CONDITIONAL session
# ---------------------------------------------------------------------------


async def _find_stnk_session(
    telegram_id: str,
    store: RedisSessionStore,
) -> Session | None:
    """Find the active session in STNK_CONDITIONAL phase."""
    try:
        pending = await store.list_pending(telegram_id)
    except RedisError:
        return None

    for motor_id in pending:
        try:
            session = await store.get_session(telegram_id, motor_id)
        except RedisError:
            continue
        if session is not None and session.phase == Phase.STNK_CONDITIONAL:
            return session
    return None

# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------


def _build_boolean_keyboard() -> ReplyKeyboardMarkup:
    """Build Reply Keyboard with Ya, Tidak, Skip buttons (Requirement 5.4)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ya"), KeyboardButton(text="Tidak"), KeyboardButton(text="Skip")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _build_date_keyboard() -> ReplyKeyboardMarkup:
    """Build Reply Keyboard with only Skip button for date input (Requirement 5.5)."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Skip")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ---------------------------------------------------------------------------
# Helper: send the next STNK conditional question
# ---------------------------------------------------------------------------


async def send_next_stnk_question(message: Message, session: Session) -> None:
    """Send the next conditional STNK question to the inspector.

    If no more questions remain, transitions to PHOTOS phase.
    """
    question = next_stnk_question(session)

    if question is None:
        # All conditional questions answered/skipped → transition to PHOTOS
        # This case is handled by the caller after saving session
        return

    # Build progress info
    relevant = stnk_relevant_fields(session.stnk_answer)
    total = len(relevant)
    done = sum(1 for f in relevant if f in session.answers)
    progress_bar = render_progress_bar(done, total)

    label = STNK_FIELD_LABELS.get(question.field, question.field)

    if question.keyboard_kind == "reply":
        # Boolean field: Ya/Tidak/Skip
        keyboard = _build_boolean_keyboard()
        text = (
            f"📋 *Pertanyaan STNK Tambahan*\n\n"
            f"{label}\n"
            f"{progress_bar} {done}/{total}\n\n"
            f"Pilih jawaban:"
        )
    else:
        # Date field: free text with Skip button
        keyboard = _build_date_keyboard()
        text = (
            f"📋 *Pertanyaan STNK Tambahan*\n\n"
            f"{label}\n"
            f"{progress_bar} {done}/{total}\n\n"
            f"Masukkan tanggal format YYYY-MM-DD atau tekan Skip:"
        )

    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Main handler: text messages during STNK_CONDITIONAL phase
# ---------------------------------------------------------------------------


@router.message(F.text)
async def handle_stnk_conditional_text(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Handle text input during STNK_CONDITIONAL phase.

    Processes answers for conditional STNK questions:
    - Boolean fields accept: Ya, Tidak, Skip
    - Date field accepts: valid YYYY-MM-DD or Skip
    - Skip saves null for the field and advances
    - Invalid input re-displays the current question

    Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
    """
    telegram_id = str(message.from_user.id)

    # Find active STNK_CONDITIONAL session
    session = await _find_stnk_session(telegram_id, session_store)
    if session is None:
        return  # Not in STNK phase, let other handlers deal with it

    text = message.text.strip() if message.text else ""

    # Get the current question
    question = next_stnk_question(session)

    if question is None:
        # No more questions — transition to PHOTOS
        updated = transition_to_photos(session)
        await session_store.save_session(updated)
        await message.answer(
            "✅ Pertanyaan STNK selesai!\n\n"
            "Selanjutnya: pengambilan foto.",
            reply_markup=ReplyKeyboardRemove(),
        )
        # Send the first photo prompt
        from bot.handlers.photos import send_photo_prompt
        await send_photo_prompt(message, updated)
        return

    # --- Process the answer based on field type ---
    if question.keyboard_kind == "reply":
        # Boolean field: accept Ya, Tidak, Skip
        if text == "Skip":
            value: str | None = None
        elif text in ("Ya", "Tidak"):
            value = text
        else:
            # Invalid input — re-display question
            await send_next_stnk_question(message, session)
            return
    else:
        # Date field (stnk_mati_tanggal): accept Skip or valid date
        if text == "Skip":
            value = None
        elif validate_stnk_date(text):
            value = text
        else:
            # Invalid date format — show error and re-display
            await message.answer(
                "❌ Format tanggal tidak valid.\n"
                "Gunakan format YYYY-MM-DD (contoh: 2024-06-15)\n"
                "atau tekan Skip untuk melewati.",
                reply_markup=_build_date_keyboard(),
            )
            return

    # --- Apply the answer ---
    updated_session = apply_stnk_answer(session, question.field, value)

    # Save to Redis before advancing (Requirement 4.6 pattern)
    try:
        await session_store.save_session(updated_session)
    except Exception:
        await message.answer(
            "Gagal menyimpan jawaban, silakan coba lagi.",
            reply_markup=_build_boolean_keyboard()
            if question.keyboard_kind == "reply"
            else _build_date_keyboard(),
        )
        return

    # --- Check if there are more questions ---
    next_q = next_stnk_question(updated_session)

    if next_q is None:
        # All conditional questions done → transition to PHOTOS
        final_session = transition_to_photos(updated_session)
        await session_store.save_session(final_session)
        await message.answer(
            "✅ Pertanyaan STNK selesai!\n\n"
            "Selanjutnya: pengambilan foto.",
            reply_markup=ReplyKeyboardRemove(),
        )
        # Send the first photo prompt
        from bot.handlers.photos import send_photo_prompt
        await send_photo_prompt(message, final_session)
    else:
        # Send the next question
        await send_next_stnk_question(message, updated_session)
