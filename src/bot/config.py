"""
Configuration module for Telegram Inspection Bot.

Reads all settings from environment variables (and optionally a .env file).
Fails fast on startup if any required variable is missing — without logging
secret values (Requirement 12.1, 12.2, 12.3, 12.5).
"""

from __future__ import annotations

import logging
import warnings
from functools import lru_cache
from typing import Any

from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Required fields (no default) will cause a ValidationError — and therefore
    a non-zero exit — if the corresponding env var is absent (Requirement 12.2).
    Secret fields use SecretStr so their values are never accidentally logged
    or repr'd (Requirement 12.3).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Extra env vars are silently ignored so the container can carry
        # unrelated variables without breaking startup.
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Frappe connection (REQUIRED)
    # ------------------------------------------------------------------
    frappe_url: HttpUrl
    frappe_api_key: SecretStr
    frappe_api_secret: SecretStr

    # ------------------------------------------------------------------
    # Telegram (REQUIRED)
    # ------------------------------------------------------------------
    telegram_bot_token: SecretStr

    # ------------------------------------------------------------------
    # Redis (REQUIRED)
    # ------------------------------------------------------------------
    redis_url: str
    redis_ttl: int = 86400  # seconds — 24 hours

    # ------------------------------------------------------------------
    # Webhook server (REQUIRED host/port; secret is optional)
    # ------------------------------------------------------------------
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8443
    # If not set, the bot logs a WARNING and accepts all requests (dev mode).
    webhook_shared_secret: SecretStr | None = None

    # ------------------------------------------------------------------
    # Optional tuning
    # ------------------------------------------------------------------
    auth_cache_ttl_seconds: int = 60
    frappe_request_timeout_seconds: int = 30
    photo_max_bytes: int = 5 * 1024 * 1024  # 5 MB
    photo_compress_target_longest_edge: int = 1920
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Post-init checks (Requirement 12.5, 12.6)
    # ------------------------------------------------------------------
    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        """Warn on non-HTTPS Frappe URL and missing webhook shared secret."""
        scheme = self.frappe_url.scheme if self.frappe_url else ""
        if scheme != "https":
            warnings.warn(
                f"FRAPPE_URL scheme is '{scheme}', not 'https'. "
                "Communication with Frappe is NOT encrypted. "
                "This is only acceptable in local development.",
                stacklevel=2,
            )
            logger.warning(
                "frappe_url_non_https",
                extra={"scheme": scheme},
            )

        if self.webhook_shared_secret is None:
            warnings.warn(
                "WEBHOOK_SHARED_SECRET is not set. "
                "The /webhook/inspection-request endpoint will accept ALL requests. "
                "Set this variable in production.",
                stacklevel=2,
            )
            logger.warning("webhook_shared_secret_not_set")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton.

    Raises ``pydantic_settings.ValidationError`` (which is a subclass of
    ``ValueError``) on the first call if any required env var is missing.
    The error message names the missing field(s) but never includes secret
    values (Requirement 12.2, 12.3).
    """
    return Settings()
