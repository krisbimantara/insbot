"""Unit tests for the motor selection handler (src/bot/handlers/motor_selection.py).

Tests cover:
- Motor list display with Inline Keyboard buttons
- Empty pending list message
- Motor tap → create session, show confirmation card
- Existing active session → show resume/restart options
- Mulai Ulang → delete session, create fresh one
- Mulai Inspeksi → set inspection_started=true, phase=CHECKLIST
- Refresh pending_motors from Frappe on every list display
- tipe_inspeksi based on status_inspeksi

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.adapters.exceptions import FrappeUnavailable
from bot.domain.models import MotorMeta, MotorTarikan, Phase, Session
from bot.handlers.motor_selection import (
    CB_LIHAT_DAFTAR,
    CB_LANJUTKAN_SESI,
    CB_MULAI_INSPEKSI,
    CB_MULAI_ULANG,
    MOTOR_SELECT_PREFIX,
    _build_motor_list_keyboard,
    _determine_tipe_inspeksi,
    show_motor_list,
    handle_motor_selected,
    handle_lihat_daftar,
    handle_lanjutkan_sesi,
    handle_mulai_ulang,
    handle_mulai_inspeksi,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_motor(
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
    telegram_id: str = "123456789",
    motor_id: str = "PJ-001",
    tipe_inspeksi: str = "Inspeksi",
    inspection_started: bool = False,
    phase: Phase = Phase.SELECTED,
) -> Session:
    return Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi=tipe_inspeksi,
        inspection_started=inspection_started,
        phase=phase,
        motor_meta=MotorMeta(
            name=motor_id,
            nopol="B 1234 XYZ",
            merk="Honda",
            model="Beat",
            tahun="2022",
            warna="Merah",
        ),
    )


def _make_frappe_client(motors: list[MotorTarikan] | None = None, raises: Exception | None = None):
    client = AsyncMock()
    if raises:
        client.get_pending_list = AsyncMock(side_effect=raises)
    else:
        client.get_pending_list = AsyncMock(return_value=motors or [])
    return client


def _make_session_store(session: Session | None = None):
    store = AsyncMock()
    store.get_session = AsyncMock(return_value=session)
    store.save_session = AsyncMock()
    store.delete_session = AsyncMock()
    store.replace_pending = AsyncMock()
    return store


def _make_callback(data: str, user_id: int = 123456789):
    callback = AsyncMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _make_message(user_id: int = 123456789):
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


# ---------------------------------------------------------------------------
# Tests for _determine_tipe_inspeksi
# ---------------------------------------------------------------------------


class TestDetermineTipeInspeksi:
    """Tests for tipe_inspeksi determination (Requirement 3.8)."""

    def test_proses_inspeksi_returns_inspeksi(self):
        motor = _make_motor(status_inspeksi="Proses Inspeksi")
        assert _determine_tipe_inspeksi(motor) == "Inspeksi"

    def test_proses_inspeksi_ulang_returns_inspeksi_ulang(self):
        motor = _make_motor(status_inspeksi="Proses Inspeksi Ulang")
        assert _determine_tipe_inspeksi(motor) == "Inspeksi Ulang"


# ---------------------------------------------------------------------------
# Tests for _build_motor_list_keyboard
# ---------------------------------------------------------------------------


class TestBuildMotorListKeyboard:
    """Tests for motor list keyboard building (Requirement 3.2)."""

    def test_single_motor_keyboard(self):
        motors = [_make_motor()]
        keyboard = _build_motor_list_keyboard(motors)
        assert len(keyboard.inline_keyboard) == 1
        button = keyboard.inline_keyboard[0][0]
        assert button.text == "Honda Beat 2022 — B 1234 XYZ"
        assert button.callback_data == "motor:PJ-001"

    def test_multiple_motors_keyboard(self):
        motors = [
            _make_motor(name="PJ-001", merk="Honda", model="Beat", tahun="2022", nopol="B 1234 XYZ"),
            _make_motor(name="PJ-002", merk="Yamaha", model="Nmax", tahun="2023", nopol="B 5678 ABC"),
        ]
        keyboard = _build_motor_list_keyboard(motors)
        assert len(keyboard.inline_keyboard) == 2
        assert keyboard.inline_keyboard[0][0].callback_data == "motor:PJ-001"
        assert keyboard.inline_keyboard[1][0].callback_data == "motor:PJ-002"
        assert "Yamaha Nmax 2023 — B 5678 ABC" in keyboard.inline_keyboard[1][0].text

    def test_empty_motors_keyboard(self):
        keyboard = _build_motor_list_keyboard([])
        assert len(keyboard.inline_keyboard) == 0


# ---------------------------------------------------------------------------
# Tests for show_motor_list
# ---------------------------------------------------------------------------


class TestShowMotorList:
    """Tests for the show_motor_list function (Requirements 3.1, 3.3, 3.7)."""

    async def test_displays_motors_as_inline_keyboard(self):
        """Motors are displayed as Inline Keyboard buttons (Requirement 3.2)."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store()
        message = _make_message()

        await show_motor_list(message, "123456789", frappe, store)

        frappe.get_pending_list.assert_awaited_once_with("123456789")
        message.answer.assert_awaited_once()
        call_kwargs = message.answer.call_args.kwargs
        assert "reply_markup" in call_kwargs

    async def test_empty_list_shows_no_tasks_message(self):
        """Empty list shows 'Tidak ada tugas inspeksi yang tersisa.' (Requirement 3.3)."""
        frappe = _make_frappe_client(motors=[])
        store = _make_session_store()
        message = _make_message()

        await show_motor_list(message, "123456789", frappe, store)

        message.answer.assert_awaited_once()
        call_args = message.answer.call_args
        assert "Tidak ada tugas inspeksi yang tersisa." in call_args[0][0]

    async def test_refreshes_pending_in_redis(self):
        """Pending motors are refreshed in Redis on every display (Requirement 3.7)."""
        motors = [
            _make_motor(name="PJ-001"),
            _make_motor(name="PJ-002"),
        ]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store()
        message = _make_message()

        await show_motor_list(message, "123456789", frappe, store)

        store.replace_pending.assert_awaited_once_with("123456789", ["PJ-001", "PJ-002"])

    async def test_frappe_unavailable_shows_busy_message(self):
        """FrappeUnavailable shows busy message."""
        frappe = _make_frappe_client(raises=FrappeUnavailable("Network error"))
        store = _make_session_store()
        message = _make_message()

        await show_motor_list(message, "123456789", frappe, store)

        message.answer.assert_awaited_once()
        call_args = message.answer.call_args
        assert "Sistem sedang sibuk" in call_args[0][0]


