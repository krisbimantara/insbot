"""Unit tests for the STNK conditional handler (src/bot/handlers/stnk.py).

Tests cover:
- Boolean field handling (Ya/Tidak/Skip)
- Date field handling (valid date, invalid date, Skip)
- Transition to PHOTOS phase when all questions answered
- Invalid input re-displays current question
- Redis save failure handling
- stnk_answer stored separately in session

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.models import MotorMeta, Phase, Session
from bot.handlers.stnk import (
    _build_boolean_keyboard,
    _build_date_keyboard,
    handle_stnk_conditional_text,
    send_next_stnk_question,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_MOTOR_META = MotorMeta(
    name="PJ-001",
    nopol="B1234XY",
    merk="Honda",
    model="Beat",
    tahun="2020",
    warna="Merah",
)


def _make_session(
    stnk_answer="Cukup",
    answers=None,
    phase=Phase.STNK_CONDITIONAL,
) -> Session:
    return Session(
        telegram_id="123",
        motor_id="PJ-001",
        tipe_inspeksi="Inspeksi",
        phase=phase,
        stnk_answer=stnk_answer,
        answers=answers or {},
        motor_meta=_MOTOR_META,
        inspection_started=True,
    )


def _make_message(text: str = "", user_id: int = 123):
    message = AsyncMock()
    message.text = text
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


def _make_session_store(save_raises: bool = False):
    store = AsyncMock(spec=RedisSessionStore)
    if save_raises:
        store.save_session = AsyncMock(side_effect=Exception("Redis error"))
    else:
        store.save_session = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# Tests for keyboard builders
# ---------------------------------------------------------------------------


class TestKeyboardBuilders:
    def test_boolean_keyboard_has_three_buttons(self):
        kb = _build_boolean_keyboard()
        buttons = kb.keyboard[0]
        texts = [btn.text for btn in buttons]
        assert texts == ["Ya", "Tidak", "Skip"]
        assert kb.resize_keyboard is True
        assert kb.one_time_keyboard is True

    def test_date_keyboard_has_skip_button(self):
        kb = _build_date_keyboard()
        buttons = kb.keyboard[0]
        texts = [btn.text for btn in buttons]
        assert texts == ["Skip"]
        assert kb.resize_keyboard is True
        assert kb.one_time_keyboard is True


# ---------------------------------------------------------------------------
# Tests for send_next_stnk_question
# ---------------------------------------------------------------------------


class TestSendNextStnkQuestion:
    async def test_sends_boolean_question_with_reply_keyboard(self):
        session = _make_session(stnk_answer="Cukup")
        message = _make_message()

        await send_next_stnk_question(message, session)

        message.answer.assert_awaited_once()
        call_args = message.answer.call_args
        text = call_args[0][0]
        assert "STNK Hilang" in text
        kwargs = call_args.kwargs
        assert kwargs.get("reply_markup") is not None

    async def test_sends_date_question_with_skip_keyboard(self):
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        message = _make_message()

        await send_next_stnk_question(message, session)

        message.answer.assert_awaited_once()
        call_args = message.answer.call_args
        text = call_args[0][0]
        assert "YYYY-MM-DD" in text

    async def test_no_question_does_not_send(self):
        """When all questions answered, send_next_stnk_question does nothing."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
                "stnk_mati_tanggal": "2024-01-01",
            },
        )
        message = _make_message()

        await send_next_stnk_question(message, session)

        message.answer.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests for handle_stnk_conditional_text — Boolean fields
# ---------------------------------------------------------------------------


class TestHandleStnkBooleanFields:
    async def test_ya_saves_and_advances(self):
        """'Ya' answer is saved and next question is displayed."""
        session = _make_session(stnk_answer="Cukup")
        store = _make_session_store()
        message = _make_message(text="Ya")

        await handle_stnk_conditional_text(message, store, session)

        # Session saved with answer
        store.save_session.assert_awaited()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.answers["stnk_hilang_polisi"] == "Ya"

        # Next question displayed
        message.answer.assert_awaited()

    async def test_tidak_saves_and_advances(self):
        """'Tidak' answer is saved and next question is displayed."""
        session = _make_session(stnk_answer="Cukup")
        store = _make_session_store()
        message = _make_message(text="Tidak")

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.answers["stnk_hilang_polisi"] == "Tidak"

    async def test_skip_saves_null_and_advances(self):
        """'Skip' saves null for the field and advances (Requirement 5.4)."""
        session = _make_session(stnk_answer="Cukup")
        store = _make_session_store()
        message = _make_message(text="Skip")

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.answers["stnk_hilang_polisi"] is None

    async def test_invalid_boolean_redisplays_question(self):
        """Invalid text for boolean field re-displays the question."""
        session = _make_session(stnk_answer="Cukup")
        store = _make_session_store()
        message = _make_message(text="Maybe")

        await handle_stnk_conditional_text(message, store, session)

        # Session NOT saved (no valid answer)
        store.save_session.assert_not_awaited()
        # Question re-displayed
        message.answer.assert_awaited()


# ---------------------------------------------------------------------------
# Tests for handle_stnk_conditional_text — Date field
# ---------------------------------------------------------------------------


