from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiosqlite
from aiohttp import web

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
) -> tuple[int, dict[str, Any]]:
    db_ok, db_detail = await _check_db(conn)
    scheduler_ok, scheduler_detail = _check_scheduler_task(scheduler_task)

    payload: dict[str, Any] = {
        "status": "ok" if db_ok and scheduler_ok else "degraded",
        "checks": {
            "db": {
                "ok": db_ok,
                "detail": db_detail,
            },
            "scheduler": {
                "ok": scheduler_ok,
                "detail": scheduler_detail,
            },
        },
    }
    return (200 if db_ok and scheduler_ok else 503), payload


async def start_healthcheck_server(
    *,
    host: str,
    port: int,
    conn: aiosqlite.Connection,
    scheduler_task: asyncio.Task[object],
) -> HealthcheckServer:
    async def healthz(_request: web.Request) -> web.Response:
        status, payload = await collect_health_status(conn, scheduler_task)
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
