"""Live channel-rights checks, shared by the bot handlers and the Mini App API.

Boundary (enforced by ``tests/test_services_boundary.py``): services must not
import ``telegram`` or ``aiogram``. So ``bot`` is an opaque ``object``, and the
"bot is not in the chat" case is matched by exception class name, exactly like
``admin_broadcast_svc._is_blocked``.

Checks return a stable key instead of a message: the handlers localize it via
``tr()``, the Mini App API returns it as a JSON error.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RightsError:
    key: str
    subject: str | None = None  # "user" | "bot", only for ``rights_check_failed``
    error: Exception | None = None


def _check_failed(exc: Exception, *, subject: str) -> RightsError:
    if type(exc).__name__ == "TelegramForbiddenError":
        text = str(exc).lower()
        if "not a member" in text or "bot was kicked" in text:
            return RightsError("rights_not_member")
    return RightsError("rights_check_failed", subject=subject, error=exc)


async def check_user_is_admin(bot: object, chat_id: int, user_id: int) -> RightsError | None:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as exc:
        return _check_failed(exc, subject="user")
    if member.status not in {"creator", "administrator"}:
        return RightsError("rights_user_admin_required")
    return None


async def check_bot_can_post(bot: object, chat_id: int) -> RightsError | None:
    try:
        me = await bot.me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    except Exception as exc:
        return _check_failed(exc, subject="bot")
    if member.status != "administrator":
        return RightsError("rights_bot_admin_required")
    if getattr(member, "can_post_messages", None) is False:
        return RightsError("rights_bot_can_post_required")
    return None
