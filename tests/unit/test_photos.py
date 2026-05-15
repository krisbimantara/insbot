"""Unit tests for the photo capture handler (src/bot/handlers/photos.py).

Tests cover:
- Photo prompt display with label, description, and progress
- ReplyKeyboardRemove on first photo prompt
- Accept photo message, save file_id
- Accept document-image, save file_id
- Reject non-image with error message
- Inline Keyboard [Konfirmasi] [Foto Ulang] after photo
- Confirm advances photo_index
- Retry clears photo and re-prompts
- Transition to Summary when all 10 confirmed

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.domain.models import MotorMeta, Phase, Session, PHOTO_FIELDS
from bot.handlers.photos import (
    CB_PHOTO_CONFIRM,
    CB_PHOTO_RETRY,
    PHOTO_LABELS,
    PHOTO_DESCRIPTIONS,
    _build_photo_prompt,
    _build_confirm_retry_keyboard,
    handle_photo_message,
    handle_document_message,
    handle_non_image_message,
    handle_photo_confirm,
    handle_photo_retry,
    send_photo_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_session(
    telegram_id: str = "123456789",
    motor_id: str = "PJ-001",
    phase: Phase = Phase.PHOTOS,
    photo_index: int = 0,
    photos: dict | None = None,
) -> Session:
    return Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi="Inspeksi",
        inspection_started=True,
        phase=phase,
        photo_index=photo_index,
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


def _make_session_store(session: Session | None = None):
    store = AsyncMock()
    store.get_session = AsyncMock(return_value=session)
    store.save_session = AsyncMock()
    return store


def _make_message_with_photo(user_id: int = 123456789, file_id: str = "photo_file_123"):
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()

    # Simulate photo array (Telegram sends multiple sizes, last is largest)
    photo_size = MagicMock()
    photo_size.file_id = file_id
    message.photo = [MagicMock(file_id="small_id"), photo_size]

    return message


def _make_message_with_document(
    user_id: int = 123456789,
    file_id: str = "doc_file_123",
    mime_type: str = "image/jpeg",
):
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    message.photo = None

    document = MagicMock()
    document.file_id = file_id
    document.mime_type = mime_type
    message.document = document

    return message


def _make_message_with_video(user_id: int = 123456789):
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    message.photo = None
    message.document = None
    message.video = MagicMock()
    return message


def _make_callback(data: str, user_id: int = 123456789):
    from aiogram.types import CallbackQuery as CQ

    callback = AsyncMock(spec=CQ)
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


# ---------------------------------------------------------------------------
# Tests for _build_photo_prompt
# ---------------------------------------------------------------------------


class TestBuildPhotoPrompt:
    """Tests for photo prompt text building (Requirement 6.3)."""

    def test_first_photo_prompt(self):
        """First photo prompt shows correct label, description, and progress."""
        text = _build_photo_prompt(0)
        assert "Tampak Depan" in text
        assert "Foto 1/10" in text
        assert "depan" in text.lower()

    def test_last_photo_prompt(self):
        """Last photo prompt shows Foto 10/10."""
        text = _build_photo_prompt(9)
        assert "Ban Belakang" in text
        assert "Foto 10/10" in text

    def test_middle_photo_prompt(self):
        """Middle photo prompt shows correct progress."""
        text = _build_photo_prompt(4)
        assert "Mesin" in text
        assert "Foto 5/10" in text

    def test_all_photo_prompts_have_progress(self):
        """All 10 photo prompts have correct progress format."""
        for i in range(10):
            text = _build_photo_prompt(i)
            assert f"Foto {i + 1}/10" in text


# ---------------------------------------------------------------------------
# Tests for _build_confirm_retry_keyboard
# ---------------------------------------------------------------------------


class TestBuildConfirmRetryKeyboard:
    """Tests for confirm/retry keyboard (Requirement 6.4)."""

    def test_keyboard_has_two_buttons(self):
        """Keyboard has [Konfirmasi] and [Foto Ulang] buttons."""
        keyboard = _build_confirm_retry_keyboard(0)
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert len(buttons) == 2
        assert buttons[0].text == "Konfirmasi"
        assert buttons[1].text == "Foto Ulang"

    def test_callback_data_includes_index(self):
        """Callback data includes the photo index."""
        keyboard = _build_confirm_retry_keyboard(5)
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert buttons[0].callback_data == f"{CB_PHOTO_CONFIRM}5"
        assert buttons[1].callback_data == f"{CB_PHOTO_RETRY}5"


# ---------------------------------------------------------------------------
# Tests for handle_photo_message
# ---------------------------------------------------------------------------


class TestHandlePhotoMessage:
    """Tests for photo message handling (Requirement 6.4)."""

    @pytest.mark.asyncio
    async def test_saves_file_id_on_photo(self):
        """Photo message saves file_id to session.photos[field] (Requirement 6.4)."""
        session = _make_session(photo_index=0)
        store = _make_session_store()
        message = _make_message_with_photo(file_id="photo_abc")

        await handle_photo_message(message, store, active_session=session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert saved.photos["foto_tampak_depan"] == "photo_abc"

    @pytest.mark.asyncio
    async def test_uses_largest_photo(self):
        """Uses the last (largest) photo in the array."""
        session = _make_session(photo_index=0)
        store = _make_session_store()
        message = _make_message_with_photo(file_id="largest_photo_id")

        await handle_photo_message(message, store, active_session=session)

        saved = store.save_session.call_args[0][0]
        assert saved.photos["foto_tampak_depan"] == "largest_photo_id"

    @pytest.mark.asyncio
    async def test_shows_confirm_retry_keyboard(self):
        """After receiving photo, shows Inline Keyboard [Konfirmasi] [Foto Ulang]."""
        session = _make_session(photo_index=3)
        store = _make_session_store()
        message = _make_message_with_photo()

        await handle_photo_message(message, store, active_session=session)

        message.answer.assert_awaited_once()
        call_kwargs = message.answer.call_args.kwargs
        keyboard = call_kwargs.get("reply_markup")
        assert keyboard is not None
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert any("Konfirmasi" in btn.text for btn in buttons)
        assert any("Foto Ulang" in btn.text for btn in buttons)

    @pytest.mark.asyncio
    async def test_ignores_when_not_photos_phase(self):
        """Does nothing when session is not in PHOTOS phase."""
        session = _make_session(phase=Phase.CHECKLIST)
        store = _make_session_store()
        message = _make_message_with_photo()

        await handle_photo_message(message, store, active_session=session)

        store.save_session.assert_not_awaited()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_when_no_session(self):
        """Does nothing when active_session is None."""
        store = _make_session_store()
        message = _make_message_with_photo()

        await handle_photo_message(message, store, active_session=None)

        store.save_session.assert_not_awaited()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_saves_correct_field_for_index(self):
        """Saves to the correct PHOTO_FIELDS entry based on photo_index."""
        session = _make_session(photo_index=7)  # foto_stnk
        store = _make_session_store()
        message = _make_message_with_photo(file_id="stnk_photo_id")

        await handle_photo_message(message, store, active_session=session)

        saved = store.save_session.call_args[0][0]
        assert saved.photos["foto_stnk"] == "stnk_photo_id"


# ---------------------------------------------------------------------------
# Tests for handle_document_message
# ---------------------------------------------------------------------------


class TestHandleDocumentMessage:
    """Tests for document-image handling (Requirement 6.4)."""

    @pytest.mark.asyncio
    async def test_accepts_image_document(self):
        """Document with image/ mime_type is accepted and file_id saved."""
        session = _make_session(photo_index=2)
        store = _make_session_store()
        message = _make_message_with_document(
            file_id="doc_image_id", mime_type="image/jpeg"
        )

        await handle_document_message(message, store, active_session=session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert saved.photos["foto_tampak_kanan"] == "doc_image_id"

    @pytest.mark.asyncio
    async def test_accepts_png_document(self):
        """Document with image/png mime_type is accepted."""
        session = _make_session(photo_index=0)
        store = _make_session_store()
        message = _make_message_with_document(
            file_id="png_doc_id", mime_type="image/png"
        )

        await handle_document_message(message, store, active_session=session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert saved.photos["foto_tampak_depan"] == "png_doc_id"

    @pytest.mark.asyncio
    async def test_rejects_non_image_document(self):
        """Document with non-image mime_type is rejected (Requirement 6.7)."""
        session = _make_session(photo_index=0)
        store = _make_session_store()
        message = _make_message_with_document(
            file_id="pdf_id", mime_type="application/pdf"
        )

        await handle_document_message(message, store, active_session=session)

        store.save_session.assert_not_awaited()
        message.answer.assert_awaited_once()
        text = message.answer.call_args[0][0]
        assert "Mohon kirim foto (JPG/PNG)." in text

    @pytest.mark.asyncio
    async def test_rejects_video_document(self):
        """Document with video/ mime_type is rejected."""
        session = _make_session(photo_index=0)
        store = _make_session_store()
        message = _make_message_with_document(
            file_id="video_id", mime_type="video/mp4"
        )

        await handle_document_message(message, store, active_session=session)

        store.save_session.assert_not_awaited()
        message.answer.assert_awaited_once()
        text = message.answer.call_args[0][0]
        assert "Mohon kirim foto (JPG/PNG)." in text

    @pytest.mark.asyncio
    async def test_ignores_when_not_photos_phase(self):
        """Does nothing when session is not in PHOTOS phase."""
        session = _make_session(phase=Phase.SUMMARY)
        store = _make_session_store()
        message = _make_message_with_document()

        await handle_document_message(message, store, active_session=session)

        store.save_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests for handle_non_image_message
# ---------------------------------------------------------------------------


class TestHandleNonImageMessage:
    """Tests for non-image rejection (Requirement 6.7)."""

    @pytest.mark.asyncio
    async def test_rejects_video_with_error(self):
        """Video message during PHOTOS phase shows error."""
        session = _make_session(photo_index=0)
        message = _make_message_with_video()

        await handle_non_image_message(message, active_session=session)

        message.answer.assert_awaited_once()
        text = message.answer.call_args[0][0]
        assert "Mohon kirim foto (JPG/PNG)." in text

    @pytest.mark.asyncio
    async def test_ignores_when_not_photos_phase(self):
        """Does nothing when session is not in PHOTOS phase."""
        session = _make_session(phase=Phase.CHECKLIST)
        message = _make_message_with_video()

        await handle_non_image_message(message, active_session=session)

        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_when_no_session(self):
        """Does nothing when active_session is None."""
        message = _make_message_with_video()

        await handle_non_image_message(message, active_session=None)

        message.answer.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests for handle_photo_confirm
# ---------------------------------------------------------------------------


class TestHandlePhotoConfirm:
    """Tests for 'Konfirmasi' callback (Requirement 6.6)."""

    @pytest.mark.asyncio
    async def test_advances_photo_index(self):
        """Konfirmasi advances photo_index by 1 (Requirement 6.6)."""
        session = _make_session(photo_index=3)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_CONFIRM}3")

        await handle_photo_confirm(callback, store, active_session=session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert saved.photo_index == 4
        assert saved.phase == Phase.PHOTOS

    @pytest.mark.asyncio
    async def test_sends_next_photo_prompt(self):
        """After confirm, sends the next photo prompt."""
        session = _make_session(photo_index=0)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_CONFIRM}0")

        await handle_photo_confirm(callback, store, active_session=session)

        # Should send next photo prompt via callback.message.answer
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "Foto 2/10" in text
        assert "Tampak Belakang" in text

    @pytest.mark.asyncio
    async def test_transitions_to_summary_on_last_photo(self):
        """Confirming photo 10 transitions to SUMMARY phase (Requirement 6.9)."""
        session = _make_session(photo_index=9)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_CONFIRM}9")

        await handle_photo_confirm(callback, store, active_session=session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert saved.photo_index == 10
        assert saved.phase == Phase.SUMMARY
        assert saved.mode == "ringkasan"

    @pytest.mark.asyncio
    async def test_summary_transition_message(self):
        """On transition to summary, shows appropriate message."""
        session = _make_session(photo_index=9)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_CONFIRM}9")

        await handle_photo_confirm(callback, store, active_session=session)

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "Ringkasan" in text or "dikonfirmasi" in text

    @pytest.mark.asyncio
    async def test_inactive_session_shows_error(self):
        """If session is not in PHOTOS phase, shows error."""
        session = _make_session(phase=Phase.CHECKLIST)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_CONFIRM}0")

        await handle_photo_confirm(callback, store, active_session=session)

        callback.answer.assert_awaited_once()
        # Should have been called with error text
        call_args = callback.answer.call_args
        assert "tidak aktif" in (call_args[0][0] if call_args[0] else "")

    @pytest.mark.asyncio
    async def test_no_session_shows_error(self):
        """If active_session is None, shows error."""
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_CONFIRM}0")

        await handle_photo_confirm(callback, store, active_session=None)

        callback.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests for handle_photo_retry
# ---------------------------------------------------------------------------


class TestHandlePhotoRetry:
    """Tests for 'Foto Ulang' callback (Requirement 6.5)."""

    @pytest.mark.asyncio
    async def test_clears_photo_and_reprompts(self):
        """Foto Ulang clears photos[field] and re-prompts (Requirement 6.5)."""
        session = _make_session(
            photo_index=2,
            photos={"foto_tampak_kanan": "old_file_id"},
        )
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_RETRY}2")

        await handle_photo_retry(callback, store, active_session=session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert "foto_tampak_kanan" not in saved.photos

    @pytest.mark.asyncio
    async def test_does_not_advance_index(self):
        """Foto Ulang does not advance photo_index."""
        session = _make_session(photo_index=5)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_RETRY}5")

        await handle_photo_retry(callback, store, active_session=session)

        saved = store.save_session.call_args[0][0]
        assert saved.photo_index == 5

    @pytest.mark.asyncio
    async def test_reprompts_same_photo(self):
        """After retry, re-prompts the same photo."""
        session = _make_session(photo_index=5)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_RETRY}5")

        await handle_photo_retry(callback, store, active_session=session)

        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "Foto 6/10" in text
        assert "Nomor Rangka" in text

    @pytest.mark.asyncio
    async def test_inactive_session_shows_error(self):
        """If session is not in PHOTOS phase, shows error."""
        session = _make_session(phase=Phase.SUMMARY)
        store = _make_session_store()
        callback = _make_callback(data=f"{CB_PHOTO_RETRY}0")

        await handle_photo_retry(callback, store, active_session=session)

        callback.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests for send_photo_prompt
# ---------------------------------------------------------------------------


class TestSendPhotoPrompt:
    """Tests for send_photo_prompt helper (Requirements 6.1, 6.3)."""

    @pytest.mark.asyncio
    async def test_first_photo_includes_reply_keyboard_remove(self):
        """First photo prompt includes ReplyKeyboardRemove (Requirement 6.1)."""
        session = _make_session(photo_index=0)
        message = AsyncMock()
        message.answer = AsyncMock()

        await send_photo_prompt(message, session)

        message.answer.assert_awaited_once()
        call_kwargs = message.answer.call_args.kwargs
        from aiogram.types import ReplyKeyboardRemove
        assert isinstance(call_kwargs.get("reply_markup"), ReplyKeyboardRemove)

    @pytest.mark.asyncio
    async def test_non_first_photo_no_reply_keyboard_remove(self):
        """Non-first photo prompts do not include ReplyKeyboardRemove."""
        session = _make_session(photo_index=3)
        message = AsyncMock()
        message.answer = AsyncMock()

        await send_photo_prompt(message, session)

        message.answer.assert_awaited_once()
        call_kwargs = message.answer.call_args.kwargs
        assert call_kwargs.get("reply_markup") is None

    @pytest.mark.asyncio
    async def test_prompt_shows_correct_label_and_progress(self):
        """Photo prompt shows label and progress (Requirement 6.3)."""
        session = _make_session(photo_index=6)
        message = AsyncMock()
        message.answer = AsyncMock()

        await send_photo_prompt(message, session)

        text = message.answer.call_args[0][0]
        assert "Nomor Mesin" in text
        assert "Foto 7/10" in text

    @pytest.mark.asyncio
    async def test_callback_target_uses_message_answer(self):
        """When target is CallbackQuery, uses callback.message.answer."""
        session = _make_session(photo_index=0)
        callback = _make_callback(data="test")

        await send_photo_prompt(callback, session)

        callback.message.answer.assert_awaited_once()
