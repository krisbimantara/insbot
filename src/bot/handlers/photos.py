"""Photo capture handler for the Telegram Inspection Bot.

Handles:
- Displaying photo prompts in fixed order with label, description, and progress
- Sending ReplyKeyboardRemove on first photo prompt (Requirement 6.1)
- Accepting photo/document-image, saving file_id; rejecting non-image
- Showing Inline Keyboard [Konfirmasi] [Foto Ulang] after each photo
- Handling confirm (advance index) and retry (clear and re-prompt)
- Transitioning to Summary when all 10 confirmed

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.fsm import transition_to_summary
from bot.domain.models import PHOTO_FIELDS, Phase, Session

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

router = Router(name="photos")


# ---------------------------------------------------------------------------
# Helper: find active PHOTOS session
# ---------------------------------------------------------------------------


async def _find_photos_session(
    telegram_id: str,
    store: RedisSessionStore,
) -> Session | None:
    """Find the active session in PHOTOS phase."""
    try:
        pending = await store.list_pending(telegram_id)
    except RedisError:
        return None

    for motor_id in pending:
        try:
            session = await store.get_session(telegram_id, motor_id)
        except RedisError:
            continue
        if session is not None and session.phase == Phase.PHOTOS:
            return session
    return None

# ---------------------------------------------------------------------------
# Callback data prefixes
# ---------------------------------------------------------------------------

CB_PHOTO_CONFIRM = "photo_confirm:"
CB_PHOTO_RETRY = "photo_retry:"

# ---------------------------------------------------------------------------
# Photo labels and descriptions
# ---------------------------------------------------------------------------

PHOTO_LABELS: dict[str, str] = {
    "foto_tampak_depan": "Tampak Depan",
    "foto_tampak_belakang": "Tampak Belakang",
    "foto_tampak_kanan": "Tampak Kanan",
    "foto_tampak_kiri": "Tampak Kiri",
    "foto_mesin": "Mesin",
    "foto_nomor_rangka": "Nomor Rangka",
    "foto_nomor_mesin": "Nomor Mesin",
    "foto_stnk": "STNK",
    "foto_ban_depan": "Ban Depan",
    "foto_ban_belakang": "Ban Belakang",
}

PHOTO_DESCRIPTIONS: dict[str, str] = {
    "foto_tampak_depan": "Ambil foto motor dari arah depan secara penuh.",
    "foto_tampak_belakang": "Ambil foto motor dari arah belakang secara penuh.",
    "foto_tampak_kanan": "Ambil foto motor dari sisi kanan secara penuh.",
    "foto_tampak_kiri": "Ambil foto motor dari sisi kiri secara penuh.",
    "foto_mesin": "Ambil foto mesin motor dengan jelas.",
    "foto_nomor_rangka": "Ambil foto nomor rangka yang tertera pada motor.",
    "foto_nomor_mesin": "Ambil foto nomor mesin yang tertera pada motor.",
    "foto_stnk": "Ambil foto STNK motor (halaman depan).",
    "foto_ban_depan": "Ambil foto ban depan motor, tampilkan kondisi tapak ban.",
    "foto_ban_belakang": "Ambil foto ban belakang motor, tampilkan kondisi tapak ban.",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _build_photo_prompt(photo_index: int) -> str:
    """Build the photo prompt text for the given index (Requirement 6.3).

    Shows: label, description, and progress "Foto {photo_index+1}/10".
    """
    field = PHOTO_FIELDS[photo_index]
    label = PHOTO_LABELS.get(field, field)
    description = PHOTO_DESCRIPTIONS.get(field, "")
    progress = f"Foto {photo_index + 1}/10"

    return (
        f"📷 *{label}*\n\n"
        f"{description}\n\n"
        f"_{progress}_"
    )


def _build_confirm_retry_keyboard(photo_index: int) -> InlineKeyboardMarkup:
    """Build Inline Keyboard with [Konfirmasi] and [Foto Ulang] buttons (Requirement 6.4)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Konfirmasi",
                    callback_data=f"{CB_PHOTO_CONFIRM}{photo_index}",
                ),
                InlineKeyboardButton(
                    text="Foto Ulang",
                    callback_data=f"{CB_PHOTO_RETRY}{photo_index}",
                ),
            ]
        ]
    )


