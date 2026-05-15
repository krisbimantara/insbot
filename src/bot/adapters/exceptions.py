"""Exception hierarchy for the Telegram Inspection Bot.

All bot-specific exceptions derive from ``InspectionBotError``.

Hierarchy::

    InspectionBotError(Exception)
    ├── FrappeError                    # base for all Frappe API errors
    │   ├── FrappePermissionError      # 403 / PermissionError from Frappe
    │   ├── FrappeNotFound             # 404 / DoesNotExistError from Frappe
    │   ├── FrappeValidationError      # 400/417 / ValidationError from Frappe
    │   └── FrappeUnavailable          # 5xx / network error from Frappe
    ├── SessionError                   # base for session errors
    │   ├── SessionExpired             # TTL expired (Requirement 9.6)
    │   └── SessionNotFound            # session key not found in Redis
    ├── PreSubmitValidationError       # pre-submit validation failed (Requirement 8.1)
    ├── StatusChanged                  # motor reassigned/removed (Requirement 15.2)
    └── StatusMismatch                 # tipe_inspeksi changed (Requirement 15.3)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.domain.models import ValidationError


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class InspectionBotError(Exception):
    """Base exception for all Telegram Inspection Bot errors."""


# ---------------------------------------------------------------------------
# Frappe API errors (Requirements 8.8, 8.9, 8.10)
# ---------------------------------------------------------------------------


class FrappeError(InspectionBotError):
    """Base exception for errors originating from the Frappe REST API."""


class FrappePermissionError(FrappeError):
    """Raised when Frappe returns HTTP 403 or ``exc_type = 'PermissionError'``.

    Requirement 8.8: Bot shall reply "Akses ditolak. Hubungi admin." and stop
    processing the update.
    """

    def __init__(self, message: str = "Permission denied by Frappe") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"FrappePermissionError: {self.message}"


class FrappeNotFound(FrappeError):
    """Raised when Frappe returns HTTP 404 or ``exc_type = 'DoesNotExistError'``."""

    def __init__(self, message: str = "Resource not found in Frappe") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"FrappeNotFound: {self.message}"


class FrappeValidationError(FrappeError):
    """Raised when Frappe returns HTTP 400/417 or ``exc_type = 'ValidationError'``.

    Stores the raw ``message`` string from Frappe so that callers can inspect
    it to distinguish between different validation failure scenarios
    (Requirements 8.8, 8.9).
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"FrappeValidationError: {self.message}"

    def indicates_already_completed(self) -> bool:
        """Return True if the Frappe message indicates the motor is already completed.

        Matches messages that contain ``"Selesai Inspeksi"`` or
        ``"already completed"`` (Requirement 8.9).
        """
        msg_lower = self.message.lower()
        return "selesai inspeksi" in msg_lower or "already completed" in msg_lower

    def indicates_payload_incomplete(self) -> bool:
        """Return True if the Frappe message indicates the submitted payload is incomplete.

        Matches messages that contain ``"tidak lengkap"``, ``"incomplete"``, or
        ``"missing"`` (Requirement 8.8).
        """
        msg_lower = self.message.lower()
        return (
            "tidak lengkap" in msg_lower
            or "incomplete" in msg_lower
            or "missing" in msg_lower
        )


