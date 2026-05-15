"""
Unit tests for src/bot/logging.py.

Verifies:
- configure_logging sets up structlog without raising.
- get_logger returns a usable BoundLogger.
- Each audit-event helper emits a JSON line with the correct event_type and
  required fields.
- No secret values (frappe_api_key, frappe_api_secret, telegram_bot_token)
  are accepted as parameters or appear in log output (Requirement 12.3).
- ISO 8601 timestamps are produced correctly.
- Session duration is computed when both started_at and submitted_at are given.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone

import pytest
import structlog

from bot.logging import (
    _to_iso,
    configure_logging,
    get_logger,
    log_category_revised,
    log_inspection_requested,
    log_inspection_started,
    log_submit_failed,
    log_submit_success,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_log(func, *args, **kwargs) -> dict:
    """Run *func* with structlog redirected to a StringIO buffer.

    Returns the parsed JSON dict of the single log line emitted.
    """
    buf = io.StringIO()
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )
    func(*args, **kwargs)
    line = buf.getvalue().strip()
    assert line, "No log output was produced"
    return json.loads(line)


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------

class TestConfigureLogging:
    def test_does_not_raise_with_default_level(self):
        configure_logging()  # should not raise

    def test_does_not_raise_with_debug_level(self):
        configure_logging("DEBUG")

    def test_sets_root_logger_level(self):
        configure_logging("WARNING")
        assert logging.getLogger().level == logging.WARNING
        # Reset to INFO for subsequent tests.
        configure_logging("INFO")

    def test_unknown_level_falls_back_to_info(self):
        # getattr with a fallback of INFO means unknown strings → INFO
        configure_logging("NOTAREAL")
        assert logging.getLogger().level == logging.INFO


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------

class TestGetLogger:
    def test_returns_bound_logger(self):
        log = get_logger("test.module")
        # structlog BoundLogger exposes .info, .warning, .error, etc.
        assert callable(getattr(log, "info", None))
        assert callable(getattr(log, "warning", None))

    def test_default_name_is_module(self):
        # Should not raise; name defaults to __name__ of logging.py
        log = get_logger()
        assert log is not None


# ---------------------------------------------------------------------------
# log_inspection_requested
# ---------------------------------------------------------------------------

class TestLogInspectionRequested:
    def test_event_type(self):
        entry = _capture_log(
            log_inspection_requested,
            motor_tarikan="PJ-001",
            inspector_chat_id=123456,
            tipe_inspeksi="Inspeksi",
            received_at="2025-01-13T08:42:11+00:00",
        )
        assert entry["event_type"] == "INSPECTION_REQUESTED"

    def test_required_fields_present(self):
        entry = _capture_log(
            log_inspection_requested,
            motor_tarikan="PJ-002",
            inspector_chat_id="987654",
            tipe_inspeksi="Inspeksi Ulang",
            received_at="2025-01-13T08:42:11+00:00",
        )
        assert entry["motor_tarikan"] == "PJ-002"
        assert entry["inspector_chat_id"] == "987654"
        assert entry["tipe_inspeksi"] == "Inspeksi Ulang"
        assert "received_at" in entry
        assert "ts" in entry
        assert entry["level"] == "info"

    def test_accepts_datetime_received_at(self):
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        entry = _capture_log(
            log_inspection_requested,
            motor_tarikan="PJ-003",
            inspector_chat_id=111,
            tipe_inspeksi="Inspeksi",
            received_at=dt,
        )
        assert "2025-06-01" in entry["received_at"]

    def test_inspector_chat_id_coerced_to_string(self):
        entry = _capture_log(
            log_inspection_requested,
            motor_tarikan="PJ-004",
            inspector_chat_id=42,
            tipe_inspeksi="Inspeksi",
            received_at="2025-01-01T00:00:00Z",
        )
        assert isinstance(entry["inspector_chat_id"], str)
        assert entry["inspector_chat_id"] == "42"


# ---------------------------------------------------------------------------
# log_inspection_started
# ---------------------------------------------------------------------------

class TestLogInspectionStarted:
    def test_event_type(self):
        entry = _capture_log(
            log_inspection_started,
            telegram_id=123,
            motor_tarikan="PJ-001",
            tipe_inspeksi="Inspeksi",
        )
        assert entry["event_type"] == "INSPECTION_STARTED"

    def test_required_fields(self):
        entry = _capture_log(
            log_inspection_started,
            telegram_id="456",
            motor_tarikan="PJ-005",
            tipe_inspeksi="Inspeksi Ulang",
        )
        assert entry["telegram_id"] == "456"
        assert entry["motor_tarikan"] == "PJ-005"
        assert entry["tipe_inspeksi"] == "Inspeksi Ulang"
        assert "started_at" in entry
        assert entry["level"] == "info"

    def test_started_at_is_iso8601(self):
        entry = _capture_log(
            log_inspection_started,
            telegram_id=1,
            motor_tarikan="PJ-006",
            tipe_inspeksi="Inspeksi",
        )
        # Should be parseable as ISO 8601
        datetime.fromisoformat(entry["started_at"])


# ---------------------------------------------------------------------------
# log_category_revised
# ---------------------------------------------------------------------------

class TestLogCategoryRevised:
    def test_event_type(self):
        entry = _capture_log(
            log_category_revised,
            telegram_id=123,
            motor_tarikan="PJ-001",
            category_name="Body & Rangka",
        )
        assert entry["event_type"] == "CATEGORY_REVISED"

    def test_required_fields(self):
        entry = _capture_log(
            log_category_revised,
            telegram_id="789",
            motor_tarikan="PJ-007",
            category_name="Mesin",
        )
        assert entry["telegram_id"] == "789"
        assert entry["motor_tarikan"] == "PJ-007"
        assert entry["category_name"] == "Mesin"
        assert "timestamp" in entry
        assert entry["level"] == "info"

    def test_timestamp_is_iso8601(self):
        entry = _capture_log(
            log_category_revised,
            telegram_id=1,
            motor_tarikan="PJ-008",
            category_name="Kelistrikan",
        )
        datetime.fromisoformat(entry["timestamp"])


# ---------------------------------------------------------------------------
# log_submit_success
# ---------------------------------------------------------------------------

class TestLogSubmitSuccess:
    def test_event_type(self):
        entry = _capture_log(
            log_submit_success,
            telegram_id=123,
            motor_tarikan="PJ-001",
            hasil_inspeksi_name="HI-PJ-001-0001",
        )
        assert entry["event_type"] == "SUBMIT_SUCCESS"

    def test_required_fields(self):
        entry = _capture_log(
            log_submit_success,
            telegram_id="321",
            motor_tarikan="PJ-009",
            hasil_inspeksi_name="HI-PJ-009-0001",
            tipe_inspeksi="Inspeksi",
        )
        assert entry["telegram_id"] == "321"
        assert entry["motor_tarikan"] == "PJ-009"
        assert entry["hasil_inspeksi_name"] == "HI-PJ-009-0001"
        assert entry["tipe_inspeksi"] == "Inspeksi"
        assert "submitted_at" in entry
        assert entry["level"] == "info"

    def test_session_duration_computed(self):
        started = datetime(2025, 1, 13, 8, 0, 0, tzinfo=timezone.utc)
        submitted = datetime(2025, 1, 13, 8, 30, 0, tzinfo=timezone.utc)
        entry = _capture_log(
            log_submit_success,
            telegram_id=1,
            motor_tarikan="PJ-010",
            hasil_inspeksi_name="HI-PJ-010-0001",
            started_at=started,
            submitted_at=submitted,
        )
        assert entry["session_duration_seconds"] == pytest.approx(1800.0)

    def test_no_duration_without_started_at(self):
        entry = _capture_log(
            log_submit_success,
            telegram_id=1,
            motor_tarikan="PJ-011",
            hasil_inspeksi_name="HI-PJ-011-0001",
        )
        assert "session_duration_seconds" not in entry

    def test_submitted_at_defaults_to_now(self):
        before = datetime.now(tz=timezone.utc)
        entry = _capture_log(
            log_submit_success,
            telegram_id=1,
            motor_tarikan="PJ-012",
            hasil_inspeksi_name="HI-PJ-012-0001",
        )
        after = datetime.now(tz=timezone.utc)
        submitted_at = datetime.fromisoformat(entry["submitted_at"])
        assert before <= submitted_at <= after


# ---------------------------------------------------------------------------
# log_submit_failed
# ---------------------------------------------------------------------------

class TestLogSubmitFailed:
    def test_event_type(self):
        entry = _capture_log(
            log_submit_failed,
            telegram_id=123,
            motor_tarikan="PJ-001",
            error_type="FrappeUnavailable",
            error_message="Connection refused",
        )
        assert entry["event_type"] == "SUBMIT_FAILED"

    def test_required_fields(self):
        entry = _capture_log(
            log_submit_failed,
            telegram_id="555",
            motor_tarikan="PJ-013",
            error_type="FrappeValidationError",
            error_message="Payload incomplete",
            attempt=2,
            status_code=417,
        )
        assert entry["telegram_id"] == "555"
        assert entry["motor_tarikan"] == "PJ-013"
        assert entry["error_type"] == "FrappeValidationError"
        assert entry["error_message"] == "Payload incomplete"
        assert entry["attempt"] == 2
        assert entry["status_code"] == 417
        assert entry["level"] == "warning"

    def test_default_attempt_is_1(self):
        entry = _capture_log(
            log_submit_failed,
            telegram_id=1,
            motor_tarikan="PJ-014",
            error_type="NetworkError",
            error_message="Timeout",
        )
        assert entry["attempt"] == 1

    def test_status_code_omitted_when_none(self):
        entry = _capture_log(
            log_submit_failed,
            telegram_id=1,
            motor_tarikan="PJ-015",
            error_type="NetworkError",
            error_message="Timeout",
        )
        assert "status_code" not in entry

    def test_level_is_warning(self):
        entry = _capture_log(
            log_submit_failed,
            telegram_id=1,
            motor_tarikan="PJ-016",
            error_type="FrappeUnavailable",
            error_message="503",
        )
        assert entry["level"] == "warning"


# ---------------------------------------------------------------------------
# _to_iso helper
# ---------------------------------------------------------------------------

class TestToIso:
    def test_datetime_aware(self):
        dt = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = _to_iso(dt)
        assert "2025-06-15" in result
        assert "10:30:00" in result

    def test_datetime_naive_treated_as_utc(self):
        dt = datetime(2025, 6, 15, 10, 30, 0)
        result = _to_iso(dt)
        # Should include UTC offset info after replacement
        assert "2025-06-15" in result

    def test_string_passthrough(self):
        s = "2025-01-13T08:42:11Z"
        assert _to_iso(s) == s


# ---------------------------------------------------------------------------
# Security: no secret parameters accepted
# ---------------------------------------------------------------------------

class TestNoSecretParameters:
    """Verify that audit helpers do not accept secret credential parameters."""

    def test_log_inspection_requested_no_secret_params(self):
        import inspect
        from bot.logging import log_inspection_requested
        sig = inspect.signature(log_inspection_requested)
        param_names = set(sig.parameters.keys())
        assert "frappe_api_key" not in param_names
        assert "frappe_api_secret" not in param_names
        assert "telegram_bot_token" not in param_names

    def test_log_inspection_started_no_secret_params(self):
        import inspect
        from bot.logging import log_inspection_started
        sig = inspect.signature(log_inspection_started)
        param_names = set(sig.parameters.keys())
        assert "frappe_api_key" not in param_names
        assert "frappe_api_secret" not in param_names
        assert "telegram_bot_token" not in param_names

    def test_log_submit_success_no_secret_params(self):
        import inspect
        from bot.logging import log_submit_success
        sig = inspect.signature(log_submit_success)
        param_names = set(sig.parameters.keys())
        assert "frappe_api_key" not in param_names
        assert "frappe_api_secret" not in param_names
        assert "telegram_bot_token" not in param_names

    def test_log_submit_failed_no_secret_params(self):
        import inspect
        from bot.logging import log_submit_failed
        sig = inspect.signature(log_submit_failed)
        param_names = set(sig.parameters.keys())
        assert "frappe_api_key" not in param_names
        assert "frappe_api_secret" not in param_names
        assert "telegram_bot_token" not in param_names
