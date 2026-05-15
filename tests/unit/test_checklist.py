"""Unit tests for the checklist handler (src/bot/handlers/checklist.py).

Tests cover:
- Valid answer handling and progression
- Invalid answer re-displays same question
- Progress bar display
- Category transitions
- Redis failure handling
- Checklist completion transitions (to STNK_CONDITIONAL or PHOTOS)
- ReplyKeyboardRemove on phase exit

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    COMPONENT_OPTIONS,
    MANDATORY_FIELDS,
    MotorMeta,
    Phase,
    Session,
)
from bot.handlers.checklist import (
    _build_reply_keyboard,
    _format_question_message,
    _find_active_checklist_session,
    _display_question,
    handle_checklist_answer,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_motor_meta() -> MotorMeta:
    return MotorMeta(
        name="PJ-001",
        nopol="B 1234 XYZ",
        merk="Honda",
        model="Beat",
        tahun="2022",
        warna="Merah",
    )


def _make_session(
    telegram_id: str = "123456789",
    motor_id: str = "PJ-001",
    phase: Phase = Phase.CHECKLIST,
    answers: dict | None = None,
    current_category: str | None = None,
    current_question: str | None = None,
    completed_categories: list | None = None,
    stnk_answer: str | None = None,
) -> Session:
    """Create a test session in CHECKLIST phase."""
    if current_category is None:
        current_category = CATEGORIES[0]
    if current_question is None:
        current_question = CATEGORY_FIELDS[current_category][0]
    return Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi="Inspeksi",
        inspection_started=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        phase=phase,
        current_category=current_category,
        current_question=current_question,
        answers=answers or {},
        completed_categories=completed_categories or [],
        stnk_answer=stnk_answer,
        motor_meta=_make_motor_meta(),
    )


def _make_session_store(session: Session | None = None, pending: set | None = None):
    """Create a mock session store."""
    store = AsyncMock()
    store.list_pending = AsyncMock(return_value=pending or {"PJ-001"})
    store.get_session = AsyncMock(return_value=session)
    store.save_session = AsyncMock()
    return store


def _make_message(text: str = "Baik", user_id: int = 123456789):
    """Create a mock Message."""
    msg = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# Tests for _build_reply_keyboard
# ---------------------------------------------------------------------------


class TestBuildReplyKeyboard:
    """Tests for Reply Keyboard building (Requirement 4.3, 4.4)."""

    def test_default_options_keyboard(self):
        """Default options produce 3-button keyboard."""
        keyboard = _build_reply_keyboard(("Baik", "Cukup", "Rusak"))
        assert keyboard.one_time_keyboard is True
        assert keyboard.resize_keyboard is True
        buttons = keyboard.keyboard[0]
        assert len(buttons) == 3
        assert buttons[0].text == "Baik"
        assert buttons[1].text == "Cukup"
        assert buttons[2].text == "Rusak"

    def test_fuel_options_keyboard(self):
        """Fuel options produce 5-button keyboard (Requirement 4.4)."""
        keyboard = _build_reply_keyboard(("E", "1/4", "1/2", "3/4", "F"))
        buttons = keyboard.keyboard[0]
        assert len(buttons) == 5
        assert buttons[0].text == "E"
        assert buttons[4].text == "F"


# ---------------------------------------------------------------------------
# Tests for _format_question_message
# ---------------------------------------------------------------------------


class TestFormatQuestionMessage:
    """Tests for question message formatting (Requirement 4.3)."""

    def test_includes_category_and_label(self):
        text = _format_question_message(
            label="Kepala",
            category="Body & Rangka",
            done=5,
            total=66,
        )
        assert "Body & Rangka" in text
        assert "Kepala" in text

    def test_includes_progress_bar(self):
        text = _format_question_message(
            label="Kepala",
            category="Body & Rangka",
            done=33,
            total=66,
        )
        assert "33/66" in text
        # Should contain progress bar characters
        assert "█" in text or "░" in text


# ---------------------------------------------------------------------------
# Tests for _find_active_checklist_session
# ---------------------------------------------------------------------------


class TestFindActiveChecklistSession:
    """Tests for finding active checklist session."""

    @pytest.mark.asyncio
    async def test_finds_checklist_session(self):
        session = _make_session(phase=Phase.CHECKLIST)
        store = _make_session_store(session=session, pending={"PJ-001"})

        result = await _find_active_checklist_session("123456789", store)

        assert result is not None
        assert result.phase == Phase.CHECKLIST

    @pytest.mark.asyncio
    async def test_returns_none_for_non_checklist_phase(self):
        session = _make_session(phase=Phase.PHOTOS)
        store = _make_session_store(session=session, pending={"PJ-001"})

        result = await _find_active_checklist_session("123456789", store)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_pending(self):
        store = _make_session_store(session=None, pending=set())

        result = await _find_active_checklist_session("123456789", store)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_redis_error(self):
        store = AsyncMock()
        store.list_pending = AsyncMock(side_effect=RedisError("Connection refused"))

        result = await _find_active_checklist_session("123456789", store)

        assert result is None


# ---------------------------------------------------------------------------
# Tests for handle_checklist_answer
# ---------------------------------------------------------------------------


class TestHandleChecklistAnswer:
    """Tests for the main checklist answer handler."""

    @pytest.mark.asyncio
    async def test_valid_answer_saves_and_advances(self):
        """Valid answer is saved to Redis and advances to next question (Req 4.6)."""
        session = _make_session(
            current_category="Body & Rangka",
            current_question="kepala",
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="Baik")

        await handle_checklist_answer(message, store)

        # Session should be saved
        store.save_session.assert_awaited()
        saved_session = store.save_session.call_args[0][0]
        assert saved_session.answers.get("kepala") == "Baik"
        # Should advance to next question
        assert saved_session.current_question == "sayap_dalam"

    @pytest.mark.asyncio
    async def test_invalid_answer_redisplays_question(self):
        """Invalid answer re-displays same question (Requirement 4.5, 4.8)."""
        session = _make_session(
            current_category="Body & Rangka",
            current_question="kepala",
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="InvalidOption")

        await handle_checklist_answer(message, store)

        # Session should NOT be saved
        store.save_session.assert_not_awaited()
        # Should re-display question with keyboard
        message.answer.assert_awaited()
        call_kwargs = message.answer.call_args.kwargs
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_free_text_not_in_options_redisplays(self):
        """Free text that's not in option set re-displays (Requirement 4.8)."""
        session = _make_session(
            current_category="Body & Rangka",
            current_question="kepala",
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="some random text")

        await handle_checklist_answer(message, store)

        store.save_session.assert_not_awaited()
        message.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_redis_failure_shows_error_and_redisplays(self):
        """Redis failure shows error message and re-displays (Requirement 4.6)."""
        session = _make_session(
            current_category="Body & Rangka",
            current_question="kepala",
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        store.save_session = AsyncMock(side_effect=RedisError("Connection refused"))
        message = _make_message(text="Baik")

        await handle_checklist_answer(message, store)

        # Should show error message
        message.answer.assert_awaited()
        call_args = message.answer.call_args
        text = call_args[0][0] if call_args[0] else call_args.kwargs.get("text", "")
        assert "Gagal menyimpan" in text

    @pytest.mark.asyncio
    async def test_fuel_options_accepted(self):
        """Fuel options (E/1/4/1/2/3/4/F) are accepted for bahan_bakar (Req 4.4)."""
        # Build a session where bahan_bakar is the current question
        # Fill all fields before bahan_bakar
        answers = {}
        for field in MANDATORY_FIELDS:
            if field == "bahan_bakar":
                break
            answers[field] = "Baik"

        session = _make_session(
            current_category="Mesin",
            current_question="bahan_bakar",
            answers=answers,
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="1/2")

        await handle_checklist_answer(message, store)

        store.save_session.assert_awaited()
        saved_session = store.save_session.call_args[0][0]
        assert saved_session.answers.get("bahan_bakar") == "1/2"

    @pytest.mark.asyncio
    async def test_category_transition_message(self):
        """Category completion shows transition message (Requirement 4.7)."""
        # Fill all fields in Body & Rangka except the last one
        body_fields = CATEGORY_FIELDS["Body & Rangka"]
        answers = {field: "Baik" for field in body_fields[:-1]}

        session = _make_session(
            current_category="Body & Rangka",
            current_question=body_fields[-1],  # last field: crankcase_assy
            answers=answers,
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="Baik")

        await handle_checklist_answer(message, store)

        # Should have multiple answer calls: transition message + next question
        assert message.answer.await_count >= 2
        # First call should be the transition message
        first_call_text = message.answer.call_args_list[0][0][0]
        assert "Body & Rangka" in first_call_text
        assert "selesai" in first_call_text

    @pytest.mark.asyncio
    async def test_no_active_session_ignores_message(self):
        """If no active checklist session, message is ignored."""
        store = _make_session_store(session=None, pending={"PJ-001"})
        message = _make_message(text="Baik")

        await handle_checklist_answer(message, store)

        store.save_session.assert_not_awaited()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_checklist_complete_stnk_baik_transitions_to_photos(self):
        """When checklist complete and stnk=Baik, transitions to PHOTOS (Req 4.10)."""
        # Fill all 66 fields except the last one (stnk)
        answers = {field: "Baik" for field in MANDATORY_FIELDS[:-1]}

        session = _make_session(
            current_category="Dokumen (STNK)",
            current_question="stnk",
            answers=answers,
            completed_categories=list(CATEGORIES[:-1]),  # all except last
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="Baik")

        await handle_checklist_answer(message, store)

        # Should save session with phase=PHOTOS
        save_calls = store.save_session.call_args_list
        # Last save should be the transition
        final_session = save_calls[-1][0][0]
        assert final_session.phase == Phase.PHOTOS
        assert final_session.stnk_answer == "Baik"

        # Should send ReplyKeyboardRemove (Requirement 4.10)
        last_answer_call = message.answer.call_args_list[-1]
        reply_markup = last_answer_call.kwargs.get("reply_markup")
        from aiogram.types import ReplyKeyboardRemove
        assert isinstance(reply_markup, ReplyKeyboardRemove)

    @pytest.mark.asyncio
    async def test_checklist_complete_stnk_rusak_transitions_to_stnk_conditional(self):
        """When checklist complete and stnk=Rusak, transitions to STNK_CONDITIONAL."""
        # Fill all 66 fields except the last one (stnk)
        answers = {field: "Baik" for field in MANDATORY_FIELDS[:-1]}

        session = _make_session(
            current_category="Dokumen (STNK)",
            current_question="stnk",
            answers=answers,
            completed_categories=list(CATEGORIES[:-1]),
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="Rusak")

        await handle_checklist_answer(message, store)

        # Should save session with phase=STNK_CONDITIONAL
        save_calls = store.save_session.call_args_list
        final_session = save_calls[-1][0][0]
        assert final_session.phase == Phase.STNK_CONDITIONAL
        assert final_session.stnk_answer == "Rusak"

    @pytest.mark.asyncio
    async def test_progress_bar_shown_in_question(self):
        """Progress bar is shown with each question (Requirement 4.3)."""
        session = _make_session(
            current_category="Body & Rangka",
            current_question="kepala",
            answers={},
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        # Send invalid to trigger re-display (easier to check output)
        message = _make_message(text="InvalidOption")

        await handle_checklist_answer(message, store)

        message.answer.assert_awaited()
        call_args = message.answer.call_args
        text = call_args[0][0] if call_args[0] else call_args.kwargs.get("text", "")
        assert "0/66" in text
        assert "░" in text

    @pytest.mark.asyncio
    async def test_maintains_current_category_and_question(self):
        """Session maintains current_category and current_question (Req 4.9)."""
        session = _make_session(
            current_category="Body & Rangka",
            current_question="kepala",
        )
        store = _make_session_store(session=session, pending={"PJ-001"})
        message = _make_message(text="Cukup")

        await handle_checklist_answer(message, store)

        saved_session = store.save_session.call_args[0][0]
        # After answering kepala, should advance to sayap_dalam
        assert saved_session.current_category == "Body & Rangka"
        assert saved_session.current_question == "sayap_dalam"
