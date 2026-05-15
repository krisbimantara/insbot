"""Core data models and constants for the Telegram Inspection Bot domain layer.

All constants and models are pure (no I/O). This module is the single source of
truth for field names, option sets, and session structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Phase FSM enum
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    """Finite-state machine phases for an inspection session."""

    IDLE = "idle"                          # no motor selected
    SELECTED = "selected"                  # motor selected, "Mulai Inspeksi" not yet tapped
    CHECKLIST = "checklist"                # answering component questions
    STNK_CONDITIONAL = "stnk_conditional"  # answering conditional STNK questions
    PHOTOS = "photos"                      # capturing 10 mandatory photos
    SUMMARY = "summary"                    # reviewing answers before submit
    REVISION = "revision"                  # revising a specific category


# ---------------------------------------------------------------------------
# Categories (Requirement 4.1)
# ---------------------------------------------------------------------------

CATEGORIES: tuple[str, ...] = (
    "Body & Rangka",
    "Mesin",
    "Kelistrikan",
    "Lampu & Sein",
    "Kaki-kaki & Rem",
    "Aksesori & Kelengkapan",
    "Kick & Pedal",
    "Dokumen (STNK)",
)

# ---------------------------------------------------------------------------
# Mandatory fields — 66 fields in fixed order (Requirement 14.1)
# Grouped by category for readability; the flat tuple preserves that order.
# ---------------------------------------------------------------------------

# Body & Rangka (9 fields)
_BODY_RANGKA: tuple[str, ...] = (
    "kepala",
    "sayap_dalam",
    "sayap_luar",
    "rangka_tengah",
    "body_belakang",
    "spakboard_depan",
    "spakboard_belakang",
    "leher_angsa",
    "list_grafis",
)

# Mesin (13 fields)
_MESIN: tuple[str, ...] = (
    "crankcase_assy",
    "head_cylinder",
    "cylinder",
    "carburator_assy",
    "oil_pump_assy",
    "cover_crankcase_1",
    "cover_crankcase_2",
    "rantai_kamrat",
    "crankshaft_assy",
    "gear_rantai_vbelt",
    "muffler_knalpot",
    "fuel_tank",
    "bahan_bakar",
)

# Kelistrikan (10 fields)
_KELISTRIKAN: tuple[str, ...] = (
    "accu",
    "cdi",
    "kiprok",
    "main_switch_steering_lock",
    "ignition_coil",
    "dinamo_stater",
    "rotor_magnet",
    "stator_kumparan",
    "klakson",
    "speedometer",
)

# Lampu & Sein (4 fields)
_LAMPU_SEIN: tuple[str, ...] = (
    "lampu_depan",
    "lampu_belakang",
    "sein_depan",
    "sein_belakang",
)

# Kaki-kaki & Rem (14 fields)
_KAKI_REM: tuple[str, ...] = (
    "shock_belakang",
    "inner_tube_depan",
    "master_cakram",
    "plate_brake_shoe",
    "piringan_rem_depan",
    "master_cylinder_rem",
    "kampas_cakram",
    "kampas_tromol",
    "tires_depan",
    "tires_belakang",
    "velg_cw_depan",
    "velg_cw_belakang",
    "velg_jarjari_depan",
    "velg_jarjari_belakang",
)

# Aksesori & Kelengkapan (12 fields)
_AKSESORI: tuple[str, ...] = (
    "kaca_spion",
    "tool_kit",
    "tool_box",
    "tutup_rantai_vbelt",
    "panel_instrumen_kanan",
    "panel_instrumen_kiri",
    "jok_tempat_duduk",
    "behel_belakang",
    "segitiga_atas",
    "segitiga_bawah",
    "foot_step_depan",
    "foot_step_belakang",
)

# Kick & Pedal (3 fields)
_KICK_PEDAL: tuple[str, ...] = (
    "kick_starter",
    "pedal_gigi",
    "pedal_rem",
)

# Dokumen (STNK) (1 field)
_DOKUMEN: tuple[str, ...] = (
    "stnk",
)

MANDATORY_FIELDS: tuple[str, ...] = (
    *_BODY_RANGKA,
    *_MESIN,
    *_KELISTRIKAN,
    *_LAMPU_SEIN,
    *_KAKI_REM,
    *_AKSESORI,
    *_KICK_PEDAL,
    *_DOKUMEN,
)

# Sanity-check: must be exactly 66 fields
assert len(MANDATORY_FIELDS) == 66, (
    f"MANDATORY_FIELDS must have 66 entries, got {len(MANDATORY_FIELDS)}"
)
assert len(set(MANDATORY_FIELDS)) == 66, "MANDATORY_FIELDS contains duplicate field names"

# ---------------------------------------------------------------------------
# Mapping: category name → fields belonging to that category
# ---------------------------------------------------------------------------

CATEGORY_FIELDS: dict[str, tuple[str, ...]] = {
    "Body & Rangka": _BODY_RANGKA,
    "Mesin": _MESIN,
    "Kelistrikan": _KELISTRIKAN,
    "Lampu & Sein": _LAMPU_SEIN,
    "Kaki-kaki & Rem": _KAKI_REM,
    "Aksesori & Kelengkapan": _AKSESORI,
    "Kick & Pedal": _KICK_PEDAL,
    "Dokumen (STNK)": _DOKUMEN,
}

# ---------------------------------------------------------------------------
# Component answer options (Requirement 14.2)
# ---------------------------------------------------------------------------

_DEFAULT_OPTIONS: tuple[str, ...] = ("Baik", "Cukup", "Rusak")
_BAHAN_BAKAR_OPTIONS: tuple[str, ...] = ("E", "1/4", "1/2", "3/4", "F")

COMPONENT_OPTIONS: dict[str, tuple[str, ...]] = {
    field: _DEFAULT_OPTIONS for field in MANDATORY_FIELDS
}
COMPONENT_OPTIONS["bahan_bakar"] = _BAHAN_BAKAR_OPTIONS

# ---------------------------------------------------------------------------
# Photo fields — 10 photos in fixed order (Requirement 6.2 / 14.4)
# ---------------------------------------------------------------------------

PHOTO_FIELDS: tuple[str, ...] = (
    "foto_tampak_depan",
    "foto_tampak_belakang",
    "foto_tampak_kanan",
    "foto_tampak_kiri",
    "foto_mesin",
    "foto_nomor_rangka",
    "foto_nomor_mesin",
    "foto_stnk",
    "foto_ban_depan",
    "foto_ban_belakang",
)

assert len(PHOTO_FIELDS) == 10, f"PHOTO_FIELDS must have 10 entries, got {len(PHOTO_FIELDS)}"

# ---------------------------------------------------------------------------
# Conditional STNK fields by answer (Requirement 5.2, 5.3)
# ---------------------------------------------------------------------------

STNK_CONDITIONAL_BY_ANSWER: dict[str, tuple[str, ...]] = {
    "Baik": (),
    "Cukup": (
        "stnk_hilang_polisi",
        "stnk_tilang",
        "stnk_mati_tanggal",
    ),
    "Rusak": (
        "stnk_hilang_polisi",
        "stnk_tilang",
        "stnk_ta",
        "stnk_mati_tanggal",
    ),
}

# Flat set of all possible conditional STNK field names (for pruning logic)
ALL_STNK_CONDITIONAL_FIELDS: frozenset[str] = frozenset(
    f
    for fields in STNK_CONDITIONAL_BY_ANSWER.values()
    for f in fields
)

# ---------------------------------------------------------------------------
# Pydantic models — Frappe API response types
# ---------------------------------------------------------------------------


class MotorTarikan(BaseModel):
    """Represents a Motor Tarikan document returned by Frappe's get_pending_list."""

    name: str  # e.g. "PJ-001"
    nopol: str
    merk: str
    model: str
    tahun: str
    warna: str
    status_inspeksi: Literal["Proses Inspeksi", "Proses Inspeksi Ulang"]


