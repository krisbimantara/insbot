"""Motor selection handler for the Telegram Inspection Bot.

Handles:
- Displaying the pending motor list as Inline Keyboard buttons
- Motor tap → create/load session, show confirmation card
- Existing active session → show [Lanjutkan Sesi Sebelumnya] / [Mulai Ulang]
- [Mulai Inspeksi] → start inspection (set inspection_started=true, phase=CHECKLIST)
- Refresh pending_motors from Frappe on every list display

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.adapters.exceptions import FrappeUnavailable
from bot.adapters.frappe import FrappeClient
from bot.adapters.redis_store import RedisSessionStore
from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    MotorMeta,
    MotorTarikan,
    Phase,
    Session,
)

logger = logging.getLogger(__name__)

router = Router(name="motor_selection")

# ---------------------------------------------------------------------------
# Callback data prefixes
# ---------------------------------------------------------------------------

MOTOR_SELECT_PREFIX = "motor:"
CB_MULAI_INSPEKSI = "mulai_inspeksi:"
CB_LANJUTKAN_SESI = "lanjutkan_sesi:"
CB_MULAI_ULANG = "mulai_ulang:"
CB_LIHAT_DAFTAR = "lihat_daftar_motor"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _determine_tipe_inspeksi(motor: MotorTarikan) -> str:
    """Determine tipe_inspeksi based on status_inspeksi (Requirement 3.8).

    Returns "Inspeksi Ulang" if status_inspeksi == "Proses Inspeksi Ulang",
    otherwise "Inspeksi".
    """
    if motor.status_inspeksi == "Proses Inspeksi Ulang":
        return "Inspeksi Ulang"
    return "Inspeksi"


def _build_motor_list_keyboard(motors: list[MotorTarikan]) -> InlineKeyboardMarkup:
    """Build Inline Keyboard with one button per motor (Requirement 3.2).

    Format: {merk} {model} {tahun} — {nopol}
    callback_data: motor:{motor_tarikan_name}
    """
    buttons = []
    for motor in motors:
        label = f"{motor.merk} {motor.model} {motor.tahun} — {motor.nopol}"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"{MOTOR_SELECT_PREFIX}{motor.name}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_confirmation_card_text(motor: MotorTarikan, tipe_inspeksi: str) -> str:
    """Build the confirmation card text showing motor details (Requirement 3.4)."""
    return (
        f"🏍 *Konfirmasi Motor*\n\n"
        f"Nopol: `{motor.nopol}`\n"
        f"Merk: {motor.merk}\n"
        f"Model: {motor.model}\n"
        f"Tahun: {motor.tahun}\n"
        f"Warna: {motor.warna}\n"
        f"Tipe Inspeksi: *{tipe_inspeksi}*"
    )


def _build_confirmation_card_text_from_meta(meta: MotorMeta, tipe_inspeksi: str) -> str:
    """Build the confirmation card text from MotorMeta (for existing sessions)."""
    return (
        f"🏍 *Konfirmasi Motor*\n\n"
        f"Nopol: `{meta.nopol}`\n"
        f"Merk: {meta.merk}\n"
        f"Model: {meta.model}\n"
        f"Tahun: {meta.tahun}\n"
        f"Warna: {meta.warna}\n"
        f"Tipe Inspeksi: *{tipe_inspeksi}*"
    )


# ---------------------------------------------------------------------------
# Public function: show motor list (called by /mulai and CB_LIHAT_DAFTAR)
# ---------------------------------------------------------------------------


async def show_motor_list(
    callback_or_message,
    telegram_id: str,
    frappe: FrappeClient,
    store: RedisSessionStore,
) -> None:
    """Fetch pending motors from Frappe, refresh Redis, and display the list.

    This function is called by both the /mulai command handler and the
    "Lihat Daftar Motor" callback. It:
    1. Calls get_pending_list from Frappe (Requirement 3.1)
    2. Replaces pending_motors in Redis with fresh data (Requirement 3.7)
    3. Displays the motor list as Inline Keyboard or empty message (Requirement 3.3)
    """
    try:
        motors = await frappe.get_pending_list(telegram_id)
    except FrappeUnavailable:
        await _send_text(callback_or_message, "Sistem sedang sibuk, silakan coba lagi sebentar.")
        return

    # Refresh pending_motors in Redis (Requirement 3.7)
    motor_ids = [m.name for m in motors]
    await store.replace_pending(telegram_id, motor_ids)

    if not motors:
        # Empty list (Requirement 3.3)
        await _send_text(callback_or_message, "Tidak ada tugas inspeksi yang tersisa.")
        return

    # Display motor list as Inline Keyboard (Requirement 3.2)
    keyboard = _build_motor_list_keyboard(motors)
    await _send_text(
        callback_or_message,
        "📋 *Daftar Motor Pending*\n\nPilih motor untuk diinspeksi:",
        reply_markup=keyboard,
    )


async def _send_text(target, text: str, **kwargs) -> None:
    """Send text to either a CallbackQuery or Message target."""
    from aiogram.types import Message

    if isinstance(target, CallbackQuery):
        await target.answer()
        if target.message is not None:
            await target.message.answer(text, parse_mode="Markdown", **kwargs)  # type: ignore[union-attr]
    elif isinstance(target, Message):
        await target.answer(text, parse_mode="Markdown", **kwargs)
    else:
        # Fallback: try answer method
        await target.answer(text, parse_mode="Markdown", **kwargs)


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------


@router.callback_query(lambda c: c.data == CB_LIHAT_DAFTAR)
async def handle_lihat_daftar(
    callback: CallbackQuery,
    frappe_client: FrappeClient,
    session_store: RedisSessionStore,
) -> None:
    """Handle 'Lihat Daftar Motor' button tap."""
    telegram_id = str(callback.from_user.id)
    await show_motor_list(callback, telegram_id, frappe_client, session_store)


@router.callback_query(lambda c: c.data and c.data.startswith(MOTOR_SELECT_PREFIX))
async def handle_motor_selected(
    callback: CallbackQuery,
    frappe_client: FrappeClient,
    session_store: RedisSessionStore,
) -> None:
    """Handle motor selection from the Inline Keyboard (Requirement 3.4, 3.5).

    On motor tap:
    - If session exists with inspection_started=true → show resume/restart options
    - Otherwise → create/load session, show confirmation card with [Mulai Inspeksi]
    """
    telegram_id = str(callback.from_user.id)
    motor_id = callback.data[len(MOTOR_SELECT_PREFIX):]  # type: ignore[index]

    # Fetch motor details from Frappe to get fresh data
    try:
        motors = await frappe_client.get_pending_list(telegram_id)
    except FrappeUnavailable:
        await callback.answer()
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Sistem sedang sibuk, silakan coba lagi sebentar."
            )
        return

    # Find the selected motor in the list
    motor = next((m for m in motors if m.name == motor_id), None)
    if motor is None:
        await callback.answer()
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Motor tidak ditemukan dalam daftar pending. "
                "Ketik /mulai untuk melihat daftar terbaru."
            )
        return

    tipe_inspeksi = _determine_tipe_inspeksi(motor)

    # Check for existing session (Requirement 3.5)
    existing_session = await session_store.get_session(telegram_id, motor_id)

    if existing_session is not None and existing_session.inspection_started:
        # Active session exists → show resume/restart options (Requirement 3.5)
        text = _build_confirmation_card_text(motor, tipe_inspeksi)
        text += "\n\n⚠️ *Sesi inspeksi sebelumnya ditemukan.*"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Lanjutkan Sesi Sebelumnya",
                        callback_data=f"{CB_LANJUTKAN_SESI}{motor_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Mulai Ulang",
                        callback_data=f"{CB_MULAI_ULANG}{motor_id}",
                    )
                ],
            ]
        )
        await callback.answer()
        if callback.message:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=keyboard)  # type: ignore[union-attr]
        return

    # No active session or session not started → create new session
    motor_meta = MotorMeta(
        name=motor.name,
        nopol=motor.nopol,
        merk=motor.merk,
        model=motor.model,
        tahun=motor.tahun,
        warna=motor.warna,
    )

    session = Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi=tipe_inspeksi,
        phase=Phase.SELECTED,
        motor_meta=motor_meta,
    )

    await session_store.save_session(session)

    # Show confirmation card with [Mulai Inspeksi] (Requirement 3.4)
    text = _build_confirmation_card_text(motor, tipe_inspeksi)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Mulai Inspeksi",
                    callback_data=f"{CB_MULAI_INSPEKSI}{motor_id}",
                )
            ]
        ]
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=keyboard)  # type: ignore[union-attr]


@router.callback_query(lambda c: c.data and c.data.startswith(CB_LANJUTKAN_SESI))
async def handle_lanjutkan_sesi(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
    frappe_client: FrappeClient,
) -> None:
    """Handle 'Lanjutkan Sesi Sebelumnya' button tap.

    Checks for session expiry (Requirement 9.6) and motor reassignment
    (Requirement 15.2) before allowing resume.
    """
    from bot.session_middleware import (
        SESSION_EXPIRED_MESSAGE,
        check_motor_reassigned,
    )

    telegram_id = str(callback.from_user.id)
    motor_id = callback.data[len(CB_LANJUTKAN_SESI):]  # type: ignore[index]

    session = await session_store.get_session(telegram_id, motor_id)
    if session is None:
        await callback.answer()
        if callback.message:
            await callback.message.answer(SESSION_EXPIRED_MESSAGE)  # type: ignore[union-attr]
        return

    # Check for motor reassignment (Requirement 15.2)
    reassigned = await check_motor_reassigned(
        callback, telegram_id, motor_id, session_store
    )
    if reassigned:
        return

    await callback.answer()
    if callback.message:
        text = (
            f"✅ Melanjutkan sesi inspeksi untuk *{session.motor_meta.nopol}*.\n\n"
            f"Fase: {session.phase.value}\n"
        )
        if session.current_category:
            text += f"Kategori: {session.current_category}\n"
        await callback.message.answer(text, parse_mode="Markdown")  # type: ignore[union-attr]


@router.callback_query(lambda c: c.data and c.data.startswith(CB_MULAI_ULANG))
async def handle_mulai_ulang(
    callback: CallbackQuery,
    frappe_client: FrappeClient,
    session_store: RedisSessionStore,
) -> None:
    """Handle 'Mulai Ulang' button tap (Requirement 3.6).

    Deletes the existing session and creates a fresh one, then shows
    the confirmation card with [Mulai Inspeksi].
    """
    telegram_id = str(callback.from_user.id)
    motor_id = callback.data[len(CB_MULAI_ULANG):]  # type: ignore[index]

    # Delete existing session (Requirement 3.6)
    await session_store.delete_session(telegram_id, motor_id)

    # Fetch motor details from Frappe
    try:
        motors = await frappe_client.get_pending_list(telegram_id)
    except FrappeUnavailable:
        await callback.answer()
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Sistem sedang sibuk, silakan coba lagi sebentar."
            )
        return

    motor = next((m for m in motors if m.name == motor_id), None)
    if motor is None:
        await callback.answer()
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Motor tidak ditemukan dalam daftar pending. "
                "Ketik /mulai untuk melihat daftar terbaru."
            )
        return

    tipe_inspeksi = _determine_tipe_inspeksi(motor)

    # Create fresh session
    motor_meta = MotorMeta(
        name=motor.name,
        nopol=motor.nopol,
        merk=motor.merk,
        model=motor.model,
        tahun=motor.tahun,
        warna=motor.warna,
    )

    session = Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi=tipe_inspeksi,
        phase=Phase.SELECTED,
        motor_meta=motor_meta,
    )

    await session_store.save_session(session)

    # Show confirmation card with [Mulai Inspeksi]
    text = _build_confirmation_card_text(motor, tipe_inspeksi)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Mulai Inspeksi",
                    callback_data=f"{CB_MULAI_INSPEKSI}{motor_id}",
                )
            ]
        ]
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=keyboard)  # type: ignore[union-attr]


@router.callback_query(lambda c: c.data and c.data.startswith(CB_MULAI_INSPEKSI))
async def handle_mulai_inspeksi(
    callback: CallbackQuery,
    session_store: RedisSessionStore,
) -> None:
    """Handle 'Mulai Inspeksi' button tap.

    Sets inspection_started=true, started_at=now, phase=CHECKLIST,
    and advances to the first question (Requirement 3.4 implied).
    """
    telegram_id = str(callback.from_user.id)
    motor_id = callback.data[len(CB_MULAI_INSPEKSI):]  # type: ignore[index]

    session = await session_store.get_session(telegram_id, motor_id)
    if session is None:
        await callback.answer()
        if callback.message:
            await callback.message.answer(  # type: ignore[union-attr]
                "Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."
            )
        return

    # Transition to CHECKLIST phase using FSM
    from bot.domain.fsm import transition_to_checklist

    updated_session = transition_to_checklist(session)
    await session_store.save_session(updated_session)

    await callback.answer()
    if callback.message:
        first_category = CATEGORIES[0]
        first_field = CATEGORY_FIELDS[first_category][0]
        await callback.message.answer(  # type: ignore[union-attr]
            f"✅ Inspeksi dimulai untuk *{updated_session.motor_meta.nopol}*\n\n"
            f"Kategori: *{first_category}*\n"
            f"Komponen pertama: {first_field}\n\n"
            f"Silakan jawab pertanyaan yang akan muncul.",
            parse_mode="Markdown",
        )
