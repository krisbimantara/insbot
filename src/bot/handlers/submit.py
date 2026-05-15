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
    compress_if_needed,
    download_telegram_photo,
    get_photo_filename,
)
from bot.adapters.redis_store import RedisSessionStore
from bot.config import Settings
from bot.domain.models import PHOTO_FIELDS, Session, SubmitResult
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
        compressed = compress_if_needed(
            raw,
            max_bytes=settings.photo_max_bytes,
            longest_edge=settings.photo_compress_target_longest_edge,
        )
        logger.info("photo_compressed", extra={"field": field, "size_bytes": len(compressed)})
        filename = get_photo_filename(field, session.motor_id)
        url = await frappe.upload_foto(compressed, filename=filename)
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

    Orchestrates the submission pipeline and maps outcomes to user messages.
    """
    telegram_id = str(callback.from_user.id)
    await callback.answer()

    # Find active session in SUMMARY phase
    session = await _find_summary_session(telegram_id, session_store)
    if session is None:
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."
            )
        return

    # Show progress message (Requirement 8.5)
    progress_msg = None
    if callback.message:
        progress_msg = await callback.message.answer(  # type: ignore[union-attr]
            "⏳ Mengirim hasil inspeksi..."
        )

    try:
        result = await _submit_inspection(
            session,
            bot=callback.bot,
            frappe=frappe_client,
            settings=settings,
        )
    except PreSubmitValidationError as e:
        # Requirement 8.2: show missing fields, return to summary
        await _handle_pre_submit_error(callback, e)
        await _delete_progress_msg(progress_msg)
        return
    except (StatusChanged, StatusMismatch):
        # Requirement 15.2, 15.3: motor reassigned or tipe changed
        await _handle_status_error(callback, session, session_store)
        await _delete_progress_msg(progress_msg)
        return
    except FrappeValidationError as e:
        if e.indicates_payload_incomplete():
            # Requirement 8.8: payload incomplete → back to summary
            await _handle_payload_incomplete(callback, e)
        else:
            # Other validation error
            await _handle_generic_validation_error(callback, e)
        await _delete_progress_msg(progress_msg)
        return
    except FrappePermissionError:
        # Requirement 8.8: 403 → access denied + delete session
        await _handle_permission_error(callback, session, session_store)
        await _delete_progress_msg(progress_msg)
        return
    except FrappeUnavailable:
        # Requirement 8.10: all retries exhausted → manual retry message
        await _handle_unavailable(callback)
        await _delete_progress_msg(progress_msg)
        return

    # --- Success path (Requirement 8.6, 8.11) ---
    await _handle_success(callback, session, result, session_store)
    await _delete_progress_msg(progress_msg)


# ---------------------------------------------------------------------------
# Outcome handlers
# ---------------------------------------------------------------------------


async def _handle_success(
    callback: CallbackQuery,
    session: Session,
    result: SubmitResult,
    store: RedisSessionStore,
) -> None:
    """Handle successful submission (Requirement 8.6, 8.11).

    - Delete session from Redis
    - Remove motor from pending set
    - Show confirmation with doc name + optional [Lihat Daftar Motor]
    """
    # Clean up Redis
    await store.delete_session(session.telegram_id, session.motor_id)
    await store.remove_pending(session.telegram_id, session.motor_id)

    logger.info(
        "submit_success",
        extra={
            "telegram_id": session.telegram_id,
            "motor_id": session.motor_id,
            "doc_name": result.name,
            "already_completed": result.already_completed,
        },
    )

    # Build confirmation message
    if result.already_completed:
        text = (
            "✅ *Inspeksi sudah tercatat sebelumnya.*\n\n"
            f"Motor: {session.motor_meta.nopol}\n"
            "Status: Selesai"
        )
    else:
        doc_name = result.name or "—"
        text = (
            "✅ *Hasil inspeksi berhasil dikirim!*\n\n"
            f"Motor: {session.motor_meta.nopol}\n"
            f"Dokumen: `{doc_name}`"
        )

    # Optional [Lihat Daftar Motor] button (Requirement 8.11)
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

    if callback.message:
        await callback.message.answer(  # type: ignore[union-attr]
            text, parse_mode="Markdown", reply_markup=keyboard
        )


async def _handle_pre_submit_error(
    callback: CallbackQuery,
    error: PreSubmitValidationError,
) -> None:
    """Handle pre-submit validation failure (Requirement 8.2).

    Show missing fields and return to summary.
    """
    # Build list of missing fields (max 10 shown to avoid message overflow)
    field_lines = []
    for err in error.errors[:10]:
        field_lines.append(f"• {err.field}: {err.message}")
    if len(error.errors) > 10:
        field_lines.append(f"... dan {len(error.errors) - 10} lainnya")

    text = (
        "❌ *Data belum lengkap*\n\n"
        "Field yang belum terisi:\n"
        + "\n".join(field_lines)
        + "\n\nSilakan lengkapi data terlebih dahulu."
    )

    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown")  # type: ignore[union-attr]


async def _handle_status_error(
    callback: CallbackQuery,
    session: Session,
    store: RedisSessionStore,
) -> None:
    """Handle StatusChanged / StatusMismatch (Requirement 15.2, 15.3).

    Inform inspector that the motor has been reassigned and delete the session.
    """
    # Delete stale session
    await store.delete_session(session.telegram_id, session.motor_id)
    await store.remove_pending(session.telegram_id, session.motor_id)

    text = (
        "⚠️ *Motor sudah dialihkan*\n\n"
        f"Motor {session.motor_meta.nopol} sudah tidak tersedia atau "
        "status inspeksi telah berubah.\n\n"
        "Ketik /mulai untuk melihat daftar motor terbaru."
    )

    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown")  # type: ignore[union-attr]


async def _handle_payload_incomplete(
    callback: CallbackQuery,
    error: FrappeValidationError,
) -> None:
    """Handle Frappe ValidationError indicating payload incomplete (Requirement 8.8).

    Show error and return to summary.
    """
    text = (
        "❌ *Payload tidak lengkap*\n\n"
        f"Server menolak: {error.message}\n\n"
        "Silakan periksa kembali data inspeksi."
    )

    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown")  # type: ignore[union-attr]


async def _handle_generic_validation_error(
    callback: CallbackQuery,
    error: FrappeValidationError,
) -> None:
    """Handle other FrappeValidationError cases."""
    text = (
        "❌ *Gagal mengirim*\n\n"
        f"Server menolak: {error.message}\n\n"
        "Silakan hubungi admin jika masalah berlanjut."
    )

    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown")  # type: ignore[union-attr]


async def _handle_permission_error(
    callback: CallbackQuery,
    session: Session,
    store: RedisSessionStore,
) -> None:
    """Handle FrappePermissionError / 403 (Requirement 15.3).

    Delete session and show access denied message.
    Requirement 15.3: "Akses ditolak untuk motor ini. Hubungi admin."
    """
    await store.delete_session(session.telegram_id, session.motor_id)

    text = "🚫 *Akses ditolak untuk motor ini.*\n\nHubungi admin."

    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown")  # type: ignore[union-attr]


async def _handle_unavailable(callback: CallbackQuery) -> None:
    """Handle FrappeUnavailable after all retries exhausted (Requirement 8.10).

    Show manual retry message.
    """
    text = "❌ Gagal mengirim ke server. Tekan Kirim Hasil lagi untuk mencoba ulang."

    if callback.message:
        await callback.message.answer(text)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _delete_progress_msg(msg) -> None:
    """Attempt to delete the progress message; ignore errors."""
    if msg is None:
        return
    try:
        await msg.delete()
    except Exception:
        pass
