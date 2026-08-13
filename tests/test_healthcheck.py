from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from core.db import open_db
from core.healthcheck import collect_health_status, start_healthcheck_server
from core.scheduler import SchedulerMetrics
from core.state import StateStore


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


@pytest.mark.asyncio
async def test_collect_health_status_reports_stale_scheduler_tick(health_db) -> None:
    scheduler_task = asyncio.create_task(asyncio.sleep(60))
    metrics = SchedulerMetrics(last_tick_finished_at=100, last_tick_started_at=90)
    try:
        status, payload = await collect_health_status(
            health_db,
            scheduler_task,
            scheduler_metrics=metrics,
            scheduler_stale_seconds=30,
            now=200,
        )
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task

    assert status == 503
    assert payload["status"] == "degraded"
    assert payload["checks"]["scheduler"]["ok"] is False
    assert "stale" in payload["checks"]["scheduler"]["detail"]


@pytest.mark.asyncio
async def test_collect_health_status_reports_oldest_due_backlog() -> None:
    conn = await open_db(":memory:")
    scheduler_task = asyncio.create_task(asyncio.sleep(60))
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(1)
        await store.upsert_destination(-1, "channel", "Due", None, "administrator", True)
        await store.create_scheduled_text_post(1, -1, 100, "late", None)

        status, payload = await collect_health_status(
            conn,
            scheduler_task,
            scheduler_metrics=SchedulerMetrics(last_tick_finished_at=200),
            scheduler_stale_seconds=30,
            now=200,
        )
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task
        await conn.close()

    assert status == 503
    assert payload["checks"]["oldest_due_post"]["ok"] is False
    assert payload["checks"]["oldest_due_post"]["age_seconds"] == 100
