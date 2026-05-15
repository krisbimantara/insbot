"""Unit tests for src/bot/domain/stnk.py (task 2.4).

Tests cover:
- stnk_relevant_fields
- prune_irrelevant_stnk
- validate_stnk_date
- next_stnk_question
- apply_stnk_answer
"""

from __future__ import annotations

import pytest

from src.bot.domain.models import (
    ALL_STNK_CONDITIONAL_FIELDS,
    MotorMeta,
    Phase,
    Session,
)
from src.bot.domain.stnk import (
    STNK_FIELD_LABELS,
    apply_stnk_answer,
    next_stnk_question,
    prune_irrelevant_stnk,
    stnk_relevant_fields,
    validate_stnk_date,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOTOR_META = MotorMeta(
    name="PJ-001",
    nopol="B1234XY",
    merk="Honda",
    model="Beat",
    tahun="2020",
    warna="Merah",
)


def _make_session(stnk_answer=None, answers=None) -> Session:
    return Session(
        telegram_id="123",
        motor_id="PJ-001",
        tipe_inspeksi="Inspeksi",
        phase=Phase.STNK_CONDITIONAL,
        stnk_answer=stnk_answer,
        answers=answers or {},
        motor_meta=_MOTOR_META,
    )


# ---------------------------------------------------------------------------
# stnk_relevant_fields
# ---------------------------------------------------------------------------


class TestStnkRelevantFields:
    def test_none_returns_empty(self):
        assert stnk_relevant_fields(None) == ()

    def test_baik_returns_empty(self):
        assert stnk_relevant_fields("Baik") == ()

    def test_cukup_returns_three_fields(self):
        result = stnk_relevant_fields("Cukup")
        assert result == ("stnk_hilang_polisi", "stnk_tilang", "stnk_mati_tanggal")

    def test_rusak_returns_four_fields(self):
        result = stnk_relevant_fields("Rusak")
        assert result == (
            "stnk_hilang_polisi",
            "stnk_tilang",
            "stnk_ta",
            "stnk_mati_tanggal",
        )

    def test_unknown_value_returns_empty(self):
        # Defensive: unknown stnk_value should not crash
        assert stnk_relevant_fields("Unknown") == ()

    def test_order_is_deterministic(self):
        # Calling twice must return the same tuple
        assert stnk_relevant_fields("Rusak") == stnk_relevant_fields("Rusak")


# ---------------------------------------------------------------------------
# prune_irrelevant_stnk
# ---------------------------------------------------------------------------


class TestPruneIrrelevantStnk:
    def test_baik_removes_all_conditional_fields(self):
        answers = {
            "stnk": "Baik",
            "stnk_hilang_polisi": "Ya",
            "stnk_tilang": "Tidak",
            "stnk_ta": "Ya",
            "stnk_mati_tanggal": "2024-01-01",
            "kepala": "Baik",
        }
        result = prune_irrelevant_stnk(answers, "Baik")
        assert "stnk_hilang_polisi" not in result
        assert "stnk_tilang" not in result
        assert "stnk_ta" not in result
        assert "stnk_mati_tanggal" not in result
        # Non-conditional keys preserved
        assert result["stnk"] == "Baik"
        assert result["kepala"] == "Baik"

    def test_none_removes_all_conditional_fields(self):
        answers = {
            "stnk_hilang_polisi": "Ya",
            "stnk_ta": "Tidak",
            "kepala": "Cukup",
        }
        result = prune_irrelevant_stnk(answers, None)
        assert "stnk_hilang_polisi" not in result
        assert "stnk_ta" not in result
        assert result["kepala"] == "Cukup"

    def test_cukup_keeps_relevant_removes_stnk_ta(self):
        answers = {
            "stnk_hilang_polisi": "Ya",
            "stnk_tilang": "Tidak",
            "stnk_ta": "Ya",          # not relevant for Cukup
            "stnk_mati_tanggal": "2024-06-01",
            "kepala": "Baik",
        }
        result = prune_irrelevant_stnk(answers, "Cukup")
        assert result["stnk_hilang_polisi"] == "Ya"
        assert result["stnk_tilang"] == "Tidak"
        assert result["stnk_mati_tanggal"] == "2024-06-01"
        assert "stnk_ta" not in result
        assert result["kepala"] == "Baik"

    def test_rusak_keeps_all_four_conditional_fields(self):
        answers = {
            "stnk_hilang_polisi": "Ya",
            "stnk_tilang": "Tidak",
            "stnk_ta": "Ya",
            "stnk_mati_tanggal": "2024-06-01",
            "kepala": "Rusak",
        }
        result = prune_irrelevant_stnk(answers, "Rusak")
        assert result["stnk_hilang_polisi"] == "Ya"
        assert result["stnk_tilang"] == "Tidak"
        assert result["stnk_ta"] == "Ya"
        assert result["stnk_mati_tanggal"] == "2024-06-01"
        assert result["kepala"] == "Rusak"

    def test_returns_copy_not_mutating_original(self):
        answers = {"stnk_hilang_polisi": "Ya", "kepala": "Baik"}
        result = prune_irrelevant_stnk(answers, "Baik")
        # Original unchanged
        assert "stnk_hilang_polisi" in answers
        assert "stnk_hilang_polisi" not in result

    def test_empty_answers_returns_empty(self):
        assert prune_irrelevant_stnk({}, "Rusak") == {}

    def test_none_values_preserved_for_relevant_fields(self):
        answers = {"stnk_hilang_polisi": None, "stnk_tilang": None}
        result = prune_irrelevant_stnk(answers, "Cukup")
        assert result["stnk_hilang_polisi"] is None
        assert result["stnk_tilang"] is None


# ---------------------------------------------------------------------------
# validate_stnk_date
# ---------------------------------------------------------------------------


class TestValidateStnkDate:
    @pytest.mark.parametrize(
        "value",
        [
            "2024-01-01",
            "2000-12-31",
            "1999-06-15",
            "2025-02-28",
        ],
    )
    def test_valid_dates(self, value):
        assert validate_stnk_date(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "2024-13-01",   # month 13
            "2024-00-01",   # month 0
            "2024-01-32",   # day 32
            "2024-02-30",   # Feb 30 doesn't exist
            "2023-02-29",   # 2023 is not a leap year
            "01-01-2024",   # wrong order
            "2024/01/01",   # wrong separator
            "20240101",     # no separators
            "2024-1-1",     # no zero-padding
            "",             # empty
            "abcd-ef-gh",   # non-numeric
            "2024-01",      # incomplete
        ],
    )
    def test_invalid_dates(self, value):
        assert validate_stnk_date(value) is False

    def test_leap_year_feb_29_valid(self):
        assert validate_stnk_date("2024-02-29") is True

    def test_non_leap_year_feb_29_invalid(self):
        assert validate_stnk_date("2023-02-29") is False


