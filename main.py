from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from core.config import load_config
from core.db import open_db
from core.fsm_storage import build_fsm_event_isolation, build_fsm_storage
from core.healthcheck import start_healthcheck_server
from core.logging_setup import configure_logging
from core.scheduler import scheduler_loop
from core.state import StateStore
from core.webapp import start_webapp_server
from telegram.admin import build_admin_router
from telegram.router import build_router


async def amain() -> None:
    cfg = load_config()
    configure_logging(cfg.log_level)

    db_path = Path(cfg.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await open_db(str(db_path))
    store = StateStore(conn)
    await store.migrate()

    storage = build_fsm_storage(cfg)
    dp = Dispatcher(storage=storage, events_isolation=build_fsm_event_isolation(storage))
    dp.include_router(build_router(store=store))
    dp.include_router(
        build_admin_router(store=store, admin_ids=cfg.admin_ids, webapp_url=cfg.webapp_url)
    )

    session = AiohttpSession(timeout=cfg.telegram_http_timeout_seconds)
    bot = Bot(token=cfg.bot_token, session=session)

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(
        scheduler_loop(bot=bot, store=store, stop_event=stop_event, poll_interval_seconds=cfg.scheduler_poll_seconds)
    )
    healthcheck_server = None
    webapp_server = None

    try:
        healthcheck_server = await start_healthcheck_server(
            host=cfg.healthcheck_host,
            port=cfg.healthcheck_port,
            conn=conn,
            scheduler_task=scheduler_task,
        )
        if cfg.webapp_url:
            webapp_server = await start_webapp_server(
                host=cfg.webapp_host,
                port=cfg.webapp_port,
                store=store,
                bot_token=cfg.bot_token,
                admin_ids=cfg.admin_ids,
                bot=bot,
            )
        await dp.start_polling(bot, polling_timeout=cfg.telegram_polling_timeout_seconds)
    finally:
        stop_event.set()
        if webapp_server is not None:
            await webapp_server.close()
        if healthcheck_server is not None:
            await healthcheck_server.close()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        await conn.close()


if __name__ == "__main__":
    asyncio.run(amain())
