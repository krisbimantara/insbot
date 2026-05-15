"""Unit tests for command handlers (src/bot/handlers/commands.py).

Tests cover:
- /start — welcome message + Inline Keyboard [Lihat Daftar Motor]
- /mulai — trigger motor list flow (with and without pending motors)
- /bantuan — help text with available commands
- /status — pending count, active motor, category, completion percentage
- /batal — reject when inspection_started=true, clear when false, no session

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import InlineKeyboardMarkup

from bot.domain.models import (
    CATEGORIES,
    MANDATORY_FIELDS,
    MotorMeta,
    MotorTarikan,
    Phase,
    Session,
)
from bot.handlers.commands import (
    CB_LIHAT_DAFTAR,
    cmd_bantuan,
    cmd_batal,
    cmd_mulai,
    cmd_start,
    cmd_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(user_id: int = 123456789) -> MagicMock:
    """Create a mock Message object."""
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


def _make_session_store(
    pending: set[str] | None = None,
    sessions: dict[str, Session] | None = None,
) -> AsyncMock:
    """Create a mock RedisSessionStore."""
    store = AsyncMock()
    store.list_pending = AsyncMock(return_value=pending or set())
    store.replace_pending = AsyncMock()
    store.delete_session = AsyncMock()

    _sessions = sessions or {}

    async def _get_session(telegram_id: str, motor_id: str) -> Session | None:
        return _sessions.get(motor_id)

    store.get_session = AsyncMock(side_effect=_get_session)
    return store


def _make_frappe_client(motors: list[MotorTarikan] | None = None) -> AsyncMock:
    """Create a mock FrappeClient."""
    client = AsyncMock()
    client.get_pending_list = AsyncMock(return_value=motors or [])
    return client


def _make_motor_tarikan(
    name: str = "PJ-001",
    nopol: str = "B 1234 XYZ",
    merk: str = "Honda",
    model: str = "Beat",
    tahun: str = "2022",
    warna: str = "Merah",
    status_inspeksi: str = "Proses Inspeksi",
) -> MotorTarikan:
    return MotorTarikan(
        name=name,
        nopol=nopol,
        merk=merk,
        model=model,
        tahun=tahun,
        warna=warna,
        status_inspeksi=status_inspeksi,
    )


def _make_session(
    motor_id: str = "PJ-001",
    inspection_started: bool = False,
    phase: Phase = Phase.SELECTED,
    current_category: str | None = None,
    answers: dict | None = None,
    photos: dict | None = None,
) -> Session:
    return Session(
        telegram_id="123456789",
        motor_id=motor_id,
        tipe_inspeksi="Inspeksi",
        inspection_started=inspection_started,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc) if inspection_started else None,
        phase=phase,
        current_category=current_category,
        answers=answers or {},
        photos=photos or {},
        motor_meta=MotorMeta(
            name=motor_id,
            nopol="B 1234 XYZ",
            merk="Honda",
            model="Beat",
            tahun="2022",
            warna="Merah",
        ),
    )


# ---------------------------------------------------------------------------
# /start tests
# ---------------------------------------------------------------------------


class TestCmdStart:
    """Tests for /start command handler."""

    async def test_start_sends_welcome_message(self):
        """Requirement 10.1: /start shows welcome message."""
        message = _make_message()
        await cmd_start(message)

        message.answer.assert_awaited_once()
        call_args = message.answer.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "Selamat datang" in text

    async def test_start_has_inline_keyboard(self):
        """Requirement 10.1: /start shows Inline Keyboard [Lihat Daftar Motor]."""
        message = _make_message()
        await cmd_start(message)

        call_args = message.answer.call_args
        reply_markup = call_args.kwargs.get("reply_markup")
        assert reply_markup is not None
        assert isinstance(reply_markup, InlineKeyboardMarkup)

        # Check button text and callback_data
        buttons = reply_markup.inline_keyboard
        assert len(buttons) == 1
        assert len(buttons[0]) == 1
        assert buttons[0][0].text == "Lihat Daftar Motor"
        assert buttons[0][0].callback_data == CB_LIHAT_DAFTAR


# ---------------------------------------------------------------------------
# /mulai tests
# ---------------------------------------------------------------------------


class TestCmdMulai:
    """Tests for /mulai command handler."""

    async def test_mulai_empty_list(self):
        """Requirement 3.3: Empty data array shows 'no tasks' message."""
        message = _make_message()
        store = _make_session_store()
        frappe = _make_frappe_client(motors=[])

        await cmd_mulai(message, session_store=store, frappe_client=frappe)

        message.answer.assert_awaited_once()
        text = message.answer.call_args.args[0]
        assert "Tidak ada tugas inspeksi" in text

    async def test_mulai_shows_motor_list(self):
        """Requirement 3.2: Motors displayed as Inline Keyboard buttons."""
        message = _make_message()
        motors = [
            _make_motor_tarikan(name="PJ-001", merk="Honda", model="Beat", tahun="2022", nopol="B 1234 XYZ"),
            _make_motor_tarikan(name="PJ-002", merk="Yamaha", model="Nmax", tahun="2023", nopol="B 5678 ABC"),
        ]
        store = _make_session_store()
        frappe = _make_frappe_client(motors=motors)

        await cmd_mulai(message, session_store=store, frappe_client=frappe)

        call_args = message.answer.call_args
        reply_markup = call_args.kwargs.get("reply_markup")
        assert isinstance(reply_markup, InlineKeyboardMarkup)

        buttons = reply_markup.inline_keyboard
        assert len(buttons) == 2
        assert "Honda Beat 2022" in buttons[0][0].text
        assert "B 1234 XYZ" in buttons[0][0].text
        assert buttons[0][0].callback_data == "motor:PJ-001"
        assert "Yamaha Nmax 2023" in buttons[1][0].text
        assert buttons[1][0].callback_data == "motor:PJ-002"

    async def test_mulai_refreshes_pending_in_redis(self):
        """Requirement 3.7: Refresh pending_motors from Frappe on every list display."""
        message = _make_message()
        motors = [_make_motor_tarikan(name="PJ-001")]
        store = _make_session_store()
        frappe = _make_frappe_client(motors=motors)

        await cmd_mulai(message, session_store=store, frappe_client=frappe)

        store.replace_pending.assert_awaited_once_with("123456789", ["PJ-001"])


# ---------------------------------------------------------------------------
# /bantuan tests
# ---------------------------------------------------------------------------


class TestCmdBantuan:
    """Tests for /bantuan command handler."""

    async def test_bantuan_shows_commands(self):
        """Requirement 10.3: Show available commands."""
        message = _make_message()
        await cmd_bantuan(message)

        message.answer.assert_awaited_once()
        call_args = message.answer.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "/start" in text
        assert "/mulai" in text
        assert "/status" in text
        assert "/batal" in text
        assert "/bantuan" in text

    async def test_bantuan_mentions_admin(self):
        """Requirement 10.3: Mention admin contact."""
        message = _make_message()
        await cmd_bantuan(message)

        text = message.answer.call_args.args[0]
        assert "admin" in text.lower()


# ---------------------------------------------------------------------------
# /status tests
# ---------------------------------------------------------------------------


class TestCmdStatus:
    """Tests for /status command handler."""

    async def test_status_no_pending(self):
        """Shows zero pending and no active inspection."""
        message = _make_message()
        store = _make_session_store(pending=set())

        await cmd_status(message, session_store=store)

        text = message.answer.call_args.args[0]
        assert "Motor pending: 0" in text
        assert "Tidak ada inspeksi aktif" in text

    async def test_status_with_pending_no_session(self):
        """Shows pending count but no active inspection."""
        message = _make_message()
        store = _make_session_store(pending={"PJ-001", "PJ-002"})

        await cmd_status(message, session_store=store)

        text = message.answer.call_args.args[0]
        assert "Motor pending: 2" in text

    async def test_status_with_active_session(self):
        """Requirement 10.4: Shows active motor, category, and completion."""
        session = _make_session(
            motor_id="PJ-001",
            inspection_started=True,
            phase=Phase.CHECKLIST,
            current_category="Mesin",
            answers={"kepala": "Baik", "sayap_dalam": "Cukup"},  # 2 of 66 answered
            photos={},
        )
        message = _make_message()
        store = _make_session_store(
            pending={"PJ-001"},
            sessions={"PJ-001": session},
        )

        await cmd_status(message, session_store=store)

        text = message.answer.call_args.args[0]
        assert "Honda Beat" in text
        assert "B 1234 XYZ" in text
        assert "Mesin" in text
        # 2 answered + 0 photos = 2/76 ≈ 3%
        assert "2/76" in text
        assert "3%" in text

    async def test_status_with_photos_progress(self):
        """Shows completion including photos."""
        # All 66 fields answered + 5 photos
        answers = {f: "Baik" for f in MANDATORY_FIELDS}
        photos = {f"foto_{i}": f"file_id_{i}" for i in range(5)}
        session = _make_session(
            motor_id="PJ-001",
            inspection_started=True,
            phase=Phase.PHOTOS,
            current_category=None,
            answers=answers,
            photos=photos,
        )
        message = _make_message()
        store = _make_session_store(
            pending={"PJ-001"},
            sessions={"PJ-001": session},
        )

        await cmd_status(message, session_store=store)

        text = message.answer.call_args.args[0]
        # 66 + 5 = 71/76 ≈ 93%
        assert "71/76" in text
        assert "93%" in text


# ---------------------------------------------------------------------------
# /batal tests
# ---------------------------------------------------------------------------


class TestCmdBatal:
    """Tests for /batal command handler."""

    async def test_batal_no_session(self):
        """No motor selected — inform user."""
        message = _make_message()
        store = _make_session_store(pending=set())

        await cmd_batal(message, session_store=store)

        text = message.answer.call_args.args[0]
        assert "Tidak ada motor" in text

    async def test_batal_inspection_started_rejects(self):
        """Requirement 10.5: Reject /batal when inspection_started=true."""
        session = _make_session(
            motor_id="PJ-001",
            inspection_started=True,
            phase=Phase.CHECKLIST,
        )
        message = _make_message()
        store = _make_session_store(
            pending={"PJ-001"},
            sessions={"PJ-001": session},
        )

        await cmd_batal(message, session_store=store)

        text = message.answer.call_args.args[0]
        assert "tidak dapat dibatalkan" in text.lower()
        assert "admin" in text.lower()
        # Session should NOT be deleted
        store.delete_session.assert_not_awaited()

    async def test_batal_before_start_clears_selection(self):
        """Requirement 10.6: /batal clears motor selection when inspection_started=false."""
        session = _make_session(
            motor_id="PJ-001",
            inspection_started=False,
            phase=Phase.SELECTED,
        )
        message = _make_message()
        store = _make_session_store(
            pending={"PJ-001"},
            sessions={"PJ-001": session},
        )

        await cmd_batal(message, session_store=store)

        # Session should be deleted
        store.delete_session.assert_awaited_once_with("123456789", "PJ-001")
        text = message.answer.call_args.args[0]
        assert "dibatalkan" in text.lower()

    async def test_batal_does_not_change_pending(self):
        """Requirement 10.6: /batal does not change pending_motors."""
        session = _make_session(
            motor_id="PJ-001",
            inspection_started=False,
            phase=Phase.SELECTED,
        )
        message = _make_message()
        store = _make_session_store(
            pending={"PJ-001"},
            sessions={"PJ-001": session},
        )

        await cmd_batal(message, session_store=store)

        # replace_pending and remove_pending should NOT be called
        store.replace_pending.assert_not_awaited()
        store.remove_pending.assert_not_awaited()
