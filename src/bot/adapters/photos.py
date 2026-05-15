"""Photo adapter — Telegram photo download and compression.

Provides two public functions:

* :func:`download_telegram_photo` — async, downloads raw bytes from Telegram
  using an :class:`aiogram.Bot` instance.
* :func:`compress_if_needed` — pure synchronous function that downscales and
  re-encodes an image as JPEG if it exceeds ``max_bytes``.
* :func:`get_photo_filename` — pure helper that returns the upload filename.

Requirement 6.8: IF a photo exceeds 5 MB after download, THE Bot SHALL
compress it (downscale + JPEG quality reduction) until ≤ 5 MB before
uploading to Frappe.
"""

from __future__ import annotations

import io

from PIL import Image


# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------


def get_photo_filename(field_name: str, motor_id: str) -> str:
    """Return the filename for a photo upload: ``{field_name}_{motor_id}.jpg``."""
    return f"{field_name}_{motor_id}.jpg"


# ---------------------------------------------------------------------------
# Telegram download
# ---------------------------------------------------------------------------


async def download_telegram_photo(bot: "Bot", file_id: str) -> bytes:  # type: ignore[name-defined]
    """Download a Telegram file and return its raw bytes.

    Parameters
    ----------
    bot:
        An :class:`aiogram.Bot` instance (injected by the caller so this
        module does not hold a global reference).
    file_id:
        The Telegram ``file_id`` stored in ``session.photos[field]``.

    Returns
    -------
    bytes
        Raw file bytes as downloaded from Telegram's CDN.
    """
    file = await bot.get_file(file_id)
    file_path: str = file.file_path  # type: ignore[union-attr]

    # bot.download_file returns a BytesIO-like object
    result = await bot.download_file(file_path)

    if isinstance(result, (bytes, bytearray)):
        return bytes(result)

    # aiogram returns a BytesIO; read it fully
    return result.read()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

_QUALITY_STEPS: tuple[int, ...] = (90, 75, 60)


def compress_if_needed(
    image_bytes: bytes,
    *,
    max_bytes: int,
    longest_edge: int,
) -> bytes:
    """Return a compressed JPEG if ``image_bytes`` exceeds ``max_bytes``.

    This is a **pure function**: given identical inputs it always produces
    identical outputs (deterministic).

    Algorithm
    ---------
    1. If ``len(image_bytes) <= max_bytes``, return *image_bytes* unchanged
       (identity — no re-encoding).
    2. Open the image with Pillow.
    3. Convert to RGB (handles RGBA, palette, greyscale, etc.).
    4. If the longest dimension exceeds ``longest_edge``, downscale so that
       the longest edge equals ``longest_edge`` while preserving aspect ratio
       (``Image.LANCZOS`` resampling).
    5. Try JPEG quality steps 90 → 75 → 60.  For each quality, encode to
       JPEG and check if the result fits within ``max_bytes``.
    6. Return the first encoding that fits.
    7. If even quality=60 does not fit, return the quality=60 encoding anyway
       (best-effort — the caller is responsible for deciding what to do).

    Parameters
    ----------
    image_bytes:
        Raw image data (any format Pillow can open: JPEG, PNG, WEBP, …).
    max_bytes:
        Size threshold in bytes.  Images at or below this size are returned
        unchanged.
    longest_edge:
        Maximum pixel length of the longest dimension after downscaling.

    Returns
    -------
    bytes
        Either the original bytes (if already small enough) or a JPEG-encoded
        compressed version.
    """
    if len(image_bytes) <= max_bytes:
        return image_bytes

    img = Image.open(io.BytesIO(image_bytes))

    # Always work in RGB for JPEG compatibility
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Downscale if the longest dimension exceeds the target
    width, height = img.size
    if max(width, height) > longest_edge:
        if width >= height:
            new_width = longest_edge
            new_height = round(height * longest_edge / width)
        else:
            new_height = longest_edge
            new_width = round(width * longest_edge / height)
        img = img.resize((new_width, new_height), Image.LANCZOS)

    # Quality stepping: try each quality level and return the first that fits
    last_encoded: bytes | None = None
    for quality in _QUALITY_STEPS:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        encoded = buf.getvalue()
        last_encoded = encoded
        if len(encoded) <= max_bytes:
            return encoded

    # Best-effort: return quality=60 encoding even if still over max_bytes
    assert last_encoded is not None  # _QUALITY_STEPS is non-empty
    return last_encoded
