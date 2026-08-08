import asyncio

import pytest

from main import _supervise


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_supervise_raises_when_scheduler_dies() -> None:
    # T-13: a scheduler crash must surface as a raise so the process exits and the
    # restart policy revives it, instead of polling on with nobody sending posts.
    async def _boom() -> None:
        raise RuntimeError("scheduler exploded")

    scheduler_task = asyncio.create_task(_boom())
    polling_task = asyncio.create_task(_forever())
    try:
        with pytest.raises(RuntimeError):
            await _supervise(scheduler_task, polling_task)
    finally:
        polling_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await polling_task


@pytest.mark.asyncio
async def test_supervise_returns_when_polling_ends_normally() -> None:
    scheduler_task = asyncio.create_task(_forever())
    polling_task = asyncio.create_task(asyncio.sleep(0))
    try:
        await _supervise(scheduler_task, polling_task)  # must not raise
    finally:
        scheduler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scheduler_task
