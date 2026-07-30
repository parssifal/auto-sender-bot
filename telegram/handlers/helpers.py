from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import Message

from core.state import DraftRow, RecurringPattern, ScheduledPostRow, Team
from core.utils import validate_schedule_time
from telegram.i18n import DEFAULT_LANGUAGE, resolve_timezone_choice, tr
from telegram.handlers.states import (
    BroadcastStates,
    DraftStates,
    EditStates,
    RepeatStates,
    ScheduleStates,
)
from telegram.handlers.keyboards import (
    _format_selected_date,
    _media_collect_kb,
    _schedule_datetime_markup,
)


def _is_datetime_entry_state(state_name: str | None) -> bool:
    return state_name in {
        ScheduleStates.entering_datetime.state,
        RepeatStates.entering_datetime.state,
        DraftStates.entering_datetime.state,
        BroadcastStates.entering_datetime.state,
        EditStates.entering_datetime.state,
    }


def _resolve_draft_id(drafts: list[DraftRow], draft_ref: str) -> str | None:
    ref = draft_ref.strip().lower()
    if not ref:
        return None

    for draft in drafts:
        if draft.id == ref:
            return draft.id

    matches = [draft.id for draft in drafts if draft.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_scheduled_post_id(posts: list[ScheduledPostRow], post_ref: str) -> tuple[str | None, bool]:
    ref = post_ref.strip().lower()
    if not ref:
        return None, False

    for post in posts:
        if post.id == ref:
            return post.id, False

    matches = [post.id for post in posts if post.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _resolve_team_id(teams: list[Team], team_ref: str) -> str | None:
    ref = team_ref.strip().lower()
    if not ref:
        return None

    for team in teams:
        if team.id == ref:
            return team.id

    matches = [team.id for team in teams if team.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


async def _clear_inline_markup(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        return


def _resolve_recurring_pattern_id(patterns: list[RecurringPattern], pattern_ref: str) -> str | None:
    ref = pattern_ref.strip().lower()
    if not ref:
        return None

    for pattern in patterns:
        if pattern.id == ref:
            return pattern.id

    matches = [pattern.id for pattern in patterns if pattern.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


def _format_rights_check_error(exc: Exception, *, subject: str, lang: str = DEFAULT_LANGUAGE) -> str:
    if isinstance(exc, TelegramForbiddenError):
        text = str(exc).lower()
        if "not a member" in text or "bot was kicked" in text:
            return tr(lang, "rights_not_member")
    return tr(lang, "rights_check_failed", subject=subject, error=exc)


def _is_valid_tz_name(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
    except Exception:
        return False
    return True


def _resolve_timezone_input(tz_raw: str) -> str | None:
    mapped = resolve_timezone_choice(tz_raw)
    if mapped:
        return mapped
    if _is_valid_tz_name(tz_raw):
        return tz_raw
    return None


def _extract_media_item(message: Message) -> dict[str, str] | None:
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id}
    if message.video:
        return {"type": "video", "file_id": message.video.file_id}
    return None


def _resolve_caption_above(
    *,
    current: bool,
    had_media_before: bool,
    text_before_media: bool,
    text_after_media: bool,
    explicit_above: bool | None,
) -> bool:
    if text_after_media:
        return False
    if explicit_above is not None:
        return explicit_above
    if not had_media_before and text_before_media:
        return True
    if had_media_before:
        return current
    return False


async def _prompt_for_datetime(message: Message, *, lang: str, tz_name: str, text: str, data: dict[str, object], state_name: str | None) -> None:
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=_schedule_datetime_markup(lang, tz_name=tz_name, data=data, state_name=state_name),
    )


async def _edit_datetime_prompt(message: Message, *, lang: str, tz_name: str, text: str, data: dict[str, object], state_name: str | None) -> None:
    await message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=_schedule_datetime_markup(lang, tz_name=tz_name, data=data, state_name=state_name),
    )


def _schedule_time_prompt(lang: str, *, selected_date: date) -> str:
    return tr(lang, "schedule_time_prompt", date_label=_format_selected_date(selected_date))


def _schedule_validation_text(lang: str, utc_timestamp: int, *, now_utc: int | None = None) -> str | None:
    validation = validate_schedule_time(utc_timestamp, now_utc=now_utc)
    if validation.is_valid or validation.error_key is None:
        return None
    return tr(lang, validation.error_key)


async def _move_to_post_collection(
    message: Message,
    state: FSMContext,
    *,
    scheduled_at_utc: int,
    scheduled_local: str,
    collecting_state: State,
    lang: str,
) -> None:
    await state.update_data(
        scheduled_at_utc=scheduled_at_utc,
        scheduled_local=scheduled_local,
        kind=None,
        text=None,
        entities_json=None,
        caption=None,
        caption_entities_json=None,
        caption_above=False,
        media_items=[],
        draft_text=None,
        draft_entities_json=None,
        text_before_media=False,
    )
    await state.set_state(collecting_state)
    await message.answer(tr(lang, "schedule_post_prompt"), reply_markup=_media_collect_kb(lang))


async def _check_user_admin(bot: Bot, chat_id: int, user_id: int, *, lang: str = DEFAULT_LANGUAGE) -> tuple[bool, str]:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as exc:
        return False, _format_rights_check_error(exc, subject=tr(lang, "rights_subject_user"), lang=lang)
    if member.status not in {"creator", "administrator"}:
        return False, tr(lang, "rights_user_admin_required")
    return True, ""


async def _check_bot_admin_and_post(bot: Bot, chat_id: int, *, lang: str = DEFAULT_LANGUAGE) -> tuple[bool, str]:
    try:
        me = await bot.me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    except Exception as exc:
        return False, _format_rights_check_error(exc, subject=tr(lang, "rights_subject_bot"), lang=lang)

    if member.status != "administrator":
        return False, tr(lang, "rights_bot_admin_required")
    can_post = getattr(member, "can_post_messages", None)
    if can_post is False:
        return False, tr(lang, "rights_bot_can_post_required")
    return True, ""
