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


def _retry_after_seconds(exc: Exception) -> float | None:
    """Flood-wait delay from aiogram's ``TelegramRetryAfter``, else ``None``.

    Name-matched like ``_is_blocked`` to keep aiogram out of the service imports.
    """
    if type(exc).__name__ == "TelegramRetryAfter":
        return float(getattr(exc, "retry_after", 1))
    return None


async def _deliver_one(send: "Callable[[int], Awaitable[object]]", uid: int) -> str:
    """Deliver to one recipient, honouring a single flood-wait. Returns the outcome key."""
    try:
        await send(uid)
        return "delivered"
    except Exception as exc:  # noqa: BLE001 — per-recipient isolation is the point
        wait = _retry_after_seconds(exc)
        if wait is None:
            return "blocked" if _is_blocked(exc) else "failed"
    # ponytail: blocks the run for `wait` seconds; Telegram's retry_after is small in practice.
    await asyncio.sleep(wait)
    try:
        await send(uid)
        return "delivered"
    except Exception as exc:  # noqa: BLE001
        return "blocked" if _is_blocked(exc) else "failed"


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
    counts = {"delivered": 0, "blocked": 0, "failed": 0}
    for uid in recipients:
        counts[await _deliver_one(_send, uid)] += 1
        if throttle:
            await asyncio.sleep(throttle)
    delivered, blocked, failed = counts["delivered"], counts["blocked"], counts["failed"]
    return {
        "total": len(recipients),
        "delivered": delivered,
        "blocked": blocked,
        "failed": failed,
    }