async def send_photo_prompt(
    target: Message | CallbackQuery,
    session: Session,
) -> None:
    """Send the photo prompt for the current photo_index.

    On the first photo (index 0), includes ReplyKeyboardRemove (Requirement 6.1).
    """
    photo_index = session.photo_index
    text = _build_photo_prompt(photo_index)

    # First photo prompt includes ReplyKeyboardRemove (Requirement 6.1)
    reply_markup: ReplyKeyboardRemove | None = None
    if photo_index == 0:
        reply_markup = ReplyKeyboardRemove()

    if isinstance(target, CallbackQuery):
        if target.message is not None:
            await target.message.answer(  # type: ignore[union-attr]
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
    else:
        await target.answer(
            text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )


def _is_phase_photos(session: Session | None) -> bool:
    """Check if the session is in PHOTOS phase."""
    return session is not None and session.phase == Phase.PHOTOS


# ---------------------------------------------------------------------------
# Message handlers — photo/document reception
# ---------------------------------------------------------------------------


@router.message(F.photo)
async def handle_photo_message(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Handle photo message during PHOTOS phase (Requirement 6.4).

    Accepts the photo, saves file_id, and shows [Konfirmasi] [Foto Ulang].
    """
    telegram_id = str(message.from_user.id)
    active_session = await _find_photos_session(telegram_id, session_store)

    if active_session is None:
        return  # Not in photos phase, let other handlers deal with it

    # Get the largest photo (last in the array)
    photo = message.photo[-1]  # type: ignore[index]
    file_id = photo.file_id

    # Save file_id to session
    photo_index = active_session.photo_index
    field = PHOTO_FIELDS[photo_index]

    new_photos = dict(active_session.photos)
    new_photos[field] = file_id
    updated_session = active_session.model_copy(update={"photos": new_photos})

    await session_store.save_session(updated_session)

    # Show confirm/retry keyboard (Requirement 6.4)
    keyboard = _build_confirm_retry_keyboard(photo_index)
    await message.answer(
        f"✅ Foto *{PHOTO_LABELS.get(field, field)}* diterima.\n\n"
        f"Konfirmasi atau ambil ulang?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


@router.message(F.document)
async def handle_document_message(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Handle document message during PHOTOS phase.

    Accepts document-image (mime_type starts with "image/"), rejects non-image.
    """
    telegram_id = str(message.from_user.id)
    active_session = await _find_photos_session(telegram_id, session_store)

    if active_session is None:
        return  # Not in photos phase

    document = message.document
    if document is None:
        return

    # Check if document is an image (Requirement 6.4 — accept document-image)
    mime_type = document.mime_type or ""
    if not mime_type.startswith("image/"):
        # Reject non-image document (Requirement 6.7)
        await message.answer("Mohon kirim foto (JPG/PNG).")
        return

    file_id = document.file_id

    # Save file_id to session
    photo_index = active_session.photo_index
    field = PHOTO_FIELDS[photo_index]

    new_photos = dict(active_session.photos)
    new_photos[field] = file_id
    updated_session = active_session.model_copy(update={"photos": new_photos})

    await session_store.save_session(updated_session)

    # Show confirm/retry keyboard (Requirement 6.4)
    keyboard = _build_confirm_retry_keyboard(photo_index)
    await message.answer(
        f"✅ Foto *{PHOTO_LABELS.get(field, field)}* diterima.\n\n"
        f"Konfirmasi atau ambil ulang?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


@router.message(F.video | F.sticker | F.animation | F.video_note | F.voice | F.audio)
async def handle_non_image_message(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Reject non-image media during PHOTOS phase (Requirement 6.7).

    Handles video, sticker, animation, video_note, voice, audio.
    """
    telegram_id = str(message.from_user.id)
    active_session = await _find_photos_session(telegram_id, session_store)

    if active_session is None:
        return  # Not in photos phase

    await message.answer("Mohon kirim foto (JPG/PNG).")


# ---------------------------------------------------------------------------
# Callback handlers — confirm and retry
# ---------------------------------------------------------------------------


@router.callback_query(lambda c: c.data and c.data.startswith(CB_PHOTO_CONFIRM))
async def handle_photo_confirm(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
) -> None:
    """Handle 'Konfirmasi' button tap (Requirement 6.6).

    Advances photo_index by 1. If all 10 confirmed, transitions to SUMMARY.
    """
    from bot.session_middleware import check_session_expired

    telegram_id = str(callback.from_user.id)
    active_session = await _find_photos_session(telegram_id, session_store)

    if active_session is None:
        await check_session_expired(callback, active_session)
        return

    await callback.answer()

    new_index = active_session.photo_index + 1

    if new_index >= 10:
        # All 10 photos confirmed → transition to SUMMARY (Requirement 6.9)
        updated_session = transition_to_summary(
            active_session.model_copy(update={"photo_index": new_index})
        )
        await session_store.save_session(updated_session)

        if callback.message is not None:
            await callback.message.answer(  # type: ignore[union-attr]
                "✅ Semua foto telah dikonfirmasi!\n\n"
                "Melanjutkan ke *Ringkasan Inspeksi*...",
                parse_mode="Markdown",
            )

            # Send the summary page with buttons
            from bot.handlers.summary import send_summary
            await send_summary(callback, updated_session)
    else:
        # Advance to next photo (Requirement 6.6)
        updated_session = active_session.model_copy(update={"photo_index": new_index})
        await session_store.save_session(updated_session)

        # Send next photo prompt
        await send_photo_prompt(callback, updated_session)


@router.callback_query(lambda c: c.data and c.data.startswith(CB_PHOTO_RETRY))
async def handle_photo_retry(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
) -> None:
    """Handle 'Foto Ulang' button tap (Requirement 6.5).

    Clears photos[field_name] and re-prompts the same photo.
    """
    from bot.session_middleware import check_session_expired

    telegram_id = str(callback.from_user.id)
    active_session = await _find_photos_session(telegram_id, session_store)

    if active_session is None:
        await check_session_expired(callback, active_session)
        return

    await callback.answer()

    photo_index = active_session.photo_index
    field = PHOTO_FIELDS[photo_index]

    # Clear the photo for this field (Requirement 6.5)
    new_photos = dict(active_session.photos)
    new_photos.pop(field, None)
    updated_session = active_session.model_copy(update={"photos": new_photos})

    await session_store.save_session(updated_session)

    # Re-prompt the same photo
    await send_photo_prompt(callback, updated_session)
