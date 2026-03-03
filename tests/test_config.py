import pytest

from core import config as config_module


def _disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_load_dotenv", lambda path=".env": None)


def test_load_config_uses_telegram_timeout_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.delenv("TELEGRAM_POLLING_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TELEGRAM_HTTP_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("HEALTHCHECK_HOST", raising=False)
    monkeypatch.delenv("HEALTHCHECK_PORT", raising=False)

    cfg = config_module.load_config()

    assert cfg.telegram_polling_timeout_seconds == 30
    assert cfg.telegram_http_timeout_seconds == 75.0
    assert cfg.healthcheck_host == "127.0.0.1"
    assert cfg.healthcheck_port == 8080


def test_load_config_parses_telegram_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_POLLING_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("TELEGRAM_HTTP_TIMEOUT_SECONDS", "120.5")
    monkeypatch.setenv("HEALTHCHECK_HOST", "0.0.0.0")
    monkeypatch.setenv("HEALTHCHECK_PORT", "18080")

    cfg = config_module.load_config()

    assert cfg.telegram_polling_timeout_seconds == 45
    assert cfg.telegram_http_timeout_seconds == 120.5
    assert cfg.healthcheck_host == "0.0.0.0"
    assert cfg.healthcheck_port == 18080


def test_load_config_rejects_non_positive_polling_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_POLLING_TIMEOUT_SECONDS", "0")

    with pytest.raises(RuntimeError, match="TELEGRAM_POLLING_TIMEOUT_SECONDS must be > 0"):
        config_module.load_config()


def test_load_config_rejects_non_positive_healthcheck_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("HEALTHCHECK_PORT", "0")

    with pytest.raises(RuntimeError, match="HEALTHCHECK_PORT must be > 0"):
        config_module.load_config()


def test_load_config_parses_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")

    cfg = config_module.load_config()

    assert cfg.redis_url == "redis://redis:6379/0"


def test_load_config_treats_blank_redis_url_as_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("REDIS_URL", "   ")

    cfg = config_module.load_config()

    assert cfg.redis_url is None
