"""Checklist domain logic — pure functions for question sequencing and answer application.

All functions are pure (no I/O, no side effects). Use ``session.model_copy(update={...})``
for immutable session updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.domain.models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    COMPONENT_OPTIONS,
    MANDATORY_FIELDS,
    ALL_STNK_CONDITIONAL_FIELDS,
    STNK_CONDITIONAL_BY_ANSWER,
    CategoryProgress,
    Phase,
    Question,
    Session,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Done sentinel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Done:
    """Sentinel returned by ``next_question`` when there are no more questions."""


# ---------------------------------------------------------------------------
# Human-readable field labels
# ---------------------------------------------------------------------------

def _snake_to_title(field: str) -> str:
    """Convert snake_case field name to Title Case label."""
    return field.replace("_", " ").title()


FIELD_LABELS: dict[str, str] = {
    # Body & Rangka
    "kepala": "Kepala",
    "sayap_dalam": "Sayap Dalam",
    "sayap_luar": "Sayap Luar",
    "rangka_tengah": "Rangka Tengah",
    "body_belakang": "Body Belakang",
    "spakboard_depan": "Spakboard Depan",
    "spakboard_belakang": "Spakboard Belakang",
    "leher_angsa": "Leher Angsa",
    "list_grafis": "List Grafis (1 Set)",
    # Mesin
    "crankcase_assy": "Crankcase Assy",
    "head_cylinder": "Head Cylinder",
    "cylinder": "Cylinder",
    "carburator_assy": "Carburator Assy",
    "oil_pump_assy": "Oil Pump Assy",
    "cover_crankcase_1": "Cover Crankcase 1",
    "cover_crankcase_2": "Cover Crankcase 2",
    "rantai_kamrat": "Rantai Kamrat",
    "crankshaft_assy": "Crankshaft Assy",
    "gear_rantai_vbelt": "Gear dan Rantai (1 Set) / V-Belt",
    "muffler_knalpot": "Muffler / Knalpot",
    "fuel_tank": "Fuel Tank",
    "bahan_bakar": "Bahan Bakar",
    # Kelistrikan
    "accu": "Accu",
    "cdi": "CDI",
    "kiprok": "Kiprok",
    "main_switch_steering_lock": "Main Switch / Steering Lock",
    "ignition_coil": "Ignition Coil",
    "dinamo_stater": "Dinamo Stater",
    "rotor_magnet": "Rotor Magnet",
    "stator_kumparan": "Stator / Kumparan",
    "klakson": "Klakson",
    "speedometer": "Speedometer",
    # Lampu & Sein
    "lampu_depan": "Lampu Depan",
    "lampu_belakang": "Lampu Belakang",
    "sein_depan": "Sein Depan (Sepasang)",
    "sein_belakang": "Sein Belakang (Sepasang)",
    # Kaki-kaki & Rem
    "shock_belakang": "Shock Belakang (Pair)",
    "inner_tube_depan": "Inner Tube Comp Dpn (Pair)",
    "master_cakram": "Master Cakram (1 Set)",
    "plate_brake_shoe": "Plate Brake Shoe",
    "piringan_rem_depan": "Piringan Rem Depan",
    "master_cylinder_rem": "Master Cylinder Rem",
    "kampas_cakram": "Kampas Cakram",
    "kampas_tromol": "Kampas Tromol",
    "tires_depan": "Karet (Tires) Depan",
    "tires_belakang": "Karet (Tires) Belakang",
    "velg_cw_depan": "Velg CW Depan",
    "velg_cw_belakang": "Velg CW Belakang",
    "velg_jarjari_depan": "Velg Jari-jari Depan",
    "velg_jarjari_belakang": "Velg Jari-jari Belakang",
    # Aksesori & Kelengkapan
    "kaca_spion": "Kaca Spion",
    "tool_kit": "Tool Kit",
    "tool_box": "Tool Box",
    "tutup_rantai_vbelt": "Tutup Rantai / V-Belt",
    "panel_instrumen_kanan": "Panel Instrumen Kanan",
    "panel_instrumen_kiri": "Panel Instrumen Kiri",
    "jok_tempat_duduk": "Jok / Tempat Duduk",
    "behel_belakang": "Behel Belakang",
    "segitiga_atas": "Segitiga Atas",
    "segitiga_bawah": "Segitiga Bawah",
    "foot_step_depan": "Foot Step Depan",
    "foot_step_belakang": "Foot Step Belakang",
    # Kick & Pedal
    "kick_starter": "Kick Starter",
    "pedal_gigi": "Pedal Gigi",
    "pedal_rem": "Pedal Rem",
    # Dokumen (STNK)
    "stnk": "STNK",
    # Conditional STNK fields
    "stnk_hilang_polisi": "STNK Hilang / Polisi",
    "stnk_tilang": "STNK Tilang",
    "stnk_ta": "STNK TA",
    "stnk_mati_tanggal": "STNK Mati Tanggal",
}

# Fill any missing labels with auto-generated ones
for _field in MANDATORY_FIELDS:
    if _field not in FIELD_LABELS:
        FIELD_LABELS[_field] = _snake_to_title(_field)

for _field in ALL_STNK_CONDITIONAL_FIELDS:
    if _field not in FIELD_LABELS:
        FIELD_LABELS[_field] = _snake_to_title(_field)


# ---------------------------------------------------------------------------
# Category-for-field reverse mapping
# ---------------------------------------------------------------------------

CATEGORY_FOR_FIELD: dict[str, str] = {
    field: category
    for category, fields in CATEGORY_FIELDS.items()
    for field in fields
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_question(field: str) -> Question:
    """Build a ``Question`` dataclass for a mandatory checklist field."""
    options = COMPONENT_OPTIONS[field]
    return Question(
        field=field,
        label=FIELD_LABELS.get(field, _snake_to_title(field)),
        options=options,
        keyboard_kind="reply",
        skippable=False,
    )


def _make_stnk_conditional_question(field: str) -> Question:
    """Build a ``Question`` for a conditional STNK field."""
    if field == "stnk_mati_tanggal":
        return Question(
            field=field,
            label=FIELD_LABELS.get(field, _snake_to_title(field)),
            options=("Skip",),
            keyboard_kind="free_text_with_skip",
            skippable=True,
        )
    # Boolean fields: Ya / Tidak / Skip
    return Question(
        field=field,
        label=FIELD_LABELS.get(field, _snake_to_title(field)),
        options=("Ya", "Tidak", "Skip"),
        keyboard_kind="reply",
        skippable=True,
    )


def _compute_progress(answers: dict[str, str | None]) -> dict[str, CategoryProgress]:
    """Compute per-category progress from the current answers dict."""
    progress: dict[str, CategoryProgress] = {}
    for category in CATEGORIES:
        fields = CATEGORY_FIELDS[category]
        done = sum(1 for f in fields if answers.get(f) is not None)
        progress[category] = CategoryProgress(name=category, done=done, total=len(fields))
    return progress


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def next_question(session: Session) -> Question | Done:
    """Return the next unanswered question for the current session phase.

    - CHECKLIST phase: iterate through CATEGORIES / CATEGORY_FIELDS in order.
    - STNK_CONDITIONAL phase: delegate to STNK conditional logic.
    - PHOTOS phase: return Done (photos are handled separately by the photo handler).
    - Any other phase: return Done.

    This function is pure — it reads session state and returns a value with no side effects.
    """
    if session.phase == Phase.CHECKLIST:
        return _next_checklist_question(session)
    if session.phase == Phase.STNK_CONDITIONAL:
        return _next_stnk_conditional_question(session)
    # PHOTOS, SUMMARY, REVISION, IDLE, SELECTED — no checklist question to return
    return Done()


def _next_checklist_question(session: Session) -> Question | Done:
    """Find the next unanswered mandatory field in CHECKLIST phase."""
    # Walk through categories in fixed order
    for category in CATEGORIES:
        fields = CATEGORY_FIELDS[category]
        for field in fields:
            if session.answers.get(field) is None:
                return _make_question(field)
    # All 66 mandatory fields answered
    return Done()


def _next_stnk_conditional_question(session: Session) -> Question | Done:
    """Find the next unanswered conditional STNK field."""
    stnk_val = session.stnk_answer
    if stnk_val is None or stnk_val == "Baik":
        return Done()

    relevant = STNK_CONDITIONAL_BY_ANSWER.get(stnk_val, ())
    for field in relevant:
        # A field is "unanswered" if it is not present in answers at all.
        # (None means explicitly skipped, which counts as answered.)
        if field not in session.answers:
            return _make_stnk_conditional_question(field)
    return Done()


def apply_answer(session: Session, field: str, value: str) -> Session:
    """Write an answer, recalculate progress, and advance the question pointer.

    Rules:
    1. Validates that ``value`` is in ``COMPONENT_OPTIONS[field]`` for mandatory fields.
       For conditional STNK fields, accepts "Ya", "Tidak", "Skip", or a date string.
    2. Writes ``answers[field] = value`` (or ``None`` for "Skip").
    3. Updates ``current_question`` to the next field in the category.
    4. If the category is complete, adds it to ``completed_categories`` and advances
       ``current_category``.
    5. Recalculates progress.
    6. Returns a new Session via ``model_copy`` (immutable update).

    Raises ``ValueError`` if ``value`` is not valid for the given field.
    """
    # --- Validate value ---
    if field in COMPONENT_OPTIONS:
        valid_options = COMPONENT_OPTIONS[field]
        if value not in valid_options:
            raise ValueError(
                f"Invalid value {value!r} for field {field!r}. "
                f"Valid options: {valid_options}"
            )
        stored_value: str | None = value
    elif field in ALL_STNK_CONDITIONAL_FIELDS:
        # Conditional STNK: "Ya", "Tidak", "Skip", or date string
        if value == "Skip":
            stored_value = None
        else:
            stored_value = value
    else:
        raise ValueError(f"Unknown field: {field!r}")

    # --- Write answer ---
    new_answers = dict(session.answers)
    new_answers[field] = stored_value

    # --- Recalculate progress ---
    new_progress = _compute_progress(new_answers)

    # --- Advance pointer for mandatory fields ---
    new_current_category = session.current_category
    new_current_question = session.current_question
    new_completed_categories = list(session.completed_categories)

    if field in CATEGORY_FOR_FIELD:
        category = CATEGORY_FOR_FIELD[field]
        fields_in_category = CATEGORY_FIELDS[category]
        field_idx = list(fields_in_category).index(field)
        next_idx = field_idx + 1

        if next_idx < len(fields_in_category):
            # More fields in this category
            new_current_question = fields_in_category[next_idx]
            new_current_category = category
        else:
            # Category complete
            if category not in new_completed_categories:
                new_completed_categories.append(category)

            # Find next category
            cat_idx = list(CATEGORIES).index(category)
            next_cat_idx = cat_idx + 1
            if next_cat_idx < len(CATEGORIES):
                next_category = CATEGORIES[next_cat_idx]
                new_current_category = next_category
                new_current_question = CATEGORY_FIELDS[next_category][0]
            else:
                # All categories done
                new_current_category = None
                new_current_question = None

    elif field in ALL_STNK_CONDITIONAL_FIELDS:
        # Advance within conditional STNK fields
        stnk_val = session.stnk_answer
        if stnk_val and stnk_val != "Baik":
            relevant = list(STNK_CONDITIONAL_BY_ANSWER.get(stnk_val, ()))
            if field in relevant:
                field_idx = relevant.index(field)
                next_idx = field_idx + 1
                if next_idx < len(relevant):
                    new_current_question = relevant[next_idx]
                else:
                    new_current_question = None

    # --- Build updated session ---
    return session.model_copy(
        update={
            "answers": new_answers,
            "progress": new_progress,
            "current_category": new_current_category,
            "current_question": new_current_question,
            "completed_categories": new_completed_categories,
        }
    )
