from __future__ import annotations

import logging

from aiogram import Router

from core.state import StateStore
from telegram.handlers import broadcast, drafts, queue, recurring, schedule, settings, shared, teams

logger = logging.getLogger(__name__)


def build_router(store: StateStore, *, webapp_url: str | None = None) -> Router:
    router = Router()
    router.include_router(shared.build_router(store, webapp_url=webapp_url))   # cross-flow, must be first
    router.include_router(schedule.build_router(store))
    router.include_router(recurring.build_router(store))
    router.include_router(drafts.build_router(store))
    router.include_router(broadcast.build_router(store))
    router.include_router(queue.build_router(store, webapp_url=webapp_url))
    router.include_router(settings.build_router(store, webapp_url=webapp_url))
    router.include_router(teams.build_router(store))
    return router