# ---------------------------------------------------------------------------
# next_stnk_question
# ---------------------------------------------------------------------------


class TestNextStnkQuestion:
    def test_baik_returns_none_immediately(self):
        session = _make_session(stnk_answer="Baik")
        assert next_stnk_question(session) is None

    def test_none_stnk_returns_none(self):
        session = _make_session(stnk_answer=None)
        assert next_stnk_question(session) is None

    def test_cukup_first_question_is_hilang_polisi(self):
        session = _make_session(stnk_answer="Cukup")
        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_hilang_polisi"
        assert q.keyboard_kind == "reply"
        assert "Ya" in q.options
        assert "Tidak" in q.options
        assert "Skip" in q.options
        assert q.skippable is True

    def test_cukup_second_question_after_first_answered(self):
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya"},
        )
        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_tilang"

    def test_cukup_third_question_is_mati_tanggal(self):
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_mati_tanggal"
        assert q.keyboard_kind == "free_text_with_skip"
        assert q.options == ("Skip",)

    def test_cukup_all_answered_returns_none(self):
        session = _make_session(
            stnk_answer="Cukup",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
                "stnk_mati_tanggal": "2024-01-01",
            },
        )
        assert next_stnk_question(session) is None

    def test_rusak_third_question_is_stnk_ta(self):
        session = _make_session(
            stnk_answer="Rusak",
            answers={"stnk_hilang_polisi": "Ya", "stnk_tilang": "Tidak"},
        )
        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_ta"
        assert q.keyboard_kind == "reply"

    def test_rusak_fourth_question_is_mati_tanggal(self):
        session = _make_session(
            stnk_answer="Rusak",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
                "stnk_ta": None,  # skipped
            },
        )
        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_mati_tanggal"

    def test_rusak_all_answered_returns_none(self):
        session = _make_session(
            stnk_answer="Rusak",
            answers={
                "stnk_hilang_polisi": "Ya",
                "stnk_tilang": "Tidak",
                "stnk_ta": None,
                "stnk_mati_tanggal": "2024-06-01",
            },
        )
        assert next_stnk_question(session) is None

    def test_skipped_field_none_counts_as_answered(self):
        # A field with value None (Skip) should NOT be returned again
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": None},  # skipped
        )
        q = next_stnk_question(session)
        assert q is not None
        assert q.field == "stnk_tilang"  # moves on


