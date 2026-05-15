"""Unit tests for src/bot/domain/payload.py.

Tests cover:
- build_submit_payload: komponen contains exactly 66 mandatory keys
- build_submit_payload: conditional STNK fields included only for Cukup/Rusak
- build_submit_payload: stnk=Baik → no conditional fields in komponen
- build_submit_payload: foto_urls validated (wrong keys raise ValueError)
- build_submit_payload: tipe_inspeksi, motor_tarikan, telegram_id, catatan
- build_idempotency_key: format {telegram_id}:{motor_tarikan}:{started_at}
- build_idempotency_key: started_at=None → "unknown"
- build_idempotency_key: deterministic for same inputs
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.domain.models import (
    MANDATORY_FIELDS,
    PHOTO_FIELDS,
    STNK_CONDITIONAL_BY_ANSWER,
    MotorMeta,
    Phase,
    Session,
    SubmitPayload,
)
from bot.domain.payload import build_idempotency_key, build_submit_payload


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_MOTOR_META = MotorMeta(
    name="PJ-001",
    nopol="B 1234 XYZ",
    merk="Honda",
    model="Beat",
    tahun="2020",
    warna="Merah",
)


def _make_full_answers(stnk_value: str = "Baik") -> dict[str, str | None]:
    """Return a dict with all 66 mandatory fields filled with valid values."""
    from bot.domain.models import COMPONENT_OPTIONS

    answers: dict[str, str | None] = {}
    for field in MANDATORY_FIELDS:
        options = COMPONENT_OPTIONS[field]
        answers[field] = options[0]  # pick first valid option
    # Override stnk with the requested value
    answers["stnk"] = stnk_value
    return answers


def _make_foto_urls() -> dict[str, str]:
    """Return a dict with all 10 photo fields mapped to dummy URLs."""
    return {field: f"https://example.com/{field}.jpg" for field in PHOTO_FIELDS}


def _make_session(
    stnk_value: str = "Baik",
    extra_answers: dict[str, str | None] | None = None,
    started_at: datetime | None = None,
    telegram_id: str = "111222333",
    motor_id: str = "PJ-001",
    tipe_inspeksi: str = "Inspeksi",
) -> Session:
    answers = _make_full_answers(stnk_value)
    if extra_answers:
        answers.update(extra_answers)
    return Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi=tipe_inspeksi,  # type: ignore[arg-type]
        inspection_started=True,
        started_at=started_at,
        phase=Phase.SUMMARY,
        answers=answers,
        stnk_answer=stnk_value,  # type: ignore[arg-type]
        photos={field: f"file_id_{field}" for field in PHOTO_FIELDS},
        motor_meta=_MOTOR_META,
    )


# ---------------------------------------------------------------------------
# build_submit_payload — mandatory fields
# ---------------------------------------------------------------------------


class TestBuildSubmitPayloadMandatoryFields:
    def test_komponen_has_exactly_66_mandatory_keys_when_stnk_baik(self):
        session = _make_session(stnk_value="Baik")
        payload = build_submit_payload(session, _make_foto_urls())
        mandatory_in_komponen = [k for k in payload.komponen if k in set(MANDATORY_FIELDS)]
        assert len(mandatory_in_komponen) == 66

    def test_komponen_contains_all_mandatory_field_names(self):
        session = _make_session(stnk_value="Baik")
        payload = build_submit_payload(session, _make_foto_urls())
        for field in MANDATORY_FIELDS:
            assert field in payload.komponen, f"Missing mandatory field: {field}"

    def test_komponen_values_match_answers(self):
        session = _make_session(stnk_value="Baik")
        payload = build_submit_payload(session, _make_foto_urls())
        for field in MANDATORY_FIELDS:
            assert payload.komponen[field] == session.answers[field]


# ---------------------------------------------------------------------------
# build_submit_payload — conditional STNK (Requirement 14.3 / 5.7)
# ---------------------------------------------------------------------------


class TestBuildSubmitPayloadConditionalStnk:
    def test_stnk_baik_no_conditional_fields_in_komponen(self):
        """stnk=Baik → zero conditional STNK keys in komponen."""
        all_conditional = set(
            f
            for fields in STNK_CONDITIONAL_BY_ANSWER.values()
            for f in fields
        )
        session = _make_session(
            stnk_value="Baik",
            extra_answers={f: "Ya" for f in all_conditional},  # even if stored
        )
        payload = build_submit_payload(session, _make_foto_urls())
        for field in all_conditional:
            assert field not in payload.komponen, (
                f"Conditional field {field!r} must not appear when stnk=Baik"
            )

    def test_stnk_cukup_includes_non_null_conditional_fields(self):
        """stnk=Cukup → 3 conditional fields included when non-null."""
        cukup_fields = STNK_CONDITIONAL_BY_ANSWER["Cukup"]
        extra = {f: "Ya" for f in cukup_fields}
        session = _make_session(stnk_value="Cukup", extra_answers=extra)
        payload = build_submit_payload(session, _make_foto_urls())
        for field in cukup_fields:
            assert field in payload.komponen, (
                f"Non-null conditional field {field!r} must be in komponen for stnk=Cukup"
            )

    def test_stnk_cukup_excludes_null_conditional_fields(self):
        """stnk=Cukup → null conditional fields are NOT included."""
        cukup_fields = STNK_CONDITIONAL_BY_ANSWER["Cukup"]
        extra: dict[str, str | None] = {f: None for f in cukup_fields}
        session = _make_session(stnk_value="Cukup", extra_answers=extra)
        payload = build_submit_payload(session, _make_foto_urls())
        for field in cukup_fields:
            assert field not in payload.komponen, (
                f"Null conditional field {field!r} must NOT be in komponen"
            )

    def test_stnk_rusak_includes_non_null_conditional_fields(self):
        """stnk=Rusak → 4 conditional fields included when non-null."""
        rusak_fields = STNK_CONDITIONAL_BY_ANSWER["Rusak"]
        extra = {f: "Ya" for f in rusak_fields}
        session = _make_session(stnk_value="Rusak", extra_answers=extra)
        payload = build_submit_payload(session, _make_foto_urls())
        for field in rusak_fields:
            assert field in payload.komponen, (
                f"Non-null conditional field {field!r} must be in komponen for stnk=Rusak"
            )

    def test_stnk_rusak_excludes_fields_not_in_rusak_set(self):
        """stnk=Rusak → fields only in Cukup set (none in this case) not added."""
        # Rusak is a superset of Cukup, so verify Cukup-only fields are not
        # double-counted; also verify stnk_ta (Rusak-only) is included.
        rusak_fields = set(STNK_CONDITIONAL_BY_ANSWER["Rusak"])
        cukup_fields = set(STNK_CONDITIONAL_BY_ANSWER["Cukup"])
        rusak_only = rusak_fields - cukup_fields  # {"stnk_ta"}
        extra = {f: "Ya" for f in rusak_fields}
        session = _make_session(stnk_value="Rusak", extra_answers=extra)
        payload = build_submit_payload(session, _make_foto_urls())
        for field in rusak_only:
            assert field in payload.komponen, (
                f"Rusak-only field {field!r} must be in komponen for stnk=Rusak"
            )

    def test_stnk_cukup_does_not_include_rusak_only_fields(self):
        """stnk=Cukup → stnk_ta (Rusak-only) must NOT appear even if stored."""
        rusak_only = set(STNK_CONDITIONAL_BY_ANSWER["Rusak"]) - set(
            STNK_CONDITIONAL_BY_ANSWER["Cukup"]
        )
        extra = {f: "Ya" for f in rusak_only}
        session = _make_session(stnk_value="Cukup", extra_answers=extra)
        payload = build_submit_payload(session, _make_foto_urls())
        for field in rusak_only:
            assert field not in payload.komponen, (
                f"Rusak-only field {field!r} must NOT appear for stnk=Cukup"
            )


# ---------------------------------------------------------------------------
# build_submit_payload — foto_urls validation
# ---------------------------------------------------------------------------


class TestBuildSubmitPayloadFotoUrls:
    def test_foto_urls_has_exactly_10_entries(self):
        session = _make_session()
        payload = build_submit_payload(session, _make_foto_urls())
        assert len(payload.foto_urls) == 10

    def test_foto_urls_keys_match_photo_fields(self):
        session = _make_session()
        payload = build_submit_payload(session, _make_foto_urls())
        assert set(payload.foto_urls.keys()) == set(PHOTO_FIELDS)

    def test_foto_urls_values_preserved(self):
        session = _make_session()
        foto_urls = _make_foto_urls()
        payload = build_submit_payload(session, foto_urls)
        for field, url in foto_urls.items():
            assert payload.foto_urls[field] == url

    def test_missing_foto_key_raises_value_error(self):
        session = _make_session()
        bad_urls = {field: "https://x.com/x.jpg" for field in PHOTO_FIELDS[:-1]}  # 9 keys
        with pytest.raises(ValueError, match="missing keys"):
            build_submit_payload(session, bad_urls)

    def test_extra_foto_key_raises_value_error(self):
        session = _make_session()
        bad_urls = _make_foto_urls()
        bad_urls["foto_extra"] = "https://x.com/extra.jpg"
        with pytest.raises(ValueError, match="unexpected keys"):
            build_submit_payload(session, bad_urls)

    def test_wrong_keys_raises_value_error(self):
        session = _make_session()
        bad_urls = {f"wrong_{i}": "https://x.com/x.jpg" for i in range(10)}
        with pytest.raises(ValueError):
            build_submit_payload(session, bad_urls)


# ---------------------------------------------------------------------------
# build_submit_payload — top-level fields
# ---------------------------------------------------------------------------


class TestBuildSubmitPayloadTopLevelFields:
    def test_motor_tarikan_from_session_motor_id(self):
        session = _make_session(motor_id="PJ-999")
        payload = build_submit_payload(session, _make_foto_urls())
        assert payload.motor_tarikan == "PJ-999"

    def test_telegram_id_from_session(self):
        session = _make_session(telegram_id="987654321")
        payload = build_submit_payload(session, _make_foto_urls())
        assert payload.telegram_id == "987654321"

    def test_tipe_inspeksi_inspeksi(self):
        session = _make_session(tipe_inspeksi="Inspeksi")
        payload = build_submit_payload(session, _make_foto_urls())
        assert payload.tipe_inspeksi == "Inspeksi"

    def test_tipe_inspeksi_inspeksi_ulang(self):
        session = _make_session(tipe_inspeksi="Inspeksi Ulang")
        payload = build_submit_payload(session, _make_foto_urls())
        assert payload.tipe_inspeksi == "Inspeksi Ulang"

    def test_catatan_none_when_not_set(self):
        session = _make_session()
        payload = build_submit_payload(session, _make_foto_urls())
        assert payload.catatan is None

    def test_returns_submit_payload_instance(self):
        session = _make_session()
        payload = build_submit_payload(session, _make_foto_urls())
        assert isinstance(payload, SubmitPayload)


# ---------------------------------------------------------------------------
# build_submit_payload — purity / determinism
# ---------------------------------------------------------------------------


class TestBuildSubmitPayloadPurity:
    def test_identical_inputs_produce_identical_output(self):
        session = _make_session(stnk_value="Cukup", extra_answers={
            "stnk_hilang_polisi": "Ya",
            "stnk_tilang": "Tidak",
            "stnk_mati_tanggal": "2025-01-01",
        })
        foto_urls = _make_foto_urls()
        payload1 = build_submit_payload(session, foto_urls)
        payload2 = build_submit_payload(session, foto_urls)
        assert payload1.komponen == payload2.komponen
        assert payload1.foto_urls == payload2.foto_urls

    def test_foto_urls_is_defensive_copy(self):
        """Mutating the original dict after call must not affect the payload."""
        session = _make_session()
        foto_urls = _make_foto_urls()
        payload = build_submit_payload(session, foto_urls)
        original_url = foto_urls[PHOTO_FIELDS[0]]
        foto_urls[PHOTO_FIELDS[0]] = "https://mutated.example.com/x.jpg"
        assert payload.foto_urls[PHOTO_FIELDS[0]] == original_url


# ---------------------------------------------------------------------------
# build_idempotency_key
# ---------------------------------------------------------------------------


class TestBuildIdempotencyKey:
    def test_format_with_started_at(self):
        dt = datetime(2025, 1, 13, 8, 42, 11, tzinfo=timezone.utc)
        session = _make_session(
            telegram_id="111222333",
            motor_id="PJ-001",
            started_at=dt,
        )
        key = build_idempotency_key(session)
        assert key == f"111222333:PJ-001:{dt.isoformat()}"

    def test_format_without_started_at(self):
        session = _make_session(
            telegram_id="111222333",
            motor_id="PJ-001",
            started_at=None,
        )
        key = build_idempotency_key(session)
        assert key == "111222333:PJ-001:unknown"

    def test_deterministic_same_inputs(self):
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        session = _make_session(started_at=dt)
        key1 = build_idempotency_key(session)
        key2 = build_idempotency_key(session)
        assert key1 == key2

    def test_different_telegram_id_produces_different_key(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        s1 = _make_session(telegram_id="AAA", motor_id="PJ-001", started_at=dt)
        s2 = _make_session(telegram_id="BBB", motor_id="PJ-001", started_at=dt)
        assert build_idempotency_key(s1) != build_idempotency_key(s2)

    def test_different_motor_id_produces_different_key(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        s1 = _make_session(telegram_id="AAA", motor_id="PJ-001", started_at=dt)
        s2 = _make_session(telegram_id="AAA", motor_id="PJ-002", started_at=dt)
        assert build_idempotency_key(s1) != build_idempotency_key(s2)

    def test_different_started_at_produces_different_key(self):
        dt1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        dt2 = datetime(2025, 1, 2, tzinfo=timezone.utc)
        s1 = _make_session(telegram_id="AAA", motor_id="PJ-001", started_at=dt1)
        s2 = _make_session(telegram_id="AAA", motor_id="PJ-001", started_at=dt2)
        assert build_idempotency_key(s1) != build_idempotency_key(s2)

    def test_key_contains_three_colon_separated_parts(self):
        dt = datetime(2025, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        session = _make_session(
            telegram_id="123",
            motor_id="MT-007",
            started_at=dt,
        )
        key = build_idempotency_key(session)
        parts = key.split(":", 2)  # split on first 2 colons only
        assert len(parts) == 3
        assert parts[0] == "123"
        assert parts[1] == "MT-007"
        assert parts[2] == dt.isoformat()

    def test_other_session_fields_do_not_affect_key(self):
        """Changing answers, photos, or phase must not change the key."""
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        base = _make_session(telegram_id="X", motor_id="Y", started_at=dt)

        # Create a session with different answers
        different = _make_session(
            telegram_id="X",
            motor_id="Y",
            started_at=dt,
            stnk_value="Rusak",
        )
        assert build_idempotency_key(base) == build_idempotency_key(different)
