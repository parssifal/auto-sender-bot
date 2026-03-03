from __future__ import annotations

import importlib
import logging

from aiogram.fsm.storage.base import BaseEventIsolation, BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import Config

logger = logging.getLogger(__name__)


def build_fsm_storage(cfg: Config) -> BaseStorage:
    if not cfg.redis_url:
        logger.info("Using in-memory FSM storage")
        return MemoryStorage()

    storage = _build_redis_storage(cfg.redis_url)
    logger.info("Using Redis FSM storage")
    return storage


def build_fsm_event_isolation(storage: BaseStorage) -> BaseEventIsolation | None:
    create_isolation = getattr(storage, "create_isolation", None)
    if create_isolation is None:
        return None
    return create_isolation()


def _build_redis_storage(redis_url: str) -> BaseStorage:
    try:
        redis_storage_module = importlib.import_module("aiogram.fsm.storage.redis")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("redis"):
            raise RuntimeError(
                "REDIS_URL is configured but the 'redis' package is not installed. "
                "Install dependencies from requirements.txt."
            ) from exc
        raise

    redis_storage_cls = redis_storage_module.RedisStorage
    return redis_storage_cls.from_url(redis_url)
