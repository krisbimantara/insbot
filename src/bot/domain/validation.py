"""Pre-submit validation for inspection sessions.

Pure functions — no I/O. Validates that all mandatory fields are filled with
valid option values and all 10 photos have a file_id before the session is
submitted to Frappe.
"""

from __future__ import annotations

from .models import (
    COMPONENT_OPTIONS,
    MANDATORY_FIELDS,
    PHOTO_FIELDS,
    Session,
    ValidationError,
)


def validate_pre_submit(session: Session) -> list[ValidationError]:
    """Validate that a session is ready to submit.

    Checks:
    1. All 66 mandatory component fields are present and have a value that
       belongs to the field's defined option set.
    2. All 10 photo fields have a non-empty ``file_id`` stored in
       ``session.photos``.

    Returns:
        A list of :class:`ValidationError` instances describing every
        problem found.  An empty list means the session is valid and ready
        to submit.

    This function is a pure function — it performs no I/O and does not
    mutate the session.

    Requirements: 8.1, 8.2
    """
    errors: list[ValidationError] = []

    # --- 1. Mandatory component fields ---
    for field in MANDATORY_FIELDS:
        value = session.answers.get(field)

        if value is None:
            errors.append(
                ValidationError(
                    field=field,
                    reason="missing",
                    message=f"Field '{field}' belum diisi",
                )
            )
            continue

        valid_options = COMPONENT_OPTIONS[field]
        if value not in valid_options:
            errors.append(
                ValidationError(
                    field=field,
                    reason="invalid_value",
                    message=f"Nilai '{value}' tidak valid untuk '{field}'",
                )
            )

    # --- 2. Photo fields ---
    for field in PHOTO_FIELDS:
        file_id = session.photos.get(field)
        if not file_id:
            errors.append(
                ValidationError(
                    field=field,
                    reason="missing_photo",
                    message=f"Foto '{field}' belum diambil",
                )
            )

    return errors
