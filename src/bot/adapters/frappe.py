"""Frappe HTTP client adapter for the Telegram Inspection Bot.

Wraps all REST API calls to Frappe and maps HTTP/network errors to the
exception hierarchy defined in ``adapters/exceptions.py``.

Requirements: 8.3, 8.4, 8.7, 8.8, 8.9, 11.4, 12.4, 15.3
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from bot.adapters.exceptions import (
    FrappeNotFound,
    FrappePermissionError,
    FrappeUnavailable,
    FrappeValidationError,
)
from bot.config import Settings
from bot.domain.models import MotorTarikan, SubmitPayload, SubmitResult

logger = logging.getLogger(__name__)

# Frappe API endpoint paths
_PENDING_LIST_PATH = "/api/method/juragan.api.inspeksi.pending.get_pending_list"
_UPLOAD_FOTO_PATH = "/api/method/juragan.api.inspeksi.upload.upload_foto"
_SUBMIT_PATH = "/api/method/juragan.api.inspeksi.submit.submit_hasil_inspeksi"


class FrappeClient:
    """Async HTTP client for the Frappe inspection API.

    All methods:
    - Set ``Authorization: token {key}:{secret}`` on every request (Requirement 12.4).
    - Apply a 30-second request timeout (Requirement 11.4).
    - Map HTTP/network errors to the exception hierarchy.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = str(settings.frappe_url).rstrip("/")
        self._api_key = settings.frappe_api_key.get_secret_value()
        self._api_secret = settings.frappe_api_secret.get_secret_value()
        self._timeout = aiohttp.ClientTimeout(total=settings.frappe_request_timeout_seconds)
        # Longer timeout for file uploads (photos can be large)
        self._upload_timeout = aiohttp.ClientTimeout(total=120)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_header(self) -> dict[str, str]:
        """Return the Authorization header dict for every request."""
        return {"Authorization": f"token {self._api_key}:{self._api_secret}"}

    async def _raise_for_frappe_error(self, response: aiohttp.ClientResponse) -> None:
        """Inspect a non-200 response and raise the appropriate exception.

        Error mapping (Requirements 8.8, 8.9):
        - 403 or exc_type == "PermissionError"  → FrappePermissionError
        - 404 or exc_type == "DoesNotExistError" → FrappeNotFound
        - 400/417 or exc_type == "ValidationError" → FrappeValidationError
        - 5xx                                    → FrappeUnavailable
        """
        status = response.status

        # Try to parse JSON body for structured error info
        body: dict[str, Any] = {}
        try:
            body = await response.json(content_type=None)
        except Exception:
            try:
                raw_text = await response.text()
                body = {"message": raw_text}
            except Exception:
                body = {}

        exc_type: str = body.get("exc_type", "")
        # Frappe sometimes nests the message under body["message"]["message"]
        raw_message = body.get("message", "")
        if isinstance(raw_message, dict):
            message: str = raw_message.get("message", str(raw_message))
        else:
            message = str(raw_message) if raw_message else f"HTTP {status}"

        if status == 403 or exc_type == "PermissionError":
            raise FrappePermissionError(message)

        if status == 404 or exc_type == "DoesNotExistError":
            raise FrappeNotFound(message)

        if status in (400, 417) or exc_type == "ValidationError":
            logger.error(
                "frappe_validation_error",
                extra={
                    "status": status,
                    "exc_type": exc_type,
                    "message": message,
                    "body": str(body)[:500],
                },
            )
            raise FrappeValidationError(message)

        if status >= 500:
            raise FrappeUnavailable(message, status_code=status)

        # Unexpected non-success status — treat as unavailable
        raise FrappeUnavailable(f"Unexpected HTTP {status}: {message}", status_code=status)

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_pending_list(self, telegram_id: str) -> list[MotorTarikan]:
        """Fetch the list of pending inspection motors for the given inspector.

        Calls GET /api/method/juragan.api.inspeksi.pending.get_pending_list
        with ``telegram_id`` as a query parameter.

        Returns a (possibly empty) list of :class:`~bot.domain.models.MotorTarikan`.

        Raises:
            FrappePermissionError: 403 or PermissionError from Frappe.
            FrappeValidationError: 400/417 or ValidationError from Frappe.
            FrappeUnavailable: 5xx or network error.
        """
        url = f"{self._base_url}{_PENDING_LIST_PATH}"
        headers = self._auth_header()
        params = {"telegram_id": telegram_id}

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status != 200:
                        await self._raise_for_frappe_error(response)

                    data = await response.json(content_type=None)
        except (FrappePermissionError, FrappeNotFound, FrappeValidationError, FrappeUnavailable):
            raise
        except aiohttp.ClientError as exc:
            raise FrappeUnavailable(f"Network error: {exc}") from exc
        except Exception as exc:
            raise FrappeUnavailable(f"Unexpected error: {exc}") from exc

        # Response shape: {"message": {"ok": true, "data": [...]}}
        message_body = data.get("message", {})
        if isinstance(message_body, dict):
            motors_raw = message_body.get("data", [])
        else:
            motors_raw = []

        return [MotorTarikan.model_validate(item) for item in motors_raw]

    async def upload_foto(self, file_bytes: bytes, filename: str) -> str:
        """Upload a single photo to Frappe File Manager.

        Sends a multipart/form-data POST to the upload endpoint.

        Args:
            file_bytes: Raw image bytes (JPEG/PNG).
            filename: Filename with extension, e.g. ``"foto_tampak_depan_PJ-001.jpg"``.

        Returns:
            The ``file_url`` string returned by Frappe (e.g. ``"/files/foto_depan.jpg"``).

        Raises:
            FrappePermissionError: 403 or PermissionError from Frappe.
            FrappeValidationError: 400/417 or ValidationError from Frappe.
            FrappeUnavailable: 5xx or network error.
        """
        url = f"{self._base_url}{_UPLOAD_FOTO_PATH}"
        headers = self._auth_header()

        form = aiohttp.FormData()
        form.add_field("file", file_bytes, filename=filename, content_type="image/jpeg")
        form.add_field("filename", filename)

        try:
            async with aiohttp.ClientSession(timeout=self._upload_timeout) as session:
                async with session.post(url, headers=headers, data=form) as response:
                    if response.status != 200:
                        await self._raise_for_frappe_error(response)

                    data = await response.json(content_type=None)
        except (FrappePermissionError, FrappeNotFound, FrappeValidationError, FrappeUnavailable):
            raise
        except aiohttp.ClientError as exc:
            raise FrappeUnavailable(f"Network error during upload: {exc}") from exc
        except Exception as exc:
            raise FrappeUnavailable(f"Unexpected error during upload: {exc}") from exc

        # Response shape: {"message": {"ok": true, "file_url": "..."}}
        message_body = data.get("message", {})
        if isinstance(message_body, dict):
            ok = message_body.get("ok", False)
            if not ok:
                # Application-level error returned with HTTP 200
                error_msg = message_body.get("message", "Upload failed")
                raise FrappeValidationError(str(error_msg))
            file_url = message_body.get("file_url", "")
            if not file_url:
                raise FrappeUnavailable("Upload succeeded but file_url is missing in response")
            return str(file_url)

        raise FrappeUnavailable(f"Unexpected upload response shape: {data!r}")

    async def submit_hasil_inspeksi(
        self,
        payload: SubmitPayload,
        *,
        idempotency_key: str,
    ) -> SubmitResult:
        """Submit the completed inspection result to Frappe.

        Sends a JSON POST to the submit endpoint with the full inspection payload.
        The ``idempotency_key`` is sent as the ``X-Idempotency-Key`` header for
        best-effort deduplication (Requirement 8.7).

        Args:
            payload: The fully-built :class:`~bot.domain.models.SubmitPayload`.
            idempotency_key: Unique key for this submission attempt.

        Returns:
            A :class:`~bot.domain.models.SubmitResult` with ``ok=True`` and the
            created document name on success.

        Raises:
            FrappePermissionError: 403 or PermissionError from Frappe.
            FrappeNotFound: 404 or DoesNotExistError from Frappe.
            FrappeValidationError: 400/417 or ValidationError from Frappe.
                Callers should check ``exc.indicates_already_completed()`` and
                ``exc.indicates_payload_incomplete()`` to distinguish scenarios
                (Requirements 8.8, 8.9).
            FrappeUnavailable: 5xx or network error.
        """
        url = f"{self._base_url}{_SUBMIT_PATH}"
        headers = {
            **self._auth_header(),
            "X-Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        }

        body = payload.model_dump(mode="json")

        logger.info(
            "submit_payload",
            extra={
                "motor_tarikan": payload.motor_tarikan,
                "telegram_id": payload.telegram_id,
                "tipe_inspeksi": payload.tipe_inspeksi,
                "komponen_count": len(payload.komponen),
                "foto_urls_count": len(payload.foto_urls),
                "komponen_keys": sorted(payload.komponen.keys()),
            },
        )

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, headers=headers, json=body) as response:
                    if response.status != 200:
                        await self._raise_for_frappe_error(response)

                    data = await response.json(content_type=None)
        except (FrappePermissionError, FrappeNotFound, FrappeValidationError, FrappeUnavailable):
            raise
        except aiohttp.ClientError as exc:
            raise FrappeUnavailable(f"Network error during submit: {exc}") from exc
        except Exception as exc:
            raise FrappeUnavailable(f"Unexpected error during submit: {exc}") from exc

        # Response shape: {"message": {"ok": true, "name": "HI-PJ-001-0001", ...}}
        message_body = data.get("message", {})
        if isinstance(message_body, dict):
            ok = message_body.get("ok", False)
            name = message_body.get("name")
            return SubmitResult(ok=bool(ok), name=name)

        raise FrappeUnavailable(f"Unexpected submit response shape: {data!r}")