# ---------------------------------------------------------------------------
# apply_stnk_answer
# ---------------------------------------------------------------------------


class TestApplyStnkAnswer:
    def test_records_value_in_answers(self):
        session = _make_session(stnk_answer="Cukup")
        new_session = apply_stnk_answer(session, "stnk_hilang_polisi", "Ya")
        assert new_session.answers["stnk_hilang_polisi"] == "Ya"

    def test_records_none_for_skip(self):
        session = _make_session(stnk_answer="Cukup")
        new_session = apply_stnk_answer(session, "stnk_tilang", None)
        assert new_session.answers["stnk_tilang"] is None

    def test_does_not_mutate_original_session(self):
        session = _make_session(stnk_answer="Cukup")
        apply_stnk_answer(session, "stnk_hilang_polisi", "Ya")
        assert "stnk_hilang_polisi" not in session.answers

    def test_preserves_existing_answers(self):
        session = _make_session(
            stnk_answer="Rusak",
            answers={"stnk_hilang_polisi": "Ya"},
        )
        new_session = apply_stnk_answer(session, "stnk_tilang", "Tidak")
        assert new_session.answers["stnk_hilang_polisi"] == "Ya"
        assert new_session.answers["stnk_tilang"] == "Tidak"

    def test_overwrite_existing_value(self):
        session = _make_session(
            stnk_answer="Cukup",
            answers={"stnk_hilang_polisi": "Ya"},
        )
        new_session = apply_stnk_answer(session, "stnk_hilang_polisi", "Tidak")
        assert new_session.answers["stnk_hilang_polisi"] == "Tidak"

    def test_returns_session_instance(self):
        session = _make_session(stnk_answer="Cukup")
        new_session = apply_stnk_answer(session, "stnk_hilang_polisi", "Ya")
        assert isinstance(new_session, Session)

    def test_full_cukup_flow(self):
        """Simulate answering all three Cukup questions in sequence."""
        session = _make_session(stnk_answer="Cukup")

        session = apply_stnk_answer(session, "stnk_hilang_polisi", "Ya")
        assert next_stnk_question(session).field == "stnk_tilang"

        session = apply_stnk_answer(session, "stnk_tilang", None)  # Skip
        assert next_stnk_question(session).field == "stnk_mati_tanggal"

        session = apply_stnk_answer(session, "stnk_mati_tanggal", "2024-12-31")
        assert next_stnk_question(session) is None


# ---------------------------------------------------------------------------
# STNK_FIELD_LABELS
# ---------------------------------------------------------------------------


class TestStnkFieldLabels:
    def test_all_conditional_fields_have_labels(self):
        for field in ALL_STNK_CONDITIONAL_FIELDS:
            assert field in STNK_FIELD_LABELS, f"Missing label for {field}"

    def test_labels_are_non_empty_strings(self):
        for field, label in STNK_FIELD_LABELS.items():
            assert isinstance(label, str) and label.strip(), (
                f"Label for {field!r} is empty"
            )
