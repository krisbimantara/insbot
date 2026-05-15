"""Conditional STNK domain logic — pure functions, no I/O.

Implements the branching logic for STNK conditional questions (Requirements 5.1–5.8).
All functions are pure: deterministic, no side effects, no I/O.
"""

from __future__ import annotations

import re
from datetime import date

from .models import (
    ALL_STNK_CONDITIONAL_FIELDS,
    STNK_CONDITIONAL_BY_ANSWER,
    Question,
    Session,
)

# ---------------------------------------------------------------------------
# Human-readable labels for conditional STNK fields
# ---------------------------------------------------------------------------

STNK_FIELD_LABELS: dict[str, str] = {
    "stnk_hilang_polisi": "STNK Hilang / Ditahan Polisi",
    "stnk_tilang": "STNK Sedang Ditilang",
    "stnk_ta": "STNK Tidak Ada (Rusak Total)",
    "stnk_mati_tanggal": "Tanggal Mati STNK (YYYY-MM-DD)",
}

# Fields that use Ya/Tidak/Skip boolean keyboard
_BOOLEAN_STNK_FIELDS: frozenset[str] = frozenset(
    {"stnk_hilang_polisi", "stnk_tilang", "stnk_ta"}
)

# Regex for date validation
_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# stnk_relevant_fields
# ---------------------------------------------------------------------------


def stnk_relevant_fields(stnk_value: str | None) -> tuple[str, ...]:
    """Return the tuple of conditional STNK field names relevant for *stnk_value*.

    - ``"Baik"`` or ``None`` → ``()``
    - ``"Cukup"`` → ``("stnk_hilang_polisi", "stnk_tilang", "stnk_mati_tanggal")``
    - ``"Rusak"`` → ``("stnk_hilang_polisi", "stnk_tilang", "stnk_ta", "stnk_mati_tanggal")``

    Requirements: 5.1, 5.2, 5.3
    """
    if stnk_value is None:
        return ()
    return STNK_CONDITIONAL_BY_ANSWER.get(stnk_value, ())


# ---------------------------------------------------------------------------
# prune_irrelevant_stnk
# ---------------------------------------------------------------------------


def prune_irrelevant_stnk(
    answers: dict[str, str | None],
    stnk_value: str | None,
) -> dict[str, str | None]:
    """Return a copy of *answers* with irrelevant conditional STNK keys removed.

    Keys that belong to ``ALL_STNK_CONDITIONAL_FIELDS`` but are NOT in
    ``stnk_relevant_fields(stnk_value)`` are dropped.  All other keys are
    preserved unchanged.

    Requirements: 5.6, 5.7
    """
    relevant: frozenset[str] = frozenset(stnk_relevant_fields(stnk_value))
    return {
        k: v
        for k, v in answers.items()
        if k not in ALL_STNK_CONDITIONAL_FIELDS or k in relevant
    }


# ---------------------------------------------------------------------------
# validate_stnk_date
# ---------------------------------------------------------------------------


def validate_stnk_date(value: str) -> bool:
    """Return ``True`` if *value* matches ``^\\d{4}-\\d{2}-\\d{2}$`` AND is a
    valid calendar date; ``False`` otherwise.

    Requirements: 5.5
    """
    if not _DATE_REGEX.match(value):
        return False
    try:
        year, month, day = value.split("-")
        date(int(year), int(month), int(day))
        return True
    except (ValueError, OverflowError):
        return False


# ---------------------------------------------------------------------------
# next_stnk_question
# ---------------------------------------------------------------------------


def next_stnk_question(session: Session) -> Question | None:
    """Return the next unanswered conditional STNK question, or ``None`` when done.

    Iterates over ``stnk_relevant_fields(session.stnk_answer)`` in order and
    returns the first field that has not yet been recorded in
    ``session.answers`` (i.e. the key is absent — a key present with value
    ``None`` means the inspector pressed Skip and the question is considered
    answered).

    - Boolean fields (``stnk_hilang_polisi``, ``stnk_tilang``, ``stnk_ta``):
      ``keyboard_kind="reply"``, ``options=("Ya", "Tidak", "Skip")``,
      ``skippable=True``.
    - Date field (``stnk_mati_tanggal``):
      ``keyboard_kind="free_text_with_skip"``, ``options=("Skip",)``,
      ``skippable=True``.

    Requirements: 5.2, 5.3, 5.4, 5.5
    """
    relevant = stnk_relevant_fields(session.stnk_answer)
    for field in relevant:
        if field not in session.answers:
            return _build_stnk_question(field)
    return None


def _build_stnk_question(field: str) -> Question:
    """Construct a ``Question`` dataclass for the given conditional STNK field."""
    label = STNK_FIELD_LABELS.get(field, field)
    if field in _BOOLEAN_STNK_FIELDS:
        return Question(
            field=field,
            label=label,
            options=("Ya", "Tidak", "Skip"),
            keyboard_kind="reply",
            skippable=True,
        )
    # stnk_mati_tanggal — free text with a single Skip button
    return Question(
        field=field,
        label=label,
        options=("Skip",),
        keyboard_kind="free_text_with_skip",
        skippable=True,
    )


# ---------------------------------------------------------------------------
# apply_stnk_answer
# ---------------------------------------------------------------------------


def apply_stnk_answer(
    session: Session,
    field: str,
    value: str | None,
) -> Session:
    """Pure function: record *value* for *field* in the session and return a new Session.

    - ``value=None`` represents a Skip (field is stored as ``None`` so that
      ``next_stnk_question`` knows the question has been answered/skipped).
    - Returns a new ``Session`` via ``model_copy(update=...)``.

    Requirements: 5.4, 5.5, 5.8
    """
    new_answers = dict(session.answers)
    new_answers[field] = value
    return session.model_copy(update={"answers": new_answers})