class FrappeUnavailable(FrappeError):
    """Raised when Frappe returns HTTP 5xx or a network-level error occurs.

    ``status_code`` is ``None`` for pure network errors (e.g. connection
    refused, timeout) and an integer for HTTP 5xx responses.

    Requirement 8.10: Bot shall retry up to 3 times with exponential backoff
    (2 s / 4 s / 8 s) before surfacing the error to the inspector.
    """

    def __init__(
        self,
        message: str = "Frappe is unavailable",
        status_code: int | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        if self.status_code is not None:
            return f"FrappeUnavailable(status={self.status_code}): {self.message}"
        return f"FrappeUnavailable: {self.message}"


# ---------------------------------------------------------------------------
# Session errors (Requirements 9.5, 9.6)
# ---------------------------------------------------------------------------


class SessionError(InspectionBotError):
    """Base exception for Redis session errors."""


class SessionExpired(SessionError):
    """Raised when a session's TTL has expired in Redis (Requirement 9.6).

    The bot shall display:
    "Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."
    """

    def __init__(
        self,
        telegram_id: str | None = None,
        motor_id: str | None = None,
    ) -> None:
        self.telegram_id = telegram_id
        self.motor_id = motor_id
        parts = []
        if telegram_id:
            parts.append(f"telegram_id={telegram_id}")
        if motor_id:
            parts.append(f"motor_id={motor_id}")
        detail = f" ({', '.join(parts)})" if parts else ""
        super().__init__(f"Session expired{detail}")

    def __str__(self) -> str:
        return self.args[0]


class SessionNotFound(SessionError):
    """Raised when the expected session key does not exist in Redis.

    This is distinct from ``SessionExpired``: the key was never created or was
    explicitly deleted (e.g. after a successful submit).
    """

    def __init__(
        self,
        telegram_id: str | None = None,
        motor_id: str | None = None,
    ) -> None:
        self.telegram_id = telegram_id
        self.motor_id = motor_id
        parts = []
        if telegram_id:
            parts.append(f"telegram_id={telegram_id}")
        if motor_id:
            parts.append(f"motor_id={motor_id}")
        detail = f" ({', '.join(parts)})" if parts else ""
        super().__init__(f"Session not found{detail}")

    def __str__(self) -> str:
        return self.args[0]


# ---------------------------------------------------------------------------
# Pre-submit validation error (Requirement 8.1)
# ---------------------------------------------------------------------------


class PreSubmitValidationError(InspectionBotError):
    """Raised when ``validate_pre_submit`` finds one or more missing/invalid fields.

    ``errors`` is the list of :class:`~bot.domain.models.ValidationError`
    instances returned by ``validate_pre_submit``.  The bot shall display the
    missing fields to the inspector and return to the Summary page without
    calling Frappe (Requirement 8.2).
    """

    def __init__(self, errors: "list[ValidationError]") -> None:
        self.errors = errors
        count = len(errors)
        super().__init__(
            f"Pre-submit validation failed with {count} error(s): "
            + ", ".join(e.field for e in errors)
        )

    def __str__(self) -> str:
        return self.args[0]


# ---------------------------------------------------------------------------
# Status-change errors (Requirements 15.2, 15.3)
# ---------------------------------------------------------------------------


class StatusChanged(InspectionBotError):
    """Raised when the motor has been reassigned or removed from the inspector's
    pending queue between session creation and submit (Requirement 15.2).

    The bot shall inform the inspector that the motor is no longer available
    and delete the stale session.
    """

    def __init__(
        self,
        motor_id: str | None = None,
        message: str | None = None,
    ) -> None:
        self.motor_id = motor_id
        detail = message or (
            f"Motor '{motor_id}' is no longer in the pending queue"
            if motor_id
            else "Motor has been reassigned or removed"
        )
        super().__init__(detail)

    def __str__(self) -> str:
        return self.args[0]


class StatusMismatch(InspectionBotError):
    """Raised when ``tipe_inspeksi`` in the session no longer matches the current
    Frappe status at submit time (Requirement 15.3).

    For example, the motor was changed from "Proses Inspeksi" to
    "Proses Inspeksi Ulang" while the inspector was filling the checklist.
    The bot shall inform the inspector and prompt them to restart the session.
    """

    def __init__(
        self,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.expected = expected
        self.actual = actual
        if expected and actual:
            detail = (
                f"tipe_inspeksi mismatch: session has '{expected}', "
                f"Frappe now reports '{actual}'"
            )
        else:
            detail = "tipe_inspeksi has changed since the session was created"
        super().__init__(detail)

    def __str__(self) -> str:
        return self.args[0]
