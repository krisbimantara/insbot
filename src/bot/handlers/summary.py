"""Summary and revision handler for the Telegram Inspection Bot.

Handles:
- Displaying summary page: motor name, 8 categories with done/total and (Direvisi) marker,
  photo status, Inline Keyboard [Revisi Kategori] [Kirim Hasil]
- "Revisi Kategori" tap: show 8 categories as Inline Keyboard
- Category selection: set mode=revisi, re-display category components with old answers
  + Reply Keyboard (options + Skip)
- Skip (preserve old value) and new answer (overwrite)
- On revision complete: update revision_history, set mode=ringkasan, apply STNK prune
  if category 8 revised, show summary with ReplyKeyboardRemove

Uses aiogram 3.x Router pattern.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from redis.exceptions import RedisError

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.checklist import CATEGORY_FOR_FIELD, FIELD_LABELS
from bot.domain.fsm import transition_to_revision, transition_to_summary
from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    COMPONENT_OPTIONS,
    PHOTO_FIELDS,
    Phase,
    Session,
)
from bot.domain.progress import compute_progress
from bot.domain.stnk import prune_irrelevant_stnk

logger = logging.getLogger(__name__)

router = Router(name="summary")

# ---------------------------------------------------------------------------
# Callback data constants
# ---------------------------------------------------------------------------

CB_REVISI_KATEGORI = "revisi_kategori"
CB_KATEGORI_SELECT = "kategori_select:"
CB_KIRIM_HASIL = "kirim_hasil"


# ---------------------------------------------------------------------------
# Helper: build summary text
# ---------------------------------------------------------------------------


def _build_summary_text(session: Session) -> str:
    """Build the summary page text (Requirement 7.1).

    Shows:
    - Motor name (merk model tahun — nopol)
    - 8 categories with done/total and (Direvisi) marker
    - Photo status (done/10)
    """
    meta = session.motor_meta
    motor_label = f"{meta.merk} {meta.model} {meta.tahun} — {meta.nopol}"

    lines: list[str] = [
        f"📋 *Ringkasan Inspeksi*\n",
        f"🏍 {motor_label}\n",
    ]

    # Category progress
    progress_list = compute_progress(session)
    for cat_progress in progress_list:
        revised_marker = ""
        if cat_progress.name in session.revision_history:
            revised_marker = " (Direvisi)"
        lines.append(
            f"• {cat_progress.name}: {cat_progress.done}/{cat_progress.total}{revised_marker}"
        )

    # Photo status
    photo_done = sum(1 for f in PHOTO_FIELDS if f in session.photos)
    lines.append(f"\n📷 Foto: {photo_done}/10")

    return "\n".join(lines)


def _build_summary_keyboard() -> InlineKeyboardMarkup:
    """Build Inline Keyboard with [Revisi Kategori] and [Kirim Hasil] (Requirement 7.1)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Revisi Kategori",
                    callback_data=CB_REVISI_KATEGORI,
                ),
                InlineKeyboardButton(
                    text="Kirim Hasil",
                    callback_data=CB_KIRIM_HASIL,
                ),
            ]
        ]
    )


