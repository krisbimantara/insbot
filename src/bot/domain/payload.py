"""Payload builder for the Telegram Inspection Bot domain layer.

Pure functions — no I/O. Both functions are deterministic: identical inputs
always produce identical outputs regardless of when or how many times they
are called.

Requirements: 14.1, 14.2, 14.3, 14.4, 8.4, 8.7
"""

from __future__ import annotations

from .models import (
    MANDATORY_FIELDS,
    PHOTO_FIELDS,
    STNK_CONDITIONAL_BY_ANSWER,
    Session,
    SubmitPayload,
)


def build_submit_payload(
    session: Session,
    foto_urls: dict[str, str],
) -> SubmitPayload:
    """Build the body for ``submit_hasil_inspeksi``.

    Parameters
    ----------
    session:
        The completed inspection session.  ``session.answers`` must contain
        valid values for all 66 mandatory fields.
    foto_urls:
        Mapping of photo field name → uploaded file URL.  Must contain
        exactly 10 entries keyed by :data:`PHOTO_FIELDS`.

    Returns
    -------
    SubmitPayload
        A fully-formed payload ready to be sent to Frappe.

    Raises
    ------
    ValueError
        If ``foto_urls`` does not contain exactly the 10 expected keys.

    Notes
    -----
    ``komponen`` construction (Requirement 14.3):

    1. Start with all 66 mandatory fields from ``session.answers``.
    2. If ``stnk_answer`` is ``"Cukup"`` or ``"Rusak"``, include each
       conditional STNK field whose value is non-null.
    3. If ``stnk_answer`` is ``"Baik"`` (or ``None``), no conditional fields
       are included regardless of what may be stored in ``answers``.
    """
    # Validate foto_urls keys (Requirement 14.4)
    expected_photo_keys = set(PHOTO_FIELDS)
    actual_photo_keys = set(foto_urls.keys())
    if actual_photo_keys != expected_photo_keys:
        missing = expected_photo_keys - actual_photo_keys
        extra = actual_photo_keys - expected_photo_keys
        parts: list[str] = []
        if missing:
            parts.append(f"missing keys: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected keys: {sorted(extra)}")
        raise ValueError(
            f"foto_urls must have exactly 10 entries keyed by PHOTO_FIELDS; {'; '.join(parts)}"
        )

    answers = session.answers

    # Step 1: 66 mandatory fields (Requirement 14.1)
    komponen: dict[str, str] = {}
    for field in MANDATORY_FIELDS:
        value = answers.get(field)
        if value is not None:
            komponen[field] = value

    # Step 2: conditional STNK fields (Requirement 14.3 / 5.7)
    stnk_answer = session.stnk_answer
    if stnk_answer in ("Cukup", "Rusak"):
        for field in STNK_CONDITIONAL_BY_ANSWER[stnk_answer]:
            value = answers.get(field)
            if value is not None:
                komponen[field] = value
    # stnk_answer == "Baik" or None → no conditional fields included

    return SubmitPayload(
        motor_tarikan=session.motor_id,
        telegram_id=session.telegram_id,
        tipe_inspeksi=session.tipe_inspeksi,
        komponen=komponen,
        foto_urls=dict(foto_urls),  # defensive copy
        catatan=getattr(session, "catatan", None),
    )


def build_idempotency_key(session: Session) -> str:
    """Build a deterministic idempotency key for a submit attempt.

    Format: ``{telegram_id}:{motor_tarikan}:{session_started_at}``

    The key is stable across retries of the same submit because it depends
    only on ``telegram_id``, ``motor_id`` (motor_tarikan), and
    ``started_at`` — none of which change during a single inspection session.

    Parameters
    ----------
    session:
        The inspection session.

    Returns
    -------
    str
        Idempotency key string.  If ``started_at`` is not set, the literal
        string ``"unknown"`` is used in its place so the key is always
        well-formed (Requirement 8.7).
    """
    started_at_str = (
        session.started_at.isoformat() if session.started_at is not None else "unknown"
    )
    return f"{session.telegram_id}:{session.motor_id}:{started_at_str}"