class TestHandleStnkDateField:
    async def test_valid_date_saves_and_advances(self):
        """Valid YYYY-MM-DD date is saved (Requirement 5.5)."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        store = _make_session_store()
        message = _make_message(text="2024-06-15")

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.answers["stnk_mati_tanggal"] == "2024-06-15"

    async def test_invalid_date_shows_error(self):
        """Invalid date format shows error message (Requirement 5.5)."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        store = _make_session_store()
        message = _make_message(text="15-06-2024")

        await handle_stnk_conditional_text(message, store, session)

        # Session NOT saved
        store.save_session.assert_not_awaited()
        # Error message displayed
        message.answer.assert_awaited_once()
        text = message.answer.call_args[0][0]
        assert "Format tanggal tidak valid" in text
        assert "YYYY-MM-DD" in text

    async def test_date_skip_saves_null(self):
        """Skip on date field saves null (Requirement 5.5)."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        store = _make_session_store()
        message = _make_message(text="Skip")

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.answers["stnk_mati_tanggal"] is None

    async def test_non_date_non_skip_shows_error(self):
        """Random text for date field shows error."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        store = _make_session_store()
        message = _make_message(text="kemarin")

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_not_awaited()
        message.answer.assert_awaited_once()
        text = message.answer.call_args[0][0]
        assert "Format tanggal tidak valid" in text


# ---------------------------------------------------------------------------
# Tests for transition to PHOTOS phase
# ---------------------------------------------------------------------------


class TestTransitionToPhotos:
    async def test_cukup_all_answered_transitions_to_photos(self):
        """When all Cukup questions answered, transitions to PHOTOS (Requirement 5.2)."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
            },
        )
        store = _make_session_store()
        message = _make_message(text="2024-01-01")

        await handle_stnk_conditional_text(message, store, session)

        # Should save twice: once for answer, once for phase transition
        assert store.save_session.await_count == 2
        final_session = store.save_session.call_args_list[1][0][0]
        assert final_session.phase == Phase.PHOTOS
        assert final_session.photo_index == 0

        # Transition message sent
        answer_calls = message.answer.call_args_list
        # Last call should be the transition message
        last_text = answer_calls[-1][0][0]
        assert "selesai" in last_text.lower() or "foto" in last_text.lower()

    async def test_rusak_all_answered_transitions_to_photos(self):
        """When all Rusak questions answered, transitions to PHOTOS (Requirement 5.3)."""
        session = _make_session(
            stnk_answer="Rusak",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
                "stnk_ta": "Ya",
            },
        )
        store = _make_session_store()
        message = _make_message(text="2024-12-31")

        await handle_stnk_conditional_text(message, store, session)

        assert store.save_session.await_count == 2
        final_session = store.save_session.call_args_list[1][0][0]
        assert final_session.phase == Phase.PHOTOS

    async def test_transition_sends_reply_keyboard_remove(self):
        """Transition to PHOTOS sends ReplyKeyboardRemove (Requirement 6.1)."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
            },
        )
        store = _make_session_store()
        message = _make_message(text="Skip")

        await handle_stnk_conditional_text(message, store, session)

        # Find the transition message call
        from aiogram.types import ReplyKeyboardRemove

        answer_calls = message.answer.call_args_list
        last_call_kwargs = answer_calls[-1].kwargs
        assert isinstance(last_call_kwargs.get("reply_markup"), ReplyKeyboardRemove)


# ---------------------------------------------------------------------------
# Tests for Rusak-specific flow (4 questions)
# ---------------------------------------------------------------------------


class TestRusakFlow:
    async def test_rusak_shows_stnk_ta_question(self):
        """Rusak flow includes stnk_ta question (Requirement 5.3)."""
        session = _make_session(
            stnk_answer="Rusak",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        store = _make_session_store()
        message = _make_message(text="Ya")

        # This should answer stnk_ta and advance to stnk_mati_tanggal
        # But first, let's verify the current question is stnk_ta
        from bot.domain.stnk import next_stnk_question

        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_ta"

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.answers["stnk_ta"] == "Ya"


# ---------------------------------------------------------------------------
# Tests for Redis save failure
# ---------------------------------------------------------------------------


class TestRedisSaveFailure:
    async def test_save_failure_shows_error_and_redisplays(self):
        """Redis save failure shows error message (Requirement 4.6 pattern)."""
        session = _make_session(stnk_answer="Cukup")
        store = _make_session_store(save_raises=True)
        message = _make_message(text="Ya")

        await handle_stnk_conditional_text(message, store, session)

        message.answer.assert_awaited_once()
        text = message.answer.call_args[0][0]
        assert "Gagal menyimpan" in text


# ---------------------------------------------------------------------------
# Tests for stnk_answer stored separately (Requirement 5.8)
# ---------------------------------------------------------------------------


class TestStnkAnswerStoredSeparately:
    async def test_stnk_answer_preserved_in_session(self):
        """stnk_answer is stored separately from answers dict (Requirement 5.8)."""
        session = _make_session(stnk_answer="Cukup")
        store = _make_session_store()
        message = _make_message(text="Ya")

        await handle_stnk_conditional_text(message, store, session)

        saved = store.save_session.call_args_list[0][0][0]
        # stnk_answer remains in session.stnk_answer
        assert saved.stnk_answer == "Cukup"
        # The conditional answer is in answers dict
        assert saved.answers["stnk_hilang_polisi"] == "Ya"


# ---------------------------------------------------------------------------
# Tests for edge case: no questions remaining on entry
# ---------------------------------------------------------------------------


class TestNoQuestionsRemaining:
    async def test_all_answered_on_entry_transitions_to_photos(self):
        """If all questions already answered when handler is called, transition to PHOTOS."""
        session = _make_session(
            stnk_answer="Cukup",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
                "stnk_mati_tanggal": "2024-01-01",
            },
        )
        store = _make_session_store()
        message = _make_message(text="anything")

        await handle_stnk_conditional_text(message, store, session)

        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args_list[0][0][0]
        assert saved.phase == Phase.PHOTOS
