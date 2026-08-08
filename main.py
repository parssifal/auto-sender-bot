from __future__ import annotations

import asyncio
import logging
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
from telegram.menu_button import set_default_menu_button
from telegram.router import build_router
from telegram.user_app import build_user_app_router

logger = logging.getLogger(__name__)


async def _supervise(scheduler_task: asyncio.Task, polling_task: asyncio.Task) -> None:
    """Return once either task ends; raise if the scheduler died.

    A crashed scheduler with live polling means the bot keeps accepting posts nobody
    sends. Surfacing the crash as a raise exits the process so ``restart: unless-stopped``
    revives it (an unhealthy /healthz alone does not trigger a restart).
    """
    done, _ = await asyncio.wait({scheduler_task, polling_task}, return_when=asyncio.FIRST_COMPLETED)
    if scheduler_task in done and not scheduler_task.cancelled():
        exc = scheduler_task.exception()
        if exc is not None:
            logger.error("Scheduler task crashed; stopping process for restart", exc_info=exc)
            raise RuntimeError("Scheduler task crashed") from exc


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
    dp.include_router(build_router(store=store, webapp_url=cfg.webapp_url))
    dp.include_router(
        build_admin_router(store=store, admin_ids=cfg.admin_ids, webapp_url=cfg.webapp_url)
    )
    dp.include_router(build_user_app_router(store=store, webapp_url=cfg.webapp_url))

    session = AiohttpSession(timeout=cfg.telegram_http_timeout_seconds)
    bot = Bot(token=cfg.bot_token, session=session)

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(
        scheduler_loop(bot=bot, store=store, stop_event=stop_event, poll_interval_seconds=cfg.scheduler_poll_seconds)
    )
    healthcheck_server = None
    webapp_server = None
    polling_task = None

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
        # Blue "Menu" button next to the input field opens the user Mini App
        # (reset to Telegram's default commands button when WEBAPP_URL is unset).
        await set_default_menu_button(bot, webapp_url=cfg.webapp_url)
        polling_task = asyncio.create_task(
            dp.start_polling(bot, polling_timeout=cfg.telegram_polling_timeout_seconds)
        )
        await _supervise(scheduler_task, polling_task)
    finally:
        stop_event.set()
        if polling_task is not None:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Polling task failed during shutdown")
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
            logger.exception("Scheduler task failed during shutdown")
        await conn.close()


if __name__ == "__main__":
    asyncio.run(amain())