def _build_category_selection_keyboard() -> InlineKeyboardMarkup:
    """Build Inline Keyboard with 8 categories for revision selection (Requirement 7.2)."""
    buttons: list[list[InlineKeyboardButton]] = []
    for category in CATEGORIES:
        buttons.append([
            InlineKeyboardButton(
                text=category,
                callback_data=f"{CB_KATEGORI_SELECT}{category}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_revision_keyboard(field: str) -> ReplyKeyboardMarkup:
    """Build Reply Keyboard for revision: valid options + Skip (Requirement 7.3).

    The Skip button allows preserving the old answer (Requirement 7.4).
    """
    options = COMPONENT_OPTIONS.get(field, ("Baik", "Cukup", "Rusak"))
    buttons = [KeyboardButton(text=opt) for opt in options]
    buttons.append(KeyboardButton(text="Skip"))
    return ReplyKeyboardMarkup(
        keyboard=[buttons],
        one_time_keyboard=True,
        resize_keyboard=True,
    )


# ---------------------------------------------------------------------------
# Helper: send summary page
# ---------------------------------------------------------------------------


async def send_summary(target: Message | CallbackQuery, session: Session) -> None:
    """Send the summary page with Inline Keyboard.

    Can be called from both Message and CallbackQuery contexts.
    """
    text = _build_summary_text(session)
    keyboard = _build_summary_keyboard()

    if isinstance(target, CallbackQuery):
        if target.message is not None:
            await target.message.answer(  # type: ignore[union-attr]
                text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
    else:
        await target.answer(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


# ---------------------------------------------------------------------------
# Helper: send revision question for a field
# ---------------------------------------------------------------------------


def _format_revision_question(
    field: str,
    category: str,
    old_value: str | None,
    field_index: int,
    total_fields: int,
) -> str:
    """Format the revision question text showing old answer as reference (Requirement 7.3)."""
    label = FIELD_LABELS.get(field, field)
    old_display = old_value if old_value is not None else "(belum dijawab)"
    return (
        f"✏️ *Revisi: {category}*\n\n"
        f"Komponen: *{label}*\n"
        f"Jawaban sebelumnya: _{old_display}_\n\n"
        f"Pertanyaan {field_index + 1}/{total_fields}\n"
        f"Pilih jawaban baru atau Skip untuk mempertahankan jawaban lama:"
    )


async def _send_revision_question(
    target: Message | CallbackQuery,
    session: Session,
) -> None:
    """Send the current revision question with Reply Keyboard."""
    category = session.revisi_kategori
    if category is None:
        return

    fields = CATEGORY_FIELDS[category]
    current_field = session.current_question
    if current_field is None:
        return

    field_index = list(fields).index(current_field) if current_field in fields else 0
    old_value = session.answers.get(current_field)

    text = _format_revision_question(
        field=current_field,
        category=category,
        old_value=old_value,
        field_index=field_index,
        total_fields=len(fields),
    )
    keyboard = _build_revision_keyboard(current_field)

    if isinstance(target, CallbackQuery):
        if target.message is not None:
            await target.message.answer(  # type: ignore[union-attr]
                text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
    else:
        await target.answer(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


# ---------------------------------------------------------------------------
# Helper: find active session in SUMMARY or REVISION phase
# ---------------------------------------------------------------------------


async def _find_active_session(
    telegram_id: str,
    store: RedisSessionStore,
    phases: tuple[Phase, ...],
) -> Session | None:
    """Find the active session in one of the given phases for the telegram_id."""
    try:
        pending = await store.list_pending(telegram_id)
    except RedisError:
        return None

    for motor_id in pending:
        try:
            session = await store.get_session(telegram_id, motor_id)
        except RedisError:
            continue
        if session is not None and session.phase in phases:
            return session
    return None


# ---------------------------------------------------------------------------
# Callback handler: "Revisi Kategori" button
# ---------------------------------------------------------------------------


@router.callback_query(lambda c: c.data == CB_REVISI_KATEGORI)
async def handle_revisi_kategori(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
) -> None:
    """Handle 'Revisi Kategori' tap: show 8 categories as Inline Keyboard (Requirement 7.2)."""
    from bot.session_middleware import check_session_expired

    telegram_id = str(callback.from_user.id)
    active_session = await _find_active_session(telegram_id, session_store, (Phase.SUMMARY,))

    if active_session is None:
        await check_session_expired(callback, active_session)
        return

    await callback.answer()

    keyboard = _build_category_selection_keyboard()
    if callback.message is not None:
        await callback.message.answer(  # type: ignore[union-attr]
            "Pilih kategori yang ingin direvisi:",
            reply_markup=keyboard,
        )


# ---------------------------------------------------------------------------
# Callback handler: category selection for revision
# ---------------------------------------------------------------------------


@router.callback_query(lambda c: c.data and c.data.startswith(CB_KATEGORI_SELECT))
async def handle_kategori_select(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
) -> None:
    """Handle category selection: set mode=revisi, show first component (Requirement 7.3).

    Sets mode="revisi", revisi_kategori=category_name, phase=REVISION.
    Re-displays each component in the category with old answer + Reply Keyboard.
    """
    from bot.session_middleware import check_session_expired

    telegram_id = str(callback.from_user.id)
    active_session = await _find_active_session(telegram_id, session_store, (Phase.SUMMARY,))

    if active_session is None:
        await check_session_expired(callback, active_session)
        return

    await callback.answer()

    # Extract category name from callback data
    category = callback.data[len(CB_KATEGORI_SELECT):]  # type: ignore[index]

    if category not in CATEGORY_FIELDS:
        if callback.message is not None:
            await callback.message.answer(  # type: ignore[union-attr]
                "Kategori tidak valid.",
            )
        return

    # Transition to REVISION phase
    updated_session = transition_to_revision(active_session, category)

    # Save session before sending message (Requirement 4.6 pattern)
    try:
        await session_store.save_session(updated_session)
    except RedisError:
        if callback.message is not None:
            await callback.message.answer(  # type: ignore[union-attr]
                "Gagal menyimpan, silakan coba lagi.",
            )
        return

    # Send first revision question
    await _send_revision_question(callback, updated_session)


# ---------------------------------------------------------------------------
# Text handler: revision answers during REVISION phase
# ---------------------------------------------------------------------------


@router.message(F.text)
async def handle_revision_answer(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Handle text input during REVISION phase.

    - Skip: preserve old value (Requirement 7.4)
    - Valid answer: overwrite (Requirement 7.5)
    - Invalid answer: re-display same question
    - On revision complete: update revision_history, apply STNK prune if needed,
      set mode=ringkasan, show summary with ReplyKeyboardRemove (Requirement 7.6)
    """
    telegram_id = str(message.from_user.id)

    # Find active revision session
    session = await _find_active_session(
        telegram_id, session_store, (Phase.REVISION,)
    )
    if session is None:
        return  # Not in revision phase, let other handlers deal with it

    category = session.revisi_kategori
    if category is None:
        return

    current_field = session.current_question
    if current_field is None:
        return

    text = message.text.strip() if message.text else ""
    fields = CATEGORY_FIELDS[category]

    # --- Process the answer ---
    if text == "Skip":
        # Preserve old value (Requirement 7.4) — do not change answers[field]
        new_answers = dict(session.answers)
    else:
        # Validate against valid options for this field
        valid_options = COMPONENT_OPTIONS.get(current_field, ("Baik", "Cukup", "Rusak"))
        if text not in valid_options:
            # Invalid answer: re-display same question
            await _send_revision_question(message, session)
            return
        # Overwrite with new answer (Requirement 7.5)
        new_answers = dict(session.answers)
        new_answers[current_field] = text

    # --- Advance to next field in category ---
    field_list = list(fields)
    current_idx = field_list.index(current_field) if current_field in field_list else 0
    next_idx = current_idx + 1

    if next_idx < len(field_list):
        # More fields in this category — advance pointer
        next_field = field_list[next_idx]
        updated_session = session.model_copy(
            update={
                "answers": new_answers,
                "current_question": next_field,
            }
        )

        # Save before advancing
        try:
            await session_store.save_session(updated_session)
        except RedisError:
            await message.answer(
                "Gagal menyimpan jawaban, silakan coba lagi.",
                reply_markup=_build_revision_keyboard(current_field),
            )
            return

        # Send next revision question
        await _send_revision_question(message, updated_session)
    else:
        # --- Revision complete for this category ---
        # Update revision_history (Requirement 7.6)
        new_revision_history = dict(session.revision_history)
        new_revision_history[category] = datetime.now(tz=timezone.utc)

        # Apply STNK prune if category 8 (Dokumen) revised and stnk changed (Requirement 7.7)
        final_answers = new_answers
        stnk_answer = session.stnk_answer

        if category == "Dokumen (STNK)":
            new_stnk_value = new_answers.get("stnk")
            if new_stnk_value != session.stnk_answer and new_stnk_value is not None:
                # STNK answer changed — apply prune (Requirement 7.7 / 5.6)
                stnk_answer = new_stnk_value  # type: ignore[assignment]
                final_answers = prune_irrelevant_stnk(new_answers, new_stnk_value)

        # Transition back to SUMMARY (Requirement 7.6)
        updated_session = session.model_copy(
            update={
                "answers": final_answers,
                "revision_history": new_revision_history,
                "mode": "ringkasan",
                "phase": Phase.SUMMARY,
                "revisi_kategori": None,
                "current_category": None,
                "current_question": None,
                "stnk_answer": stnk_answer,
            }
        )

        # Save session
        try:
            await session_store.save_session(updated_session)
        except RedisError:
            await message.answer(
                "Gagal menyimpan, silakan coba lagi.",
                reply_markup=_build_revision_keyboard(current_field),
            )
            return

        # Show summary with ReplyKeyboardRemove (Requirement 7.6)
        await message.answer(
            f"✅ Revisi kategori *{category}* selesai!",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_summary(message, updated_session)
