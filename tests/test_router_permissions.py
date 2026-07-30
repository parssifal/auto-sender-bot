import pytest
from aiogram.exceptions import TelegramForbiddenError

from telegram.handlers.helpers import _check_bot_admin_and_post, _check_user_admin


class _ForbiddenUserCheckBot:
    async def get_chat_member(self, **kwargs):
        raise TelegramForbiddenError(method=None, message="Forbidden: bot is not a member of the channel chat")


class _ForbiddenBotCheckBot:
    async def me(self):
        return type("Me", (), {"id": 777})()

    async def get_chat_member(self, **kwargs):
        raise TelegramForbiddenError(method=None, message="Forbidden: bot is not a member of the channel chat")


@pytest.mark.asyncio
async def test_check_user_admin_returns_membership_hint_for_forbidden() -> None:
    ok, err = await _check_user_admin(bot=_ForbiddenUserCheckBot(), chat_id=-100, user_id=42)  # type: ignore[arg-type]

    assert ok is False
    assert "Bot is not in this channel/chat." in err
    assert "Telegram server says" not in err


@pytest.mark.asyncio
async def test_check_bot_admin_returns_membership_hint_for_forbidden() -> None:
    ok, err = await _check_bot_admin_and_post(bot=_ForbiddenBotCheckBot(), chat_id=-100)  # type: ignore[arg-type]

    assert ok is False
    assert "Bot is not in this channel/chat." in err
    assert "Telegram server says" not in err
