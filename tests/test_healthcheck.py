from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from core.db import open_db
from core.healthcheck import collect_health_status, start_healthcheck_server


@pytest_asyncio.fixture
async def health_db():
    conn = await open_db(":memory:")
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_healthcheck_endpoint_reports_ok(health_db) -> None:
    scheduler_task = asyncio.create_task(asyncio.sleep(60))
    server = await start_healthcheck_server(
        host="127.0.0.1",
        port=0,
        conn=health_db,
        scheduler_task=scheduler_task,
    )

    try:
        async with ClientSession() as session:
            async with session.get(server.url()) as response:
                payload = await response.json()

        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["checks"]["db"]["ok"] is True
        assert payload["checks"]["scheduler"]["ok"] is True
    finally:
        await server.close()
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task


@pytest.mark.asyncio
async def test_collect_health_status_reports_unhealthy_scheduler(health_db) -> None:
    scheduler_task = asyncio.create_task(asyncio.sleep(0))
    await scheduler_task

    status, payload = await collect_health_status(health_db, scheduler_task)

    assert status == 503
    assert payload["status"] == "degraded"
    assert payload["checks"]["db"]["ok"] is True
    assert payload["checks"]["scheduler"]["ok"] is False
    assert payload["checks"]["scheduler"]["detail"] == "finished"
