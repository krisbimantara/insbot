"""Progress computation for the Telegram Inspection Bot domain layer.

All functions are pure (no I/O). They compute and render inspection progress
based on the current session state.
"""

from __future__ import annotations

from .models import (
    CATEGORIES,
    CATEGORY_FIELDS,
    MANDATORY_FIELDS,
    CategoryProgress,
    Session,
)


def compute_progress(session: Session) -> tuple[CategoryProgress, ...]:
    """Compute per-category progress from the current session answers.

    For each category in CATEGORIES:
    - total = number of fields in CATEGORY_FIELDS[category]
    - done  = number of those fields where session.answers.get(field) is not None

    Returns a tuple of CategoryProgress in CATEGORIES order.
    """
    result: list[CategoryProgress] = []
    for category in CATEGORIES:
        fields = CATEGORY_FIELDS[category]
        total = len(fields)
        done = sum(
            1
            for field in fields
            if session.answers.get(field) is not None
        )
        result.append(CategoryProgress(name=category, done=done, total=total))
    return tuple(result)


def render_progress_bar(done: int, total: int, width: int = 10) -> str:
    """Render a Unicode progress bar string.

    Format: ``[████████░░] {done}/{total}``

    Filled blocks use U+2588 (█), empty blocks use U+2591 (░).
    The number of filled blocks is ``round(done / total * width)`` when
    ``total > 0``, otherwise 0.

    Examples::

        render_progress_bar(8, 10)       → "[████████░░] 8/10"
        render_progress_bar(0, 0)        → "[░░░░░░░░░░] 0/0"
        render_progress_bar(10, 10)      → "[██████████] 10/10"
        render_progress_bar(0, 10)       → "[░░░░░░░░░░] 0/10"
    """
    if total > 0:
        filled = round(done / total * width)
    else:
        filled = 0

    bar = "\u2588" * filled + "\u2591" * (width - filled)
    return f"[{bar}] {done}/{total}"


def compute_overall_progress(session: Session) -> tuple[int, int]:
    """Compute overall progress across all 66 mandatory fields.

    Returns ``(done, total)`` where:
    - total is always 66
    - done is the count of MANDATORY_FIELDS where session.answers.get(field)
      is not None
    """
    total = 66
    done = sum(
        1
        for field in MANDATORY_FIELDS
        if session.answers.get(field) is not None
    )
    return done, total
