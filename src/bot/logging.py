"""
Structured logging setup for Telegram Inspection Bot.

Configures structlog with JSON output to STDOUT, ISO 8601 timestamps, and
log-level filtering.  Provides audit-event helper functions for the five
key lifecycle events defined in Requirement 13.

IMPORTANT: Secret values (frappe_api_key, frappe_api_secret,
telegram_bot_token) are NEVER accepted as parameters and NEVER written to
any log entry (Requirement 12.3).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "configure_logging",
    "get_logger",
    "log_inspection_requested",
    "log_inspection_started",
    "log_category_revised",
    "log_submit_success",
    "log_submit_failed",
]


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog with JSON output to STDOUT.

    Call this once at application startup (before any log calls).

    Args:
        log_level: Standard Python log-level name, e.g. "DEBUG", "INFO",
                   "WARNING", "ERROR".  Defaults to "INFO".

    The resulting log entries are newline-delimited JSON objects written to
    STDOUT, one per line, suitable for container log drivers (Requirement 13.6).

    Each entry contains at minimum:
        ts    — ISO 8601 UTC timestamp
        level — log level string
        event — human-readable event description
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure the standard-library root logger so that structlog's
    # stdlib integration (and any third-party libraries that use logging
    # directly) also emit JSON at the correct level.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
    )

    structlog.configure(
        processors=[
            # Merge in any extra key=value pairs from stdlib log records.
            structlog.stdlib.merge_contextvars,
            # Add the log level as a string field ("level").
            structlog.stdlib.add_log_level,
            # Add ISO 8601 UTC timestamp as "ts".
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            # Render the final dict as a compact JSON string.
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Apply level filtering: drop records below the configured level.
    # structlog itself does not filter by level; we rely on the stdlib
    # handler's level for that when using PrintLoggerFactory.
    logging.getLogger().setLevel(numeric_level)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger bound to *name*.

    Usage::

        log = get_logger(__name__)
        log.info("something_happened", key="value")

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A structlog BoundLogger instance.
    """
    return structlog.get_logger(name)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Audit-event helpers
# ---------------------------------------------------------------------------
# Each helper emits exactly one structured log entry at INFO level.
# The schema matches the Audit Log Event Schema in design.md:
#
#   { "ts": "...", "level": "INFO", "event_type": "...",
#     "telegram_id": "...", "motor_tarikan": "...", "tipe_inspeksi": "...",
#     ...event-specific fields... }
#
# None of these functions accept frappe_api_key, frappe_api_secret, or
# telegram_bot_token as parameters (Requirement 12.3).
# ---------------------------------------------------------------------------

_audit_log = get_logger("bot.audit")


def log_inspection_requested(
    motor_tarikan: str,
    inspector_chat_id: str | int,
    tipe_inspeksi: str,
    received_at: datetime | str,
) -> None:
    """Log an INSPECTION_REQUESTED audit event (Requirement 13.1).

    Emitted when the webhook ``inspection_requested`` is received from Frappe.

    Args:
        motor_tarikan:    Motor Tarikan identifier, e.g. ``"PJ-001"``.
        inspector_chat_id: Telegram chat ID of the assigned inspector.
        tipe_inspeksi:    ``"Inspeksi"`` or ``"Inspeksi Ulang"``.
        received_at:      Timestamp when the webhook was received.
                          Accepts a :class:`datetime` or an ISO 8601 string.
    """
    _audit_log.info(
        "inspection_requested",
        event_type="INSPECTION_REQUESTED",
        motor_tarikan=str(motor_tarikan),
        inspector_chat_id=str(inspector_chat_id),
        tipe_inspeksi=str(tipe_inspeksi),
        received_at=_to_iso(received_at),
    )


def log_inspection_started(
    telegram_id: str | int,
    motor_tarikan: str,
    tipe_inspeksi: str,
) -> None:
    """Log an INSPECTION_STARTED audit event (Requirement 13.2).

    Emitted when ``inspection_started`` transitions from ``False`` to ``True``
    (i.e. the inspector taps "Mulai Inspeksi").

    Args:
        telegram_id:   Inspector's Telegram user ID.
        motor_tarikan: Motor Tarikan identifier.
        tipe_inspeksi: ``"Inspeksi"`` or ``"Inspeksi Ulang"``.
    """
    _audit_log.info(
        "inspection_started",
        event_type="INSPECTION_STARTED",
        telegram_id=str(telegram_id),
        motor_tarikan=str(motor_tarikan),
        tipe_inspeksi=str(tipe_inspeksi),
        started_at=_now_iso(),
    )


