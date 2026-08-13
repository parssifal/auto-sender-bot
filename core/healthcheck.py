from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import aiosqlite
from aiohttp import web

from core.scheduler import SchedulerMetrics

logger = logging.getLogger(__name__)


@dataclass
class HealthcheckServer:
    runner: web.AppRunner
    host: str
    port: int

    async def close(self) -> None:
        await self.runner.cleanup()

    def url(self, path: str = "/healthz") -> str:
        return f"http://{self.host}:{self.port}{path}"


async def collect_health_status(
    conn: aiosqlite.Connection,
    scheduler_task: asyncio.Task[object],
    *,
    scheduler_metrics: SchedulerMetrics | None = None,
    scheduler_stale_seconds: int = 300,
    now: int | None = None,
) -> tuple[int, dict[str, Any]]:
    current = int(time.time()) if now is None else now
    db_ok, db_detail = await _check_db(conn)
    scheduler_ok, scheduler_detail, scheduler_extra = _check_scheduler(
        scheduler_task,
        scheduler_metrics=scheduler_metrics,
        scheduler_stale_seconds=scheduler_stale_seconds,
        now=current,
    )
    oldest_due_ok, oldest_due_detail, oldest_due_extra = await _check_oldest_due_post(
        conn,
        now=current,
        max_age_seconds=scheduler_stale_seconds,
    )
    ok = db_ok and scheduler_ok and oldest_due_ok

    payload: dict[str, Any] = {
        "status": "ok" if ok else "degraded",
        "checks": {
            "db": {
                "ok": db_ok,
                "detail": db_detail,
            },
            "scheduler": {
                "ok": scheduler_ok,
                "detail": scheduler_detail,
                **scheduler_extra,
            },
            "oldest_due_post": {
                "ok": oldest_due_ok,
                "detail": oldest_due_detail,
                **oldest_due_extra,
            },
        },
    }
    return (200 if ok else 503), payload


async def start_healthcheck_server(
    *,
    host: str,
    port: int,
    conn: aiosqlite.Connection,
    scheduler_task: asyncio.Task[object],
    scheduler_metrics: SchedulerMetrics | None = None,
    scheduler_stale_seconds: int = 300,
) -> HealthcheckServer:
    async def healthz(_request: web.Request) -> web.Response:
        status, payload = await collect_health_status(
            conn,
            scheduler_task,
            scheduler_metrics=scheduler_metrics,
            scheduler_stale_seconds=scheduler_stale_seconds,
        )
        return web.json_response(payload, status=status)

    app = web.Application()
    app.router.add_get("/healthz", healthz)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    addresses = runner.addresses
    actual_host = host
    actual_port = port
    if addresses:
        first_address = addresses[0]
        if isinstance(first_address, tuple) and len(first_address) >= 2:
            actual_host = str(first_address[0])
            actual_port = int(first_address[1])

    logger.info("Healthcheck server started on %s:%s", actual_host, actual_port)
    return HealthcheckServer(runner=runner, host=actual_host, port=actual_port)


async def _check_db(conn: aiosqlite.Connection) -> tuple[bool, str]:
    try:
        cursor = await asyncio.wait_for(conn.execute("SELECT 1"), timeout=1.0)
        try:
            row = await asyncio.wait_for(cursor.fetchone(), timeout=1.0)
        finally:
            await cursor.close()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    return row is not None, "ok" if row is not None else "no rows"


def _check_scheduler_task(scheduler_task: asyncio.Task[object]) -> tuple[bool, str]:
    if not scheduler_task.done():
        return True, "running"
    if scheduler_task.cancelled():
        return False, "cancelled"

    exc = scheduler_task.exception()
    if exc is not None:
        return False, f"{type(exc).__name__}: {exc}"
    return False, "finished"


def _check_scheduler(
    scheduler_task: asyncio.Task[object],
    *,
    scheduler_metrics: SchedulerMetrics | None,
    scheduler_stale_seconds: int,
    now: int,
) -> tuple[bool, str, dict[str, Any]]:
    task_ok, task_detail = _check_scheduler_task(scheduler_task)
    if not task_ok:
        return False, task_detail, {}
    if scheduler_metrics is None:
        return True, task_detail, {}

    extra = {
        "last_tick_started_at": scheduler_metrics.last_tick_started_at,
        "last_tick_finished_at": scheduler_metrics.last_tick_finished_at,
        "last_error": scheduler_metrics.last_error,
        "last_due_count": scheduler_metrics.last_due_count,
    }
    finished_at = scheduler_metrics.last_tick_finished_at
    if finished_at is None:
        return False, "stale: no successful tick yet", extra
    age = now - finished_at
    extra["last_success_age_seconds"] = age
    if age > scheduler_stale_seconds:
        return False, f"stale: last successful tick {age}s ago", extra
    return True, task_detail, extra


async def _check_oldest_due_post(
    conn: aiosqlite.Connection,
    *,
    now: int,
    max_age_seconds: int,
) -> tuple[bool, str, dict[str, Any]]:
    try:
        row = await conn.execute_fetchall(
            """
            SELECT MIN(scheduled_at_utc) AS oldest
            FROM scheduled_posts
            WHERE status='pending'
              AND scheduled_at_utc <= ?
              AND (next_retry_at_utc IS NULL OR next_retry_at_utc <= ?)
            """,
            (now, now),
        )
    except Exception as exc:
        return True, f"unavailable: {type(exc).__name__}: {exc}", {"scheduled_at_utc": None, "age_seconds": 0}

    oldest = row[0]["oldest"] if row else None
    if oldest is None:
        return True, "none", {"scheduled_at_utc": None, "age_seconds": 0}
    age = now - int(oldest)
    return (
        age <= max_age_seconds,
        "ok" if age <= max_age_seconds else f"oldest due post is {age}s old",
        {"scheduled_at_utc": int(oldest), "age_seconds": age},
    )
