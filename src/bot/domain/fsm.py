"""FSM transition logic for the Telegram Inspection Bot.

All functions are pure (no I/O, no side effects). Use ``session.model_copy(update={...})``
for immutable session updates.

State diagram:
    IDLE → SELECTED: tap motor
    SELECTED → CHECKLIST: tap Mulai Inspeksi (inspection_started=True)
    SELECTED → IDLE: /batal (only when inspection_started=False)
    CHECKLIST → CHECKLIST: answer non-stnk component
    CHECKLIST → STNK_CONDITIONAL: answer stnk in (Cukup, Rusak)
    CHECKLIST → PHOTOS: answer stnk=Baik and category 8 complete
    STNK_CONDITIONAL → PHOTOS: all conditional questions answered/skipped
    PHOTOS → SUMMARY: photo 10 confirmed
    SUMMARY → REVISION: tap Revisi Kategori → select category
    REVISION → SUMMARY: revision category complete
    SUMMARY → [*]: tap Kirim Hasil → submit success
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    Phase,
    Session,
)


# ---------------------------------------------------------------------------
# Keyboard type determination (Requirement 16)
# ---------------------------------------------------------------------------


def determine_keyboard_type(
    session: Session,
) -> Literal["reply", "inline", "remove"]:
    """Return the keyboard type appropriate for the current session state.

    Rules (Requirement 16):
    - CHECKLIST, STNK_CONDITIONAL phases → "reply"
    - mode == "revisi" (REVISION phase) → "reply"
    - IDLE, SELECTED, PHOTOS, SUMMARY phases → "inline"
    - REVISION phase with mode != "revisi" → "inline" (shouldn't normally occur)

    Note: "remove" is returned when transitioning away from a Reply Keyboard phase
    to a non-Reply Keyboard phase. Callers that perform such transitions should
    use this function on the *new* session and send ReplyKeyboardRemove when the
    result changes from "reply" to "inline".
    """
    phase = session.phase
    mode = session.mode

    if phase in (Phase.CHECKLIST, Phase.STNK_CONDITIONAL):
        return "reply"

    if phase == Phase.REVISION or mode == "revisi":
        return "reply"

    if phase in (Phase.IDLE, Phase.SELECTED, Phase.PHOTOS, Phase.SUMMARY):
        return "inline"

    # Fallback
    return "inline"


# ---------------------------------------------------------------------------
# FSM transition functions
# ---------------------------------------------------------------------------


def transition_to_checklist(session: Session) -> Session:
    """Transition from SELECTED → CHECKLIST.

    Sets:
    - phase = CHECKLIST
    - inspection_started = True
    - started_at = now (UTC)
    - current_category = first category
    - current_question = first field of first category
    """
    first_category = CATEGORIES[0]
    first_field = CATEGORY_FIELDS[first_category][0]
    return session.model_copy(
        update={
            "phase": Phase.CHECKLIST,
            "inspection_started": True,
            "started_at": datetime.now(tz=timezone.utc),
            "current_category": first_category,
            "current_question": first_field,
        }
    )


def transition_to_stnk_conditional(session: Session) -> Session:
    """Transition from CHECKLIST → STNK_CONDITIONAL.

    Sets:
    - phase = STNK_CONDITIONAL
    """
    return session.model_copy(
        update={
            "phase": Phase.STNK_CONDITIONAL,
        }
    )


def transition_to_photos(session: Session) -> Session:
    """Transition from CHECKLIST or STNK_CONDITIONAL → PHOTOS.

    Sets:
    - phase = PHOTOS
    - photo_index = 0
    """
    return session.model_copy(
        update={
            "phase": Phase.PHOTOS,
            "photo_index": 0,
        }
    )


def transition_to_summary(session: Session) -> Session:
    """Transition from PHOTOS (or REVISION) → SUMMARY.

    Sets:
    - phase = SUMMARY
    - mode = "ringkasan"
    """
    return session.model_copy(
        update={
            "phase": Phase.SUMMARY,
            "mode": "ringkasan",
        }
    )


def transition_to_revision(session: Session, category: str) -> Session:
    """Transition from SUMMARY → REVISION for a specific category.

    Sets:
    - phase = REVISION
    - mode = "revisi"
    - revisi_kategori = category
    - current_category = category
    - current_question = first field of that category

    Raises ``ValueError`` if ``category`` is not a valid category name.
    """
    if category not in CATEGORY_FIELDS:
        raise ValueError(
            f"Unknown category: {category!r}. "
            f"Valid categories: {list(CATEGORY_FIELDS.keys())}"
        )
    first_field = CATEGORY_FIELDS[category][0]
    return session.model_copy(
        update={
            "phase": Phase.REVISION,
            "mode": "revisi",
            "revisi_kategori": category,
            "current_category": category,
            "current_question": first_field,
        }
    )


def transition_to_idle(session: Session) -> Session:
    """Transition to IDLE — clears motor selection (used by /batal).

    Only valid when ``inspection_started == False``. Callers are responsible
    for checking this guard condition before calling this function.

    Sets:
    - phase = IDLE
    - motor_id = "" (cleared)
    - current_category = None
    - current_question = None
    - mode = "inspeksi"
    - revisi_kategori = None
    """
    return session.model_copy(
        update={
            "phase": Phase.IDLE,
            "current_category": None,
            "current_question": None,
            "mode": "inspeksi",
            "revisi_kategori": None,
        }
    )
