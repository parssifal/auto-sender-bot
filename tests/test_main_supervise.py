import asyncio
import signal

import pytest

from main import _install_signal_handlers, _supervise


def test_install_signal_handlers_sets_stop_event() -> None:
    # A SIGTERM/SIGINT during startup (before polling installs its own handlers)
    # must trip stop_event so the finally-block cleanup runs.
    loop = asyncio.new_event_loop()
    try:
        stop_event = asyncio.Event()
        _install_signal_handlers(loop, stop_event)
        # Invoke the registered SIGTERM callback directly (portable: does not
        # require actually raising the OS signal).
        handle = loop._signal_handlers.get(signal.SIGTERM)
        assert handle is not None, "SIGTERM handler was not registered"
        handle._run()
        assert stop_event.is_set()
    finally:
        loop.close()


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