def log_category_revised(
    telegram_id: str | int,
    motor_tarikan: str,
    category_name: str,
) -> None:
    """Log a CATEGORY_REVISED audit event (Requirement 13.3).

    Emitted when an inspector completes a category revision.

    Args:
        telegram_id:   Inspector's Telegram user ID.
        motor_tarikan: Motor Tarikan identifier.
        category_name: Human-readable category name, e.g. ``"Body & Rangka"``.
    """
    _audit_log.info(
        "category_revised",
        event_type="CATEGORY_REVISED",
        telegram_id=str(telegram_id),
        motor_tarikan=str(motor_tarikan),
        category_name=str(category_name),
        timestamp=_now_iso(),
    )


def log_submit_success(
    telegram_id: str | int,
    motor_tarikan: str,
    hasil_inspeksi_name: str,
    *,
    tipe_inspeksi: str = "",
    started_at: datetime | str | None = None,
    submitted_at: datetime | str | None = None,
) -> None:
    """Log a SUBMIT_SUCCESS audit event (Requirement 13.4).

    Emitted when Frappe returns HTTP 200 / ``ok=true`` for
    ``submit_hasil_inspeksi``.

    Args:
        telegram_id:         Inspector's Telegram user ID.
        motor_tarikan:       Motor Tarikan identifier.
        hasil_inspeksi_name: Name of the created Hasil Inspeksi document,
                             e.g. ``"HI-PJ-001-0001"``.
        tipe_inspeksi:       ``"Inspeksi"`` or ``"Inspeksi Ulang"`` (optional).
        started_at:          When the inspection session started (optional).
        submitted_at:        When the submit succeeded (optional; defaults to
                             the current UTC time).
    """
    _submitted_at = submitted_at if submitted_at is not None else _now_iso()
    extra: dict[str, Any] = {
        "event_type": "SUBMIT_SUCCESS",
        "telegram_id": str(telegram_id),
        "motor_tarikan": str(motor_tarikan),
        "tipe_inspeksi": str(tipe_inspeksi),
        "hasil_inspeksi_name": str(hasil_inspeksi_name),
        "submitted_at": _to_iso(_submitted_at),
    }
    if started_at is not None:
        extra["started_at"] = _to_iso(started_at)
        # Compute session duration when both timestamps are available.
        try:
            start_dt = _parse_iso(started_at)
            end_dt = _parse_iso(_submitted_at)
            if start_dt is not None and end_dt is not None:
                extra["session_duration_seconds"] = round(
                    (end_dt - start_dt).total_seconds(), 3
                )
        except Exception:  # noqa: BLE001
            pass  # Duration is best-effort; never crash the audit log.

    _audit_log.info("submit_success", **extra)


def log_submit_failed(
    telegram_id: str | int,
    motor_tarikan: str,
    error_type: str,
    error_message: str,
    *,
    attempt: int = 1,
    status_code: int | None = None,
) -> None:
    """Log a SUBMIT_FAILED audit event (Requirement 13.5).

    Emitted on each failed submit attempt (including retries).

    Args:
        telegram_id:   Inspector's Telegram user ID.
        motor_tarikan: Motor Tarikan identifier.
        error_type:    Short error classifier, e.g. ``"FrappeUnavailable"``,
                       ``"FrappeValidationError"``, ``"NetworkError"``.
        error_message: Human-readable error detail.  Must NOT contain secret
                       values.
        attempt:       1-based attempt number (1 = first try, 2 = first retry,
                       …).  Defaults to 1.
        status_code:   HTTP status code returned by Frappe, if available.
    """
    extra: dict[str, Any] = {
        "event_type": "SUBMIT_FAILED",
        "telegram_id": str(telegram_id),
        "motor_tarikan": str(motor_tarikan),
        "error_type": str(error_type),
        "error_message": str(error_message),
        "attempt": int(attempt),
    }
    if status_code is not None:
        extra["status_code"] = int(status_code)

    _audit_log.warning("submit_failed", **extra)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _to_iso(value: datetime | str) -> str:
    """Normalise *value* to an ISO 8601 string.

    Accepts either a :class:`datetime` (naive or aware) or a string that is
    already in ISO 8601 format.  Naive datetimes are assumed to be UTC.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _parse_iso(value: datetime | str) -> datetime | None:
    """Parse *value* into a timezone-aware :class:`datetime`, or return None."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
