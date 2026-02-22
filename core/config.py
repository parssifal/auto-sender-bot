from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str
    log_level: str = "INFO"
    scheduler_poll_seconds: float = 2.0


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
            raise RuntimeError(f"Invalid {env_path} line {line_no}: {raw_line!r}")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise RuntimeError(f"Invalid {env_path} line {line_no}: empty key")

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if key not in os.environ:
            os.environ[key] = value


def load_config() -> Config:
    _load_dotenv()

    bot_token = os.getenv("BOT_TOKEN") or os.getenv("TG_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN env var is required (or TG_TOKEN)")

    db_path = os.getenv("DB_PATH", "data/bot.db")
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    poll_seconds_raw = os.getenv("SCHEDULER_POLL_SECONDS")
    scheduler_poll_seconds = 2.0
    if poll_seconds_raw:
        try:
            scheduler_poll_seconds = float(poll_seconds_raw)
        except ValueError as exc:
            raise RuntimeError("SCHEDULER_POLL_SECONDS must be a number") from exc

    return Config(
        bot_token=bot_token,
        db_path=db_path,
        log_level=log_level,
        scheduler_poll_seconds=scheduler_poll_seconds,
    )
