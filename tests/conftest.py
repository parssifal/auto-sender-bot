import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest_asyncio  # noqa: E402
from aiogram import Bot  # noqa: E402

from core.db import open_db  # noqa: E402
from core.state import StateStore  # noqa: E402

BOT_ID = 42


class FakeBot(Bot):
    """Recording bot used by router flow tests: captures every API call."""

    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        return True


@pytest_asyncio.fixture
async def store():
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()
