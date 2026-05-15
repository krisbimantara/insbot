"""
Unit tests for src/bot/config.py.

Tests cover:
- Settings loads correctly from env vars
- Required fields cause ValidationError when missing
- HTTPS warning is emitted for non-HTTPS frappe_url
- webhook_shared_secret warning when not set
- SecretStr fields are not exposed in repr/str
- get_settings() returns a cached singleton
"""

from __future__ import annotations

import importlib
import warnings

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_ENV = {
    "FRAPPE_URL": "https://erp.example.com",
    "FRAPPE_API_KEY": "key123",
    "FRAPPE_API_SECRET": "secret456",
    "TELEGRAM_BOT_TOKEN": "123456789:ABCdef",
    "REDIS_URL": "redis://localhost:6379/0",
}


def make_settings(overrides: dict | None = None, **kwargs):
    """Import Settings fresh (bypassing lru_cache) and instantiate with given env."""
    from bot.config import Settings

    env = {**MINIMAL_ENV, **(overrides or {}), **kwargs}
    return Settings(**{k.lower(): v for k, v in env.items()})


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------


class TestSettingsLoading:
    def test_loads_required_fields(self):
        s = make_settings()
        assert str(s.frappe_url).rstrip("/") == "https://erp.example.com"
        assert s.frappe_api_key.get_secret_value() == "key123"
        assert s.frappe_api_secret.get_secret_value() == "secret456"
        assert s.telegram_bot_token.get_secret_value() == "123456789:ABCdef"
        assert s.redis_url == "redis://localhost:6379/0"

    def test_default_values(self):
        s = make_settings()
        assert s.redis_ttl == 86400
        assert s.webhook_host == "0.0.0.0"
        assert s.webhook_port == 8443
        assert s.webhook_shared_secret is None
        assert s.auth_cache_ttl_seconds == 60
        assert s.frappe_request_timeout_seconds == 30
        assert s.photo_max_bytes == 5 * 1024 * 1024
        assert s.photo_compress_target_longest_edge == 1920
        assert s.log_level == "INFO"

    def test_optional_overrides(self):
        s = make_settings(
            REDIS_TTL="3600",
            WEBHOOK_PORT="9000",
            LOG_LEVEL="DEBUG",
            WEBHOOK_SHARED_SECRET="mysecret",
        )
        assert s.redis_ttl == 3600
        assert s.webhook_port == 9000
        assert s.log_level == "DEBUG"
        assert s.webhook_shared_secret is not None
        assert s.webhook_shared_secret.get_secret_value() == "mysecret"


# ---------------------------------------------------------------------------
# Fail-fast on missing required fields (Requirement 12.2)
# ---------------------------------------------------------------------------


class TestFailFastOnMissingFields:
    @pytest.mark.parametrize(
        "missing_field",
        [
            "FRAPPE_URL",
            "FRAPPE_API_KEY",
            "FRAPPE_API_SECRET",
            "TELEGRAM_BOT_TOKEN",
            "REDIS_URL",
        ],
    )
    def test_missing_required_field_raises(self, missing_field):
        env = {k: v for k, v in MINIMAL_ENV.items() if k != missing_field}
        from bot.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(**{k.lower(): v for k, v in env.items()})

        # The error message should mention the field name, not a secret value
        error_text = str(exc_info.value)
        assert missing_field.lower() in error_text.lower()

    def test_error_message_does_not_contain_secret_values(self):
        """Requirement 12.3: error messages must not leak secret values."""
        from bot.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(
                frappe_url="https://erp.example.com",
                # missing api_key, api_secret, token, redis_url
            )

        error_text = str(exc_info.value)
        # None of the actual secret values from MINIMAL_ENV should appear
        for secret in ("key123", "secret456", "123456789:ABCdef"):
            assert secret not in error_text


# ---------------------------------------------------------------------------
# SecretStr — values not exposed in repr/str (Requirement 12.3)
# ---------------------------------------------------------------------------


class TestSecretStrNotExposed:
    def test_api_key_not_in_repr(self):
        s = make_settings()
        assert "key123" not in repr(s)
        assert "key123" not in str(s)

    def test_api_secret_not_in_repr(self):
        s = make_settings()
        assert "secret456" not in repr(s)

    def test_bot_token_not_in_repr(self):
        s = make_settings()
        assert "123456789:ABCdef" not in repr(s)

    def test_webhook_secret_not_in_repr(self):
        s = make_settings(WEBHOOK_SHARED_SECRET="topsecret")
        assert "topsecret" not in repr(s)


# ---------------------------------------------------------------------------
# HTTPS warning (Requirement 12.5)
# ---------------------------------------------------------------------------


class TestHttpsWarning:
    def test_https_url_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_settings(FRAPPE_URL="https://erp.example.com")

        https_warnings = [w for w in caught if "non_https" in str(w.message).lower() or "not 'https'" in str(w.message).lower() or "NOT encrypted" in str(w.message)]
        assert len(https_warnings) == 0

    def test_http_url_emits_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_settings(FRAPPE_URL="http://erp.example.com")

        scheme_warnings = [w for w in caught if "NOT encrypted" in str(w.message) or "not 'https'" in str(w.message)]
        assert len(scheme_warnings) >= 1

    def test_warning_message_does_not_contain_url_credentials(self):
        """Warning must not leak any secret values."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_settings(FRAPPE_URL="http://user:pass@erp.example.com")

        for w in caught:
            assert "pass" not in str(w.message)


# ---------------------------------------------------------------------------
# Webhook shared secret warning (Requirement 12.6)
# ---------------------------------------------------------------------------


class TestWebhookSecretWarning:
    def test_no_webhook_secret_emits_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_settings()  # webhook_shared_secret defaults to None

        secret_warnings = [w for w in caught if "WEBHOOK_SHARED_SECRET" in str(w.message)]
        assert len(secret_warnings) >= 1

    def test_webhook_secret_set_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_settings(WEBHOOK_SHARED_SECRET="strongsecret")

        secret_warnings = [w for w in caught if "WEBHOOK_SHARED_SECRET" in str(w.message)]
        assert len(secret_warnings) == 0


# ---------------------------------------------------------------------------
# get_settings() singleton / caching
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_get_settings_returns_settings_instance(self, monkeypatch):
        """get_settings() should return a Settings instance when env is valid."""
        import os

        for k, v in MINIMAL_ENV.items():
            monkeypatch.setenv(k, v)

        # Clear the lru_cache so we get a fresh call
        import bot.config as config_module

        config_module.get_settings.cache_clear()

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            s = config_module.get_settings()

        from bot.config import Settings

        assert isinstance(s, Settings)

    def test_get_settings_is_cached(self, monkeypatch):
        """Calling get_settings() twice returns the same object."""
        import os

        for k, v in MINIMAL_ENV.items():
            monkeypatch.setenv(k, v)

        import bot.config as config_module

        config_module.get_settings.cache_clear()

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            s1 = config_module.get_settings()
            s2 = config_module.get_settings()

        assert s1 is s2
