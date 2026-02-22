from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str
    log_level: str = "INFO"
    scheduler_poll_seconds: float = 2.0


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN env var is required")

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
