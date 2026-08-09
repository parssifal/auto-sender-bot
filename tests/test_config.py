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


def test_load_dotenv_invalid_line_does_not_leak_secret(tmp_path) -> None:
    secret = "1234567890:AAHfakebottokenSECRETdonotleak"
    env_file = tmp_path / ".env"
    env_file.write_text(secret + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as excinfo:
        config_module._load_dotenv(str(env_file))

    assert "line 1" in str(excinfo.value)
    assert secret not in str(excinfo.value)


def test_load_config_rejects_non_positive_scheduler_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("SCHEDULER_POLL_SECONDS", "0")

    with pytest.raises(RuntimeError, match="SCHEDULER_POLL_SECONDS must be > 0"):
        config_module.load_config()


def test_load_config_parses_scheduler_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("SCHEDULER_POLL_SECONDS", "1.5")

    cfg = config_module.load_config()

    assert cfg.scheduler_poll_seconds == 1.5


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


def test_load_config_admin_and_webapp_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.delenv("WEBAPP_HOST", raising=False)
    monkeypatch.delenv("WEBAPP_PORT", raising=False)

    cfg = config_module.load_config()

    assert cfg.admin_ids == ()
    assert cfg.webapp_url is None
    assert cfg.webapp_host == "0.0.0.0"
    assert cfg.webapp_port == 8081


def test_load_config_parses_admin_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_IDS", " 123, 456 ,789,")

    cfg = config_module.load_config()

    assert cfg.admin_ids == (123, 456, 789)


def test_load_config_accepts_bot_admin_ids_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.setenv("BOT_ADMIN_IDS", "410680038")

    cfg = config_module.load_config()

    assert cfg.admin_ids == (410680038,)


def test_load_config_prefers_admin_ids_over_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.setenv("BOT_ADMIN_IDS", "2")

    cfg = config_module.load_config()

    assert cfg.admin_ids == (1,)


def test_load_config_rejects_non_integer_admin_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_IDS", "123,abc")

    with pytest.raises(RuntimeError, match="ADMIN_IDS"):
        config_module.load_config()


def test_load_config_parses_webapp_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("WEBAPP_URL", "https://example.com/app")
    monkeypatch.setenv("WEBAPP_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBAPP_PORT", "9000")

    cfg = config_module.load_config()

    assert cfg.webapp_url == "https://example.com/app"
    assert cfg.webapp_host == "127.0.0.1"
    assert cfg.webapp_port == 9000


def test_load_config_treats_blank_webapp_url_as_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("WEBAPP_URL", "   ")

    cfg = config_module.load_config()

    assert cfg.webapp_url is None
