"""Telegram command handlers for the Inspection Bot.

Handles: /start, /mulai, /bantuan, /status, /batal

Uses aiogram 3.x Router pattern. Each handler receives dependencies
(session store, Frappe client) via the router's workflow data or
direct injection.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.adapters.frappe import FrappeClient
from bot.adapters.redis_store import RedisSessionStore
from bot.domain.models import MANDATORY_FIELDS, PHOTO_FIELDS, Phase, Session
from bot.domain.progress import compute_progress

router = Router(name="commands")

# ---------------------------------------------------------------------------
# Callback data constants
# ---------------------------------------------------------------------------

CB_LIHAT_DAFTAR = "lihat_daftar_motor"

# ---------------------------------------------------------------------------
# /start — welcome message + Inline Keyboard [Lihat Daftar Motor]
# ---------------------------------------------------------------------------


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Welcome message with Inline Keyboard to view motor list.

    Requirement 10.1: Show welcome, brief instructions, and Inline Keyboard
    with [Lihat Daftar Motor]. No Reply Keyboard on initial screen.
    """
    text = (
        "👋 Selamat datang di Bot Inspeksi Kendaraan!\n\n"
        "Bot ini membantu Anda menyelesaikan inspeksi motor yang ditugaskan.\n\n"
        "Tekan tombol di bawah untuk melihat daftar motor, "
        "atau ketik /bantuan untuk informasi lebih lanjut."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Lihat Daftar Motor", callback_data=CB_LIHAT_DAFTAR)]
        ]
    )
    await message.answer(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /mulai — trigger motor list flow (Requirement 10.2 → Requirement 3)
# ---------------------------------------------------------------------------


@router.message(Command("mulai"))
async def cmd_mulai(
    message: Message,
    session_store: RedisSessionStore,
    frappe_client: FrappeClient,
) -> None:
    """Trigger the motor list flow.

    Requirement 10.2: Run the Daftar Motor flow (Requirement 3).
    Fetches pending list from Frappe, refreshes Redis, and displays
    motors as Inline Keyboard buttons.
    """
    telegram_id = str(message.from_user.id)

    # Fetch pending list from Frappe (source of truth per Requirement 3.1)
    motors = await frappe_client.get_pending_list(telegram_id)

    if not motors:
        await message.answer("Tidak ada tugas inspeksi yang tersisa.")
        return

    # Refresh pending_motors in Redis (Requirement 3.7)
    motor_ids = [m.name for m in motors]
    await session_store.replace_pending(telegram_id, motor_ids)

    # Build Inline Keyboard with motor list (Requirement 3.2)
    buttons = []
    for motor in motors:
        label = f"{motor.merk} {motor.model} {motor.tahun} — {motor.nopol}"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"motor:{motor.name}")]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Pilih motor untuk diinspeksi:", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /bantuan — help text with available commands
# ---------------------------------------------------------------------------


@router.message(Command("bantuan"))
async def cmd_bantuan(message: Message) -> None:
    """Show available commands and admin contact.

    Requirement 10.3: Display summary of available commands.
    """
    text = (
        "📋 *Perintah yang tersedia:*\n\n"
        "/start — Mulai bot dan lihat menu utama\n"
        "/mulai — Lihat daftar motor untuk diinspeksi\n"
        "/status — Lihat status inspeksi saat ini\n"
        "/batal — Batalkan pemilihan motor (sebelum inspeksi dimulai)\n"
        "/bantuan — Tampilkan pesan bantuan ini\n\n"
        "Jika mengalami kendala, hubungi admin."
    )
    await message.answer(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /status — show pending count, active motor, current category, completion %
# ---------------------------------------------------------------------------


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Show inspection status overview.

    Requirement 10.4: Display pending count, active motor, current category,
    and completion percentage (done/total out of 66 + photos).
    """
    telegram_id = str(message.from_user.id)

    # Get pending motors count
    pending = await session_store.list_pending(telegram_id)
    pending_count = len(pending)

    # Try to find an active session among pending motors
    active_session: Session | None = None
    for motor_id in pending:
        session = await session_store.get_session(telegram_id, motor_id)
        if session is not None and session.inspection_started:
            active_session = session
            break

    # If no started session, check for selected (not yet started) sessions
    if active_session is None:
        for motor_id in pending:
            session = await session_store.get_session(telegram_id, motor_id)
            if session is not None:
                active_session = session
                break

    lines: list[str] = ["📊 *Status Inspeksi*\n"]
    lines.append(f"Motor pending: {pending_count}")

    if active_session is not None:
        motor_name = active_session.motor_meta.nopol
        lines.append(f"Motor aktif: {active_session.motor_meta.merk} "
                     f"{active_session.motor_meta.model} — {motor_name}")

        if active_session.current_category:
            lines.append(f"Kategori: {active_session.current_category}")

        # Compute completion percentage (66 fields + 10 photos = 76 total)
        answered = sum(
            1 for f in MANDATORY_FIELDS if active_session.answers.get(f) is not None
        )
        photos_done = len(active_session.photos)
        total = len(MANDATORY_FIELDS) + len(PHOTO_FIELDS)  # 66 + 10 = 76
        done = answered + photos_done
        percentage = round(done / total * 100) if total > 0 else 0

        lines.append(f"Kelengkapan: {done}/{total} ({percentage}%)")
    else:
        lines.append("Tidak ada inspeksi aktif.")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /batal — cancel motor selection (only when inspection_started=false)
# ---------------------------------------------------------------------------


@router.message(Command("batal"))
async def cmd_batal(
    message: Message,
    session_store: RedisSessionStore,
) -> None:
    """Cancel motor selection.

    Requirement 10.5: When inspection_started=true, reject with message.
    Requirement 10.6: When inspection_started=false, clear motor selection
    without changing pending_motors.
    """
    telegram_id = str(message.from_user.id)

    # Find any active session for this user
    pending = await session_store.list_pending(telegram_id)

    active_session: Session | None = None
    for motor_id in pending:
        session = await session_store.get_session(telegram_id, motor_id)
        if session is not None:
            active_session = session
            break

    if active_session is None:
        await message.answer("Tidak ada motor yang dipilih untuk dibatalkan.")
        return

    # Guard: reject if inspection already started (Requirement 10.5)
    if active_session.inspection_started:
        await message.answer(
            "Inspeksi tidak dapat dibatalkan setelah dimulai. "
            "Hubungi admin jika perlu reset."
        )
        return

    # Clear motor selection without changing pending_motors (Requirement 10.6)
    await session_store.delete_session(telegram_id, active_session.motor_id)
    await message.answer(
        "✅ Pemilihan motor dibatalkan. Ketik /mulai untuk memilih motor lain."
    )
