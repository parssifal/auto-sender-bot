from __future__ import annotations

import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import Config
from core.fsm_storage import build_fsm_event_isolation, build_fsm_storage


def test_build_fsm_storage_defaults_to_memory_storage() -> None:
    storage = build_fsm_storage(Config(bot_token="token", db_path="data/bot.db"))

    assert isinstance(storage, MemoryStorage)
    assert build_fsm_event_isolation(storage) is None


@pytest.mark.asyncio
async def test_build_fsm_storage_uses_redis_storage_when_url_is_configured() -> None:
    from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage

    storage = build_fsm_storage(
        Config(
            bot_token="token",
            db_path="data/bot.db",
            redis_url="redis://localhost:6379/0",
        )
    )

    try:
        assert isinstance(storage, RedisStorage)
        assert isinstance(build_fsm_event_isolation(storage), RedisEventIsolation)
    finally:
        await storage.close()


def test_build_fsm_storage_raises_clear_error_without_redis_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.fsm_storage as fsm_storage_module

    real_import_module = fsm_storage_module.importlib.import_module

    def fake_import_module(name: str):
        if name == "aiogram.fsm.storage.redis":
            raise ModuleNotFoundError("No module named 'redis'", name="redis")
        return real_import_module(name)

    monkeypatch.setattr(fsm_storage_module.importlib, "import_module", fake_import_module)

    with pytest.raises(RuntimeError, match="REDIS_URL is configured but the 'redis' package is not installed"):
        build_fsm_storage(
            Config(
                bot_token="token",
                db_path="data/bot.db",
                redis_url="redis://localhost:6379/0",
            )
        )
