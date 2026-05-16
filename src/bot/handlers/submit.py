"""Submit handler for the Telegram Inspection Bot.

Handles the "Kirim Hasil" callback from the summary page. Orchestrates the
full submission pipeline:

1. Validate pre-submit (66 fields + 10 photos)
2. Refresh tipe_inspeksi check against Frappe
3. Download, compress, and upload 10 photos serially
4. Build payload and idempotency key
5. Submit with retry (3× exponential backoff 2s/4s/8s)
6. Handle success/error outcomes

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 14.5, 15.2, 15.3
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.adapters.exceptions import (
    FrappePermissionError,
    FrappeUnavailable,
    FrappeValidationError,
    PreSubmitValidationError,
    StatusChanged,
    StatusMismatch,
)
from bot.adapters.frappe import FrappeClient
from bot.adapters.photos import (
    download_telegram_photo,
    get_photo_filename,
)
from bot.adapters.redis_store import RedisSessionStore
from bot.config import Settings
from bot.domain.models import PHOTO_FIELDS, Phase, Session, SubmitResult
from bot.domain.payload import build_idempotency_key, build_submit_payload
from bot.domain.validation import validate_pre_submit

logger = logging.getLogger(__name__)

router = Router(name="submit")

# Callback data constant
CB_KIRIM_HASIL = "kirim_hasil"

# Retry backoff schedule (Requirement 8.10)
_BACKOFFS: tuple[int, ...] = (2, 4, 8)


# ---------------------------------------------------------------------------
# Submission pipeline
# ---------------------------------------------------------------------------


async def _submit_inspection(
    session: Session,
    *,
    bot,
    frappe: FrappeClient,
    settings: Settings,
) -> SubmitResult:
    """Execute the full submission pipeline.

    This function encapsulates the logic from the design document's
    "Submission Pipeline" section. It raises domain exceptions on failure
    so the caller (handler) can map them to user-facing messages.

    Raises:
        PreSubmitValidationError: 66 fields + 10 photos not complete.
        StatusChanged: Motor no longer in pending queue (Requirement 15.2).
        StatusMismatch: tipe_inspeksi changed (Requirement 15.3).
        FrappeValidationError: Frappe rejected payload (Requirement 8.8).
        FrappePermissionError: 403 from Frappe (Requirement 8.8).
        FrappeUnavailable: 5xx after all retries exhausted (Requirement 8.10).
    """
    # Step 0: Pre-submit validation (Requirement 8.1)
    errors = validate_pre_submit(session)
    if errors:
        raise PreSubmitValidationError(errors)

    # Step 1: Refresh tipe_inspeksi check (Requirement 14.5)
    pending = await frappe.get_pending_list(session.telegram_id)
    motor = next((m for m in pending if m.name == session.motor_id), None)
    if motor is None:
        raise StatusChanged(motor_id=session.motor_id)

    expected_tipe = (
        "Inspeksi Ulang"
        if motor.status_inspeksi == "Proses Inspeksi Ulang"
        else "Inspeksi"
    )
    if expected_tipe != session.tipe_inspeksi:
        raise StatusMismatch(expected=session.tipe_inspeksi, actual=expected_tipe)

    # Step 2: Upload 10 photos serially (Requirement 8.3)
    foto_urls: dict[str, str] = {}
    for i, field in enumerate(PHOTO_FIELDS):
        file_id = session.photos[field]
        logger.info("photo_upload_start", extra={"field": field, "index": i + 1})
        raw = await download_telegram_photo(bot, file_id)
        logger.info("photo_downloaded", extra={"field": field, "size_bytes": len(raw)})
        filename = get_photo_filename(field, session.motor_id)
        url = await frappe.upload_foto(raw, filename=filename)
        foto_urls[field] = url
        logger.info("photo_uploaded", extra={"field": field, "url": url})

    # Step 3: Build payload (pure)
    payload = build_submit_payload(session, foto_urls)
    idem_key = build_idempotency_key(session)
    logger.info("submit_calling_frappe", extra={"idempotency_key": idem_key})

    # Step 4: Submit with retry — 3× exponential backoff (Requirement 8.10)
    last_exc: FrappeUnavailable | None = None
    for attempt in range(4):  # 1 initial + 3 retries
        try:
            logger.info("submit_attempt", extra={"attempt": attempt + 1})
            result = await frappe.submit_hasil_inspeksi(payload, idempotency_key=idem_key)
            logger.info("submit_success", extra={"doc_name": result.name})
            return result
        except FrappeValidationError as e:
            logger.error("submit_validation_error", extra={"error_msg": e.message, "attempt": attempt + 1})
            if e.indicates_already_completed():  # Requirement 8.9
                return SubmitResult.synthetic_success_already_completed()
            raise
        except FrappeUnavailable as e:
            logger.warning("submit_unavailable", extra={"error_msg": str(e), "attempt": attempt + 1})
            last_exc = e
            if attempt < 3:
                await asyncio.sleep(_BACKOFFS[attempt])
            # On last attempt, fall through to raise

    # All retries exhausted
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


@router.callback_query(lambda c: c.data == CB_KIRIM_HASIL)
async def handle_kirim_hasil(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
    frappe_client: FrappeClient,
    settings: Settings,
) -> None:
    """Handle 'Kirim Hasil' callback from the summary page.

    Launches the submission pipeline as a background task so the inspector
    can continue working (e.g. start inspecting the next motor) without
    waiting for the upload+submit to finish (~30-40s).

    The bot will send a notification when the submission completes or fails.
    """
    telegram_id = str(callback.from_user.id)
    chat_id = callback.from_user.id
    await callback.answer()

    # Find active session in SUMMARY phase
    session = await _find_summary_session(telegram_id, session_store)
    if session is None:
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Inspeksi ini sudah dikirim atau sesi telah berakhir.\n"
                "Ketik /mulai untuk melihat daftar motor."
            )
        return

    # Pre-submit validation (fast, do it synchronously before background)
    errors = validate_pre_submit(session)
    if errors:
        await _handle_pre_submit_error(callback, PreSubmitValidationError(errors))
        return

    # Mark session as submitting to prevent double-click
    # (delete session from SUMMARY phase so second click won't find it)
    submitting_session = session.model_copy(update={"phase": Phase.IDLE})
    await session_store.save_session(submitting_session)

    # Immediately reply — inspector can continue working
    if callback.message:
        await callback.message.answer(  # type: ignore[union-attr]
            f"⏳ Mengirim hasil inspeksi untuk {session.motor_meta.nopol}...\n\n"
            "Anda bisa melanjutkan ke motor berikutnya (jika ada).",
        )

    # Auto-show motor list (exclude the motor being submitted)
    from bot.handlers.motor_selection import show_motor_list_excluding
    await show_motor_list_excluding(callback, telegram_id, frappe_client, session_store, exclude_motor=session.motor_id)

    # Launch background task
    bot = callback.bot
    asyncio.create_task(
        _background_submit(
            session=session,
            bot=bot,
            frappe=frappe_client,
            settings=settings,
            session_store=session_store,
            chat_id=chat_id,
        )
    )


async def _background_submit(
    *,
    session: Session,
    bot,
    frappe: FrappeClient,
    settings: Settings,
    session_store: RedisSessionStore,
    chat_id: int,
) -> None:
    """Run the submission pipeline in the background and notify the user."""
    nopol = session.motor_meta.nopol

    try:
        result = await _submit_inspection(
            session,
            bot=bot,
            frappe=frappe,
            settings=settings,
        )
    except (StatusChanged, StatusMismatch):
        await session_store.delete_session(session.telegram_id, session.motor_id)
        await session_store.remove_pending(session.telegram_id, session.motor_id)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ Pengiriman gagal - {nopol}\n\n"
                "Motor sudah dialihkan atau status berubah.\n"
                "Ketik /mulai untuk melihat daftar motor terbaru."
            ),
        )
        return
    except FrappeValidationError as e:
        # Restore session to SUMMARY so user can retry
        await session_store.save_session(session)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"❌ Pengiriman gagal - {nopol}\n\n"
                f"Server menolak: {e.message}\n\n"
                "Tekan Kirim Hasil lagi untuk mencoba ulang."
            ),
        )
        return
    except FrappePermissionError:
        await session_store.delete_session(session.telegram_id, session.motor_id)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🚫 Akses ditolak - {nopol}\n\n"
                "Hubungi admin."
            ),
        )
        return
    except FrappeUnavailable:
        # Restore session to SUMMARY so user can retry
        await session_store.save_session(session)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"❌ Gagal mengirim - {nopol}\n\n"
                "Server tidak merespons setelah beberapa percobaan.\n"
                "Tekan Kirim Hasil lagi untuk mencoba ulang."
            ),
        )
        return
    except Exception as e:
        logger.exception("background_submit_unexpected_error")
        # Restore session to SUMMARY so user can retry
        await session_store.save_session(session)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"❌ Error tidak terduga - {nopol}\n\n"
                "Silakan coba lagi atau hubungi admin."
            ),
        )
        return

    # --- Success ---
    await session_store.delete_session(session.telegram_id, session.motor_id)
    await session_store.remove_pending(session.telegram_id, session.motor_id)

    logger.info(
        "submit_completed",
        extra={
            "telegram_id": session.telegram_id,
            "motor_id": session.motor_id,
            "doc_name": result.name,
        },
    )

    if result.already_completed:
        text = (
            f"✅ Inspeksi sudah tercatat - {nopol}\n\n"
            "Status: Selesai"
        )
    else:
        doc_name = result.name or "-"
        text = (
            f"✅ Hasil inspeksi berhasil dikirim!\n\n"
            f"Motor: {nopol}\n"
            f"Dokumen: {doc_name}"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Lihat Daftar Motor",
                    callback_data="lihat_daftar_motor",
                )
            ]
        ]
    )

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _handle_pre_submit_error(
    callback: CallbackQuery,
    error: PreSubmitValidationError,
) -> None:
    """Handle pre-submit validation failure (Requirement 8.2)."""
    field_lines = []
    has_missing_photos = False
    for err in error.errors[:10]:
        field_lines.append(f"• {err.field}: {err.reason}")
        if err.reason == "missing_photo":
            has_missing_photos = True
    if len(error.errors) > 10:
        field_lines.append(f"... dan {len(error.errors) - 10} lainnya")

    text = (
        "❌ Data belum lengkap\n\n"
        "Field yang belum terisi:\n"
        + "\n".join(field_lines)
        + "\n\nSilakan lengkapi data terlebih dahulu."
    )

    # Add "Lengkapi Foto" button if there are missing photos
    if has_missing_photos and callback.message:
        from bot.handlers.summary import CB_LENGKAPI_FOTO
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📷 Lengkapi Foto", callback_data=CB_LENGKAPI_FOTO)]
            ]
        )
        await callback.message.answer(text, reply_markup=keyboard)  # type: ignore[union-attr]
    elif callback.message:
        await callback.message.answer(text)  # type: ignore[union-attr]


async def _find_summary_session(
    telegram_id: str,
    store: RedisSessionStore,
) -> Session | None:
    """Find the active session in SUMMARY phase for the given telegram_id.

    Iterates through pending motors to find a session with phase=SUMMARY.
    Returns None if no active summary session is found.
    """
    from bot.domain.models import Phase

    try:
        pending = await store.list_pending(telegram_id)
    except Exception:
        return None

    for motor_id in pending:
        try:
            session = await store.get_session(telegram_id, motor_id)
        except Exception:
            continue
        if session is not None and session.phase == Phase.SUMMARY:
            return session
    return None