class MotorMeta(BaseModel):
    """Snapshot of motor data stored in the session to avoid repeated Frappe fetches."""

    name: str
    nopol: str
    merk: str
    model: str
    tahun: str
    warna: str


# ---------------------------------------------------------------------------
# Domain dataclasses (frozen, hashable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryProgress:
    """Progress counters for a single inspection category."""

    name: str
    done: int
    total: int


@dataclass(frozen=True)
class Question:
    """Represents a single inspection question to be presented to the inspector."""

    field: str
    label: str
    options: tuple[str, ...]
    keyboard_kind: Literal["reply", "free_text_with_skip"]
    skippable: bool


@dataclass(frozen=True)
class ValidationError:
    """Describes a single pre-submit validation failure."""

    field: str
    reason: Literal["missing", "invalid_value", "missing_photo"]
    message: str


# ---------------------------------------------------------------------------
# Session model (stored as JSON in Redis)
# ---------------------------------------------------------------------------


class Session(BaseModel):
    """Full inspection session state, serialised as a single JSON document in Redis.

    Key: ``session:{telegram_id}:{motor_id}``
    TTL: 86400 s, refreshed on every save (Requirement 9.1).
    """

    schema_version: int = 1
    telegram_id: str
    motor_id: str
    tipe_inspeksi: Literal["Inspeksi", "Inspeksi Ulang"]
    inspection_started: bool = False
    started_at: datetime | None = None
    mode: Literal["inspeksi", "revisi", "ringkasan"] = "inspeksi"
    phase: Phase = Phase.SELECTED
    current_category: str | None = None
    current_question: str | None = None
    answers: dict[str, str | None] = Field(default_factory=dict)
    stnk_answer: Literal["Baik", "Cukup", "Rusak"] | None = None
    photo_index: int = 0
    photos: dict[str, str] = Field(default_factory=dict)
    completed_categories: list[str] = Field(default_factory=list)
    progress: dict[str, CategoryProgress] = Field(default_factory=dict)
    revision_history: dict[str, datetime] = Field(default_factory=dict)
    revisi_kategori: str | None = None
    motor_meta: MotorMeta


# ---------------------------------------------------------------------------
# Submit payload and result models
# ---------------------------------------------------------------------------


class SubmitPayload(BaseModel):
    """Payload sent to Frappe's submit_hasil_inspeksi endpoint (Requirement 8.4)."""

    motor_tarikan: str
    telegram_id: str
    tipe_inspeksi: Literal["Inspeksi", "Inspeksi Ulang"]
    komponen: dict[str, str]   # 66 mandatory + non-null conditional STNK fields
    foto_urls: dict[str, str]  # exactly 10 entries keyed by PHOTO_FIELDS
    catatan: str | None = None


class SubmitResult(BaseModel):
    """Response from Frappe's submit_hasil_inspeksi endpoint."""

    ok: bool
    name: str | None = None          # e.g. "HI-PJ-001-0001"
    already_completed: bool = False

    @classmethod
    def synthetic_success_already_completed(cls) -> "SubmitResult":
        """Construct a synthetic success result for the 'already completed' edge case
        (Requirement 8.9): Frappe rejected with 'status sudah Selesai Inspeksi'."""
        return cls(ok=True, already_completed=True)
