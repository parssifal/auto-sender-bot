"""Admin broadcast to user DMs.

Boundary (Phase 3 rule, enforced by ``tests/test_services_boundary.py``): services
must not import ``telegram`` or ``aiogram``. This service still needs to *send*, so
it receives the aiogram ``bot`` as an opaque ``object`` and delegates delivery to
``core.notifier.send_text`` (a ``core.*`` module). It classifies "user blocked the
bot" without importing aiogram by matching the exception class name — see
``_is_blocked``. The ``send`` param is injectable so unit tests need no real bot.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from core import notifier
from core.state import StateStore


def _is_blocked(exc: Exception) -> bool:
    """True for aiogram's ``TelegramForbiddenError`` (user blocked the bot).

    Matched by class name to keep aiogram out of the service imports (boundary rule).
    """
    return type(exc).__name__ == "TelegramForbiddenError"


async def broadcast_to_all(
    store: StateStore,
    bot: object,
    *,
    text: str,
    entities_json: str | None = None,
    send: Callable[[int], Awaitable[object]] | None = None,
    throttle: float = 0.05,
) -> dict:
    """Send ``text`` to every user's DM. Per-recipient failures never abort the run.

    Returns ``{"total", "delivered", "blocked", "failed"}``. A blocked bot counts as
    ``blocked``; any other exception as ``failed``.
    """
    async def _default_send(uid: int) -> object:
        return await notifier.send_text(bot, uid, text, entities_json)

    _send = send or _default_send

    recipients = await store.all_user_ids()
    delivered = blocked = failed = 0
    for uid in recipients:
        try:
            await _send(uid)
            delivered += 1
        except Exception as exc:  # noqa: BLE001 — per-recipient isolation is the point
            if _is_blocked(exc):
                blocked += 1
            else:
                failed += 1
        if throttle:
            await asyncio.sleep(throttle)
    return {
        "total": len(recipients),
        "delivered": delivered,
        "blocked": blocked,
        "failed": failed,
    }
