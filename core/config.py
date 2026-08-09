from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str
    redis_url: str | None = None
    healthcheck_host: str = "127.0.0.1"
    healthcheck_port: int = 8080
    log_level: str = "INFO"
    scheduler_poll_seconds: float = 2.0
    telegram_polling_timeout_seconds: int = 30
    telegram_http_timeout_seconds: float = 75.0
    admin_ids: tuple[int, ...] = ()
    webapp_url: str | None = None
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8081


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line_no, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()

        if "=" not in line:
            raise RuntimeError(f"Invalid {env_path} line {line_no}: missing '='")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise RuntimeError(f"Invalid {env_path} line {line_no}: empty key")

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if key not in os.environ:
            os.environ[key] = value


def _env_num(name: str, default: float, cast):
    """Read a positive int/float env var, falling back to ``default``.

    Folds the getenv -> cast -> raise -> ``> 0`` check that five settings shared
    verbatim (and where the SCHEDULER_POLL_SECONDS ``> 0`` guard once went missing).
    """
    raw = os.getenv(name)
    if not raw:
        value = default
    else:
        try:
            value = cast(raw)
        except ValueError as exc:
            kind = "an integer" if cast is int else "a number"
            raise RuntimeError(f"{name} must be {kind}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be > 0")
    return value


def load_config() -> Config:
    _load_dotenv()

    bot_token = os.getenv("BOT_TOKEN") or os.getenv("TG_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN env var is required (or TG_TOKEN)")

    db_path = os.getenv("DB_PATH", "data/bot.db")
    redis_url = os.getenv("REDIS_URL")
    if redis_url is not None:
        redis_url = redis_url.strip() or None
    healthcheck_host = os.getenv("HEALTHCHECK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    healthcheck_port = _env_num("HEALTHCHECK_PORT", 8080, int)
    scheduler_poll_seconds = _env_num("SCHEDULER_POLL_SECONDS", 2.0, float)
    telegram_polling_timeout_seconds = _env_num("TELEGRAM_POLLING_TIMEOUT_SECONDS", 30, int)
    telegram_http_timeout_seconds = _env_num("TELEGRAM_HTTP_TIMEOUT_SECONDS", 75.0, float)

    admin_ids_raw = os.getenv("ADMIN_IDS") or os.getenv("BOT_ADMIN_IDS") or ""
    admin_ids: list[int] = []
    for token in admin_ids_raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            admin_ids.append(int(token))
        except ValueError as exc:
            raise RuntimeError(f"ADMIN_IDS must be comma-separated integers, got {token!r}") from exc

    webapp_url = os.getenv("WEBAPP_URL")
    if webapp_url is not None:
        webapp_url = webapp_url.strip() or None
    webapp_host = os.getenv("WEBAPP_HOST", "0.0.0.0").strip() or "0.0.0.0"

    webapp_port = _env_num("WEBAPP_PORT", 8081, int)

    return Config(
        bot_token=bot_token,
        db_path=db_path,
        redis_url=redis_url,
        healthcheck_host=healthcheck_host,
        healthcheck_port=healthcheck_port,
        log_level=log_level,
        scheduler_poll_seconds=scheduler_poll_seconds,
        telegram_polling_timeout_seconds=telegram_polling_timeout_seconds,
        telegram_http_timeout_seconds=telegram_http_timeout_seconds,
        admin_ids=tuple(admin_ids),
        webapp_url=webapp_url,
        webapp_host=webapp_host,
        webapp_port=webapp_port,
    )
