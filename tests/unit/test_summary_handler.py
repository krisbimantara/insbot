"""Unit tests for the summary and revision handler (src/bot/handlers/summary.py).

Tests cover:
- Summary page display (motor name, categories, photo status, keyboard)
- "Revisi Kategori" tap shows category list
- Category selection starts revision flow
- Skip preserves old value (Requirement 7.4)
- New answer overwrites (Requirement 7.5)
- Revision complete: updates revision_history, mode=ringkasan, ReplyKeyboardRemove (Requirement 7.6)
- STNK prune on category 8 revision (Requirement 7.7)
- No photo revision through this flow (Requirement 7.8)

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.adapters.redis_store import RedisSessionStore
from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    COMPONENT_OPTIONS,
    PHOTO_FIELDS,
    MotorMeta,
    Phase,
    Session,
)
from bot.handlers.summary import (
    CB_KATEGORI_SELECT,
    CB_KIRIM_HASIL,
    CB_REVISI_KATEGORI,
    _build_category_selection_keyboard,
    _build_revision_keyboard,
    _build_summary_keyboard,
    _build_summary_text,
    handle_kategori_select,
    handle_revision_answer,
    handle_revisi_kategori,
    send_summary,
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


def _make_full_answers() -> dict[str, str | None]:
    """Create a complete answers dict with all 66 mandatory fields filled."""
    from bot.domain.models import MANDATORY_FIELDS

    answers: dict[str, str | None] = {}
    for field in MANDATORY_FIELDS:
        if field == "bahan_bakar":
            answers[field] = "1/2"
        else:
            answers[field] = "Baik"
    return answers


def _make_full_photos() -> dict[str, str]:
    """Create a complete photos dict with all 10 photos."""
    return {field: f"file_id_{i}" for i, field in enumerate(PHOTO_FIELDS)}


def _make_session(
    phase: Phase = Phase.SUMMARY,
    mode: str = "ringkasan",
    answers: dict | None = None,
    photos: dict | None = None,
    revision_history: dict | None = None,
    revisi_kategori: str | None = None,
    current_question: str | None = None,
    current_category: str | None = None,
    stnk_answer: str | None = "Baik",
) -> Session:
    return Session(
        telegram_id="123",
        motor_id="PJ-001",
        tipe_inspeksi="Inspeksi",
        phase=phase,
        mode=mode,
        answers=answers if answers is not None else _make_full_answers(),
        photos=photos if photos is not None else _make_full_photos(),
        motor_meta=_MOTOR_META,
        inspection_started=True,
        revision_history=revision_history or {},
        revisi_kategori=revisi_kategori,
        current_question=current_question,
        current_category=current_category,
        stnk_answer=stnk_answer,
        completed_categories=list(CATEGORIES),
    )


def _make_message(text: str = "", user_id: int = 123):
    message = AsyncMock()
    message.text = text
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


def _make_callback(data: str = "", user_id: int = 123):
    from aiogram.types import CallbackQuery

    callback = AsyncMock(spec=CallbackQuery)
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    return callback


def _make_session_store(
    session: Session | None = None,
    save_raises: bool = False,
    pending: set[str] | None = None,
):
    store = AsyncMock(spec=RedisSessionStore)
    if save_raises:
        store.save_session = AsyncMock(side_effect=Exception("Redis error"))
    else:
        store.save_session = AsyncMock()

    if session is not None:
        store.get_session = AsyncMock(return_value=session)
    else:
        store.get_session = AsyncMock(return_value=None)

    store.list_pending = AsyncMock(return_value=pending or {"PJ-001"})
    return store


# ---------------------------------------------------------------------------
# Tests for summary text building
# ---------------------------------------------------------------------------


class TestBuildSummaryText:
    def test_contains_motor_name(self):
        session = _make_session()
        text = _build_summary_text(session)
        assert "Honda" in text
        assert "Beat" in text
        assert "2020" in text
        assert "B1234XY" in text

    def test_contains_all_categories(self):
        session = _make_session()
        text = _build_summary_text(session)
        for category in CATEGORIES:
            assert category in text

    def test_shows_done_total_per_category(self):
        session = _make_session()
        text = _build_summary_text(session)
        # Body & Rangka has 10 fields, all answered
        assert "10/10" in text

    def test_shows_direvisi_marker(self):
        """Categories in revision_history show (Direvisi) marker (Requirement 7.1)."""
        session = _make_session(
            revision_history={"Body & Rangka": datetime(2024, 1, 1, tzinfo=timezone.utc)}
        )
        text = _build_summary_text(session)
        assert "(Direvisi)" in text

    def test_shows_photo_status(self):
        session = _make_session()
        text = _build_summary_text(session)
        assert "10/10" in text
        assert "Foto" in text

    def test_partial_photos_shows_correct_count(self):
        partial_photos = {PHOTO_FIELDS[0]: "file_id_0", PHOTO_FIELDS[1]: "file_id_1"}
        session = _make_session(photos=partial_photos)
        text = _build_summary_text(session)
        assert "2/10" in text


# ---------------------------------------------------------------------------
# Tests for keyboard builders
# ---------------------------------------------------------------------------


class TestKeyboardBuilders:
    def test_summary_keyboard_has_two_buttons(self):
        kb = _build_summary_keyboard()
        buttons = kb.inline_keyboard[0]
        assert len(buttons) == 2
        assert buttons[0].text == "Revisi Kategori"
        assert buttons[0].callback_data == CB_REVISI_KATEGORI
        assert buttons[1].text == "Kirim Hasil"
        assert buttons[1].callback_data == CB_KIRIM_HASIL

    def test_category_selection_keyboard_has_8_categories(self):
        kb = _build_category_selection_keyboard()
        assert len(kb.inline_keyboard) == 8
        for i, row in enumerate(kb.inline_keyboard):
            assert len(row) == 1
            assert row[0].text == CATEGORIES[i]
            assert row[0].callback_data == f"{CB_KATEGORI_SELECT}{CATEGORIES[i]}"

    def test_revision_keyboard_has_options_plus_skip(self):
        """Revision keyboard has valid options + Skip (Requirement 7.3)."""
        kb = _build_revision_keyboard("kepala")
        buttons = kb.keyboard[0]
        texts = [btn.text for btn in buttons]
        assert "Baik" in texts
        assert "Cukup" in texts
        assert "Rusak" in texts
        assert "Skip" in texts
        assert kb.one_time_keyboard is True
        assert kb.resize_keyboard is True

    def test_revision_keyboard_bahan_bakar_has_fuel_options(self):
        """bahan_bakar revision keyboard has fuel options + Skip."""
        kb = _build_revision_keyboard("bahan_bakar")
        buttons = kb.keyboard[0]
        texts = [btn.text for btn in buttons]
        assert "E" in texts
        assert "1/4" in texts
        assert "F" in texts
        assert "Skip" in texts


# ---------------------------------------------------------------------------
# Tests for handle_revisi_kategori callback
# ---------------------------------------------------------------------------


class TestHandleRevisiKategori:
    async def test_shows_category_list(self):
        """'Revisi Kategori' tap shows 8 categories as Inline Keyboard (Requirement 7.2)."""
        session = _make_session()
        callback = _make_callback(data=CB_REVISI_KATEGORI)

        await handle_revisi_kategori(callback, AsyncMock(), active_session=session)

        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited_once()
        call_kwargs = callback.message.answer.call_args.kwargs
        kb = call_kwargs.get("reply_markup")
        assert kb is not None
        assert len(kb.inline_keyboard) == 8

    async def test_inactive_session_rejected(self):
        """Non-SUMMARY session is rejected."""
        session = _make_session(phase=Phase.CHECKLIST)
        callback = _make_callback(data=CB_REVISI_KATEGORI)

        await handle_revisi_kategori(callback, AsyncMock(), active_session=session)

        callback.answer.assert_awaited_once_with("Sesi tidak aktif.")
        callback.message.answer.assert_not_awaited()

    async def test_none_session_rejected(self):
        """None session shows expiry message (Requirement 9.6)."""
        callback = _make_callback(data=CB_REVISI_KATEGORI)

        await handle_revisi_kategori(callback, AsyncMock(), active_session=None)

        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "berakhir" in text or "/mulai" in text


# ---------------------------------------------------------------------------
# Tests for handle_kategori_select callback
# ---------------------------------------------------------------------------


class TestHandleKategoriSelect:
    async def test_starts_revision_flow(self):
        """Category selection sets mode=revisi and shows first component (Requirement 7.3)."""
        session = _make_session()
        store = _make_session_store(session=session)
        callback = _make_callback(data=f"{CB_KATEGORI_SELECT}Body & Rangka")

        await handle_kategori_select(callback, store, active_session=session)

        # callback.answer() called for acknowledgment
        callback.answer.assert_awaited_once()
        # Session saved with revision state
        store.save_session.assert_awaited_once()
        saved = store.save_session.call_args[0][0]
        assert saved.phase == Phase.REVISION
        assert saved.mode == "revisi"
        assert saved.revisi_kategori == "Body & Rangka"
        assert saved.current_question == "kepala"  # first field in Body & Rangka

        # First revision question displayed via callback.message.answer
        callback.message.answer.assert_awaited()

    async def test_invalid_category_shows_error(self):
        """Invalid category name shows error message."""
        session = _make_session()
        store = _make_session_store(session=session)
        callback = _make_callback(data=f"{CB_KATEGORI_SELECT}Invalid Category")

        await handle_kategori_select(callback, store, active_session=session)

        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.call_args[0][0]
        assert "tidak valid" in text.lower()

    async def test_non_summary_session_rejected(self):
        """Non-SUMMARY session is rejected."""
        session = _make_session(phase=Phase.PHOTOS)
        callback = _make_callback(data=f"{CB_KATEGORI_SELECT}Body & Rangka")

        await handle_kategori_select(callback, AsyncMock(), active_session=session)

        callback.answer.assert_awaited_once_with("Sesi tidak aktif.")


# ---------------------------------------------------------------------------
# Tests for handle_revision_answer — Skip
# ---------------------------------------------------------------------------


class TestRevisionSkip:
    async def test_skip_preserves_old_value(self):
        """Skip preserves old answer value (Requirement 7.4)."""
        answers = _make_full_answers()
        answers["kepala"] = "Rusak"  # old value
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Body & Rangka",
            current_question="kepala",
            current_category="Body & Rangka",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args[0][0]
        # Old value preserved
        assert saved.answers["kepala"] == "Rusak"
        # Pointer advanced to next field
        assert saved.current_question == "sayap_dalam"

    async def test_skip_on_last_field_completes_revision(self):
        """Skip on last field in category completes revision (Requirement 7.6)."""
        answers = _make_full_answers()
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Body & Rangka",
            current_question="crankcase_assy",  # last field in Body & Rangka
            current_category="Body & Rangka",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args[0][0]
        assert saved.phase == Phase.SUMMARY
        assert saved.mode == "ringkasan"
        assert saved.revisi_kategori is None
        assert "Body & Rangka" in saved.revision_history


# ---------------------------------------------------------------------------
# Tests for handle_revision_answer — New answer
# ---------------------------------------------------------------------------


class TestRevisionNewAnswer:
    async def test_valid_answer_overwrites(self):
        """Valid new answer overwrites old value (Requirement 7.5)."""
        answers = _make_full_answers()
        answers["kepala"] = "Baik"  # old value
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Body & Rangka",
            current_question="kepala",
            current_category="Body & Rangka",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Rusak", user_id=123)

        await handle_revision_answer(message, store)

        store.save_session.assert_awaited()
        saved = store.save_session.call_args[0][0]
        assert saved.answers["kepala"] == "Rusak"

    async def test_invalid_answer_redisplays_question(self):
        """Invalid answer re-displays the same question."""
        answers = _make_full_answers()
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Body & Rangka",
            current_question="kepala",
            current_category="Body & Rangka",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="InvalidOption", user_id=123)

        await handle_revision_answer(message, store)

        # Session NOT saved (invalid answer)
        store.save_session.assert_not_awaited()
        # Question re-displayed
        message.answer.assert_awaited()


# ---------------------------------------------------------------------------
# Tests for revision complete
# ---------------------------------------------------------------------------


class TestRevisionComplete:
    async def test_updates_revision_history(self):
        """Revision complete adds entry to revision_history (Requirement 7.6)."""
        answers = _make_full_answers()
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Mesin",
            current_question="bahan_bakar",  # last field in Mesin
            current_category="Mesin",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        saved = store.save_session.call_args[0][0]
        assert "Mesin" in saved.revision_history
        assert isinstance(saved.revision_history["Mesin"], datetime)

    async def test_sets_mode_ringkasan(self):
        """Revision complete sets mode=ringkasan (Requirement 7.6)."""
        answers = _make_full_answers()
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Mesin",
            current_question="bahan_bakar",
            current_category="Mesin",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        saved = store.save_session.call_args[0][0]
        assert saved.mode == "ringkasan"
        assert saved.phase == Phase.SUMMARY

    async def test_sends_reply_keyboard_remove(self):
        """Revision complete sends ReplyKeyboardRemove (Requirement 7.6)."""
        from aiogram.types import ReplyKeyboardRemove

        answers = _make_full_answers()
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Mesin",
            current_question="bahan_bakar",
            current_category="Mesin",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        # Find the call with ReplyKeyboardRemove
        found_remove = False
        for call in message.answer.call_args_list:
            kwargs = call.kwargs
            if isinstance(kwargs.get("reply_markup"), ReplyKeyboardRemove):
                found_remove = True
                break
        assert found_remove, "ReplyKeyboardRemove not sent after revision complete"

    async def test_shows_summary_after_revision(self):
        """Revision complete shows summary page (Requirement 7.6)."""
        answers = _make_full_answers()
        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Mesin",
            current_question="bahan_bakar",
            current_category="Mesin",
            answers=answers,
        )
        store = _make_session_store(session=session)
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        # Summary page should be displayed (contains motor info and categories)
        all_texts = [call[0][0] for call in message.answer.call_args_list]
        combined = " ".join(all_texts)
        assert "Ringkasan" in combined or "Honda" in combined


# ---------------------------------------------------------------------------
# Tests for STNK prune on category 8 revision (Requirement 7.7)
# ---------------------------------------------------------------------------


class TestStnkPruneOnRevision:
    async def test_stnk_change_triggers_prune(self):
        """When category 8 revised and stnk changes, prune is applied (Requirement 7.7)."""
        answers = _make_full_answers()
        # Add conditional STNK answers that should be pruned
        answers["stnk_hilang_polisi"] = "Ya"
        answers["stnk_tilang"] = "Tidak"
        answers["stnk_mati_tanggal"] = "2024-01-01"
        # Old stnk answer was "Cukup"
        answers["stnk"] = "Cukup"

        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Dokumen (STNK)",
            current_question="stnk",  # last field in Dokumen
            current_category="Dokumen (STNK)",
            answers=answers,
            stnk_answer="Cukup",
        )
        store = _make_session_store(session=session)
        # Change stnk to "Baik" — should prune all conditional fields
        message = _make_message(text="Baik", user_id=123)

        await handle_revision_answer(message, store)

        saved = store.save_session.call_args[0][0]
        # Conditional STNK fields should be pruned
        assert "stnk_hilang_polisi" not in saved.answers
        assert "stnk_tilang" not in saved.answers
        assert "stnk_mati_tanggal" not in saved.answers
        # stnk_answer updated
        assert saved.stnk_answer == "Baik"

    async def test_stnk_no_change_no_prune(self):
        """When stnk answer doesn't change, no prune is applied."""
        answers = _make_full_answers()
        answers["stnk_hilang_polisi"] = "Ya"
        answers["stnk_tilang"] = "Tidak"
        answers["stnk_mati_tanggal"] = "2024-01-01"
        answers["stnk"] = "Cukup"

        session = _make_session(
            phase=Phase.REVISION,
            mode="revisi",
            revisi_kategori="Dokumen (STNK)",
            current_question="stnk",
            current_category="Dokumen (STNK)",
            answers=answers,
            stnk_answer="Cukup",
        )
        store = _make_session_store(session=session)
        # Skip — preserves old value "Cukup"
        message = _make_message(text="Skip", user_id=123)

        await handle_revision_answer(message, store)

        saved = store.save_session.call_args[0][0]
        # Conditional fields preserved
        assert saved.answers.get("stnk_hilang_polisi") == "Ya"
        assert saved.answers.get("stnk_tilang") == "Tidak"
        assert saved.stnk_answer == "Cukup"


# ---------------------------------------------------------------------------
# Tests for no photo revision (Requirement 7.8)
# ---------------------------------------------------------------------------


class TestNoPhotoRevision:
    def test_no_photo_revision_in_category_list(self):
        """Category selection keyboard does not include a photo revision option (Requirement 7.8)."""
        kb = _build_category_selection_keyboard()
        all_texts = [row[0].text for row in kb.inline_keyboard]
        # Should only have the 8 standard categories, no "Foto" category
        assert len(all_texts) == 8
        for text in all_texts:
            assert "foto" not in text.lower() or "STNK" in text
