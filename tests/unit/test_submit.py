"""Unit tests for the submit handler (src/bot/handlers/submit.py).

Tests the submission pipeline logic and handler error mapping.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.adapters.exceptions import (
    FrappePermissionError,
    FrappeUnavailable,
    FrappeValidationError,
    PreSubmitValidationError,
    StatusChanged,
    StatusMismatch,
)
from bot.domain.models import (
    COMPONENT_OPTIONS,
    MANDATORY_FIELDS,
    PHOTO_FIELDS,
    MotorMeta,
    MotorTarikan,
    Phase,
    Session,
    SubmitResult,
    ValidationError,
)
from bot.handlers.submit import _submit_inspection, CB_KIRIM_HASIL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_complete_session(
    telegram_id: str = "123456",
    motor_id: str = "PJ-001",
    tipe_inspeksi: str = "Inspeksi",
) -> Session:
    """Create a fully-filled session that passes pre-submit validation."""
    answers: dict[str, str | None] = {}
    for field in MANDATORY_FIELDS:
        options = COMPONENT_OPTIONS[field]
        answers[field] = options[0]  # first valid option

    photos: dict[str, str] = {}
    for field in PHOTO_FIELDS:
        photos[field] = f"file_id_{field}"

    return Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi=tipe_inspeksi,
        inspection_started=True,
        started_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        phase=Phase.SUMMARY,
        answers=answers,
        photos=photos,
        motor_meta=MotorMeta(
            name=motor_id,
            nopol="B1234XYZ",
            merk="Honda",
            model="Beat",
            tahun="2022",
            warna="Merah",
        ),
    )


def _make_motor_tarikan(
    name: str = "PJ-001",
    status_inspeksi: str = "Proses Inspeksi",
) -> MotorTarikan:
    """Create a MotorTarikan matching the test session."""
    return MotorTarikan(
        name=name,
        nopol="B1234XYZ",
        merk="Honda",
        model="Beat",
        tahun="2022",
        warna="Merah",
        status_inspeksi=status_inspeksi,
    )


def _make_settings() -> MagicMock:
    """Create a mock Settings object."""
    settings = MagicMock()
    settings.photo_max_bytes = 5 * 1024 * 1024
    settings.photo_compress_target_longest_edge = 1920
    return settings


# ---------------------------------------------------------------------------
# Tests: _submit_inspection pipeline
# ---------------------------------------------------------------------------


class TestSubmitInspectionPipeline:
    """Tests for the _submit_inspection function."""

    @pytest.mark.asyncio
    async def test_success_path(self):
        """Full success path: validate → refresh → upload → build → submit."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        frappe.submit_hasil_inspeksi = AsyncMock(
            return_value=SubmitResult(ok=True, name="HI-PJ-001-0001")
        )

        result = await _submit_inspection(
            session, bot=bot, frappe=frappe, settings=settings
        )

        assert result.ok is True
        assert result.name == "HI-PJ-001-0001"
        # 10 photos uploaded
        assert frappe.upload_foto.call_count == 10
        # Submit called once (no retries needed)
        assert frappe.submit_hasil_inspeksi.call_count == 1

    @pytest.mark.asyncio
    async def test_pre_submit_validation_failure(self):
        """Raises PreSubmitValidationError when session is incomplete."""
        session = _make_complete_session()
        # Remove one answer to make it invalid
        session.answers.pop(MANDATORY_FIELDS[0])

        settings = _make_settings()
        bot = AsyncMock()
        frappe = AsyncMock()

        with pytest.raises(PreSubmitValidationError) as exc_info:
            await _submit_inspection(
                session, bot=bot, frappe=frappe, settings=settings
            )

        assert len(exc_info.value.errors) >= 1
        assert exc_info.value.errors[0].field == MANDATORY_FIELDS[0]
        # Frappe should NOT have been called
        frappe.get_pending_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_changed_motor_not_in_pending(self):
        """Raises StatusChanged when motor is no longer in pending list."""
        session = _make_complete_session()
        settings = _make_settings()
        bot = AsyncMock()

        frappe = AsyncMock()
        # Return empty list — motor not found
        frappe.get_pending_list = AsyncMock(return_value=[])

        with pytest.raises(StatusChanged):
            await _submit_inspection(
                session, bot=bot, frappe=frappe, settings=settings
            )

    @pytest.mark.asyncio
    async def test_status_mismatch_tipe_changed(self):
        """Raises StatusMismatch when tipe_inspeksi no longer matches."""
        session = _make_complete_session(tipe_inspeksi="Inspeksi")
        settings = _make_settings()
        bot = AsyncMock()

        # Motor now has "Proses Inspeksi Ulang" → expected "Inspeksi Ulang"
        motor = _make_motor_tarikan(status_inspeksi="Proses Inspeksi Ulang")
        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])

        with pytest.raises(StatusMismatch):
            await _submit_inspection(
                session, bot=bot, frappe=frappe, settings=settings
            )

    @pytest.mark.asyncio
    async def test_already_completed_treated_as_success(self):
        """FrappeValidationError with 'already completed' → synthetic success."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        # Simulate "already completed" validation error
        frappe.submit_hasil_inspeksi = AsyncMock(
            side_effect=FrappeValidationError("Status sudah Selesai Inspeksi")
        )

        result = await _submit_inspection(
            session, bot=bot, frappe=frappe, settings=settings
        )

        assert result.ok is True
        assert result.already_completed is True

    @pytest.mark.asyncio
    async def test_frappe_validation_error_payload_incomplete_raises(self):
        """FrappeValidationError with 'incomplete' message is re-raised."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        frappe.submit_hasil_inspeksi = AsyncMock(
            side_effect=FrappeValidationError("Data tidak lengkap: field X missing")
        )

        with pytest.raises(FrappeValidationError) as exc_info:
            await _submit_inspection(
                session, bot=bot, frappe=frappe, settings=settings
            )

        assert exc_info.value.indicates_payload_incomplete()

    @pytest.mark.asyncio
    async def test_permission_error_raises(self):
        """FrappePermissionError (403) is propagated."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        frappe.submit_hasil_inspeksi = AsyncMock(
            side_effect=FrappePermissionError("Permission denied")
        )

        with pytest.raises(FrappePermissionError):
            await _submit_inspection(
                session, bot=bot, frappe=frappe, settings=settings
            )

    @pytest.mark.asyncio
    async def test_retry_on_unavailable_then_success(self):
        """FrappeUnavailable triggers retry; succeeds on 2nd attempt."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        # Fail once, then succeed
        frappe.submit_hasil_inspeksi = AsyncMock(
            side_effect=[
                FrappeUnavailable("Server error", status_code=500),
                SubmitResult(ok=True, name="HI-PJ-001-0001"),
            ]
        )

        with patch("bot.handlers.submit.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await _submit_inspection(
                session, bot=bot, frappe=frappe, settings=settings
            )

        assert result.ok is True
        assert result.name == "HI-PJ-001-0001"
        # Should have slept once (2s backoff)
        mock_sleep.assert_called_once_with(2)
        assert frappe.submit_hasil_inspeksi.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_unavailable(self):
        """FrappeUnavailable after 4 attempts (1 + 3 retries) raises."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        frappe.submit_hasil_inspeksi = AsyncMock(
            side_effect=FrappeUnavailable("Server error", status_code=500)
        )

        with patch("bot.handlers.submit.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(FrappeUnavailable):
                await _submit_inspection(
                    session, bot=bot, frappe=frappe, settings=settings
                )

        # 4 total attempts
        assert frappe.submit_hasil_inspeksi.call_count == 4

    @pytest.mark.asyncio
    async def test_retry_backoff_schedule(self):
        """Verify backoff schedule is 2s, 4s, 8s."""
        session = _make_complete_session()
        motor = _make_motor_tarikan()
        settings = _make_settings()

        bot = AsyncMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="path/to/file"))
        bot.download_file = AsyncMock(return_value=b"fake_image_bytes")

        frappe = AsyncMock()
        frappe.get_pending_list = AsyncMock(return_value=[motor])
        frappe.upload_foto = AsyncMock(return_value="/files/photo.jpg")
        frappe.submit_hasil_inspeksi = AsyncMock(
            side_effect=FrappeUnavailable("Server error", status_code=500)
        )

        with patch("bot.handlers.submit.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(FrappeUnavailable):
                await _submit_inspection(
                    session, bot=bot, frappe=frappe, settings=settings
                )

        # Should have slept 3 times with backoffs 2, 4, 8
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)
        mock_sleep.assert_any_call(8)