# ---------------------------------------------------------------------------
# Tests for handle_motor_selected
# ---------------------------------------------------------------------------


class TestHandleMotorSelected:
    """Tests for motor selection callback (Requirements 3.4, 3.5)."""

    async def test_new_session_created_on_motor_tap(self):
        """Tapping a motor creates a new session and shows confirmation card (Requirement 3.4)."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{MOTOR_SELECT_PREFIX}PJ-001")

        await handle_motor_selected(callback, frappe, store)

        # Session should be saved
        store.save_session.assert_awaited_once()
        saved_session = store.save_session.call_args[0][0]
        assert saved_session.motor_id == "PJ-001"
        assert saved_session.telegram_id == "123456789"
        assert saved_session.tipe_inspeksi == "Inspeksi"
        assert saved_session.phase == Phase.SELECTED
        assert saved_session.motor_meta.nopol == "B 1234 XYZ"

    async def test_confirmation_card_shows_motor_details(self):
        """Confirmation card shows nopol, merk, model, tahun, warna, tipe_inspeksi."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{MOTOR_SELECT_PREFIX}PJ-001")

        await handle_motor_selected(callback, frappe, store)

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args.kwargs.get("text") or callback.message.answer.call_args[0][0]
        assert "B 1234 XYZ" in text
        assert "Honda" in text
        assert "Beat" in text
        assert "2022" in text
        assert "Merah" in text
        assert "Inspeksi" in text

    async def test_confirmation_card_has_mulai_inspeksi_button(self):
        """Confirmation card has [Mulai Inspeksi] button."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{MOTOR_SELECT_PREFIX}PJ-001")

        await handle_motor_selected(callback, frappe, store)

        call_kwargs = callback.message.answer.call_args.kwargs
        keyboard = call_kwargs.get("reply_markup")
        assert keyboard is not None
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert any("Mulai Inspeksi" in btn.text for btn in buttons)
        assert any(btn.callback_data.startswith(CB_MULAI_INSPEKSI) for btn in buttons)

    async def test_active_session_shows_resume_restart(self):
        """Active session shows [Lanjutkan Sesi Sebelumnya] and [Mulai Ulang] (Requirement 3.5)."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        existing = _make_session(inspection_started=True, phase=Phase.CHECKLIST)
        store = _make_session_store(session=existing)
        callback = _make_callback(data=f"{MOTOR_SELECT_PREFIX}PJ-001")

        await handle_motor_selected(callback, frappe, store)

        call_kwargs = callback.message.answer.call_args.kwargs
        keyboard = call_kwargs.get("reply_markup")
        assert keyboard is not None
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        button_texts = [btn.text for btn in buttons]
        assert "Lanjutkan Sesi Sebelumnya" in button_texts
        assert "Mulai Ulang" in button_texts

    async def test_motor_not_found_shows_error(self):
        """Motor not in pending list shows error message."""
        frappe = _make_frappe_client(motors=[])  # Empty list
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{MOTOR_SELECT_PREFIX}PJ-999")

        await handle_motor_selected(callback, frappe, store)

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "tidak ditemukan" in text

    async def test_tipe_inspeksi_ulang_set_correctly(self):
        """Motor with status 'Proses Inspeksi Ulang' sets tipe_inspeksi='Inspeksi Ulang' (Requirement 3.8)."""
        motors = [_make_motor(status_inspeksi="Proses Inspeksi Ulang")]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{MOTOR_SELECT_PREFIX}PJ-001")

        await handle_motor_selected(callback, frappe, store)

        saved_session = store.save_session.call_args[0][0]
        assert saved_session.tipe_inspeksi == "Inspeksi Ulang"


