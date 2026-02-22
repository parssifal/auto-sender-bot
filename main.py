from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import load_config
from core.db import open_db
from core.scheduler import scheduler_loop
from core.state import StateStore
from telegram.router import build_router


async def amain() -> None:
    cfg = load_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO))

    db_path = Path(cfg.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await open_db(str(db_path))
    store = StateStore(conn)
    await store.migrate()

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(build_router(store=store))

    bot = Bot(token=cfg.bot_token)

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(
        scheduler_loop(bot=bot, store=store, stop_event=stop_event, poll_interval_seconds=cfg.scheduler_poll_seconds)
    )

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except Exception:
            pass
        await conn.close()


if __name__ == "__main__":
    asyncio.run(amain())