# ---------------------------------------------------------------------------
# Tests for handle_mulai_ulang
# ---------------------------------------------------------------------------


class TestHandleMulaiUlang:
    """Tests for 'Mulai Ulang' callback (Requirement 3.6)."""

    async def test_deletes_existing_session(self):
        """Mulai Ulang deletes the existing session (Requirement 3.6)."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_MULAI_ULANG}PJ-001")

        await handle_mulai_ulang(callback, frappe, store)

        store.delete_session.assert_awaited_once_with("123456789", "PJ-001")

    async def test_creates_fresh_session(self):
        """Mulai Ulang creates a fresh session after deletion."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_MULAI_ULANG}PJ-001")

        await handle_mulai_ulang(callback, frappe, store)

        store.save_session.assert_awaited_once()
        saved_session = store.save_session.call_args[0][0]
        assert saved_session.motor_id == "PJ-001"
        assert saved_session.phase == Phase.SELECTED
        assert saved_session.inspection_started is False

    async def test_shows_confirmation_card_after_restart(self):
        """After restart, shows confirmation card with [Mulai Inspeksi]."""
        motors = [_make_motor()]
        frappe = _make_frappe_client(motors=motors)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_MULAI_ULANG}PJ-001")

        await handle_mulai_ulang(callback, frappe, store)

        call_kwargs = callback.message.answer.call_args.kwargs
        keyboard = call_kwargs.get("reply_markup")
        assert keyboard is not None
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert any("Mulai Inspeksi" in btn.text for btn in buttons)


# ---------------------------------------------------------------------------
# Tests for handle_mulai_inspeksi
# ---------------------------------------------------------------------------


class TestHandleMulaiInspeksi:
    """Tests for 'Mulai Inspeksi' callback."""

    async def test_sets_inspection_started_and_phase(self):
        """Mulai Inspeksi sets inspection_started=true and phase=CHECKLIST."""
        existing = _make_session(inspection_started=False, phase=Phase.SELECTED)
        store = _make_session_store(session=existing)
        callback = _make_callback(data=f"{CB_MULAI_INSPEKSI}PJ-001")

        await handle_mulai_inspeksi(callback, store)

        store.save_session.assert_awaited_once()
        saved_session = store.save_session.call_args[0][0]
        assert saved_session.inspection_started is True
        assert saved_session.phase == Phase.CHECKLIST
        assert saved_session.started_at is not None
        assert saved_session.current_category is not None
        assert saved_session.current_question is not None

    async def test_expired_session_shows_error(self):
        """If session is None (expired), shows error message."""
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{CB_MULAI_INSPEKSI}PJ-001")

        await handle_mulai_inspeksi(callback, store)

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "berakhir" in text or "/mulai" in text


# ---------------------------------------------------------------------------
# Tests for handle_lanjutkan_sesi
# ---------------------------------------------------------------------------


class TestHandleLanjutkanSesi:
    """Tests for 'Lanjutkan Sesi Sebelumnya' callback."""

    async def test_existing_session_shows_continuation_info(self):
        """Lanjutkan shows continuation info with current phase."""
        existing = _make_session(
            inspection_started=True,
            phase=Phase.CHECKLIST,
        )
        store = _make_session_store(session=existing)
        store.list_pending = AsyncMock(return_value={"PJ-001"})
        callback = _make_callback(data=f"{CB_LANJUTKAN_SESI}PJ-001")

        await handle_lanjutkan_sesi(callback, store, _make_frappe_client())

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "B 1234 XYZ" in text

    async def test_expired_session_shows_error(self):
        """If session expired, shows error message."""
        store = _make_session_store(session=None)
        callback = _make_callback(data=f"{CB_LANJUTKAN_SESI}PJ-001")

        await handle_lanjutkan_sesi(callback, store, _make_frappe_client())

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "berakhir" in text or "/mulai" in text

    async def test_reassigned_motor_shows_error(self):
        """If motor is no longer in pending (reassigned), shows reassignment message (Requirement 15.2)."""
        existing = _make_session(
            inspection_started=True,
            phase=Phase.CHECKLIST,
        )
        store = _make_session_store(session=existing)
        # Motor PJ-001 is NOT in the pending set (reassigned)
        store.list_pending = AsyncMock(return_value={"PJ-002"})
        callback = _make_callback(data=f"{CB_LANJUTKAN_SESI}PJ-001")

        await handle_lanjutkan_sesi(callback, store, _make_frappe_client())

        # Session should be deleted
        store.delete_session.assert_awaited_once_with("123456789", "PJ-001")
        # Reassignment message shown
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "dialihkan" in text
