from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)

from core.rbac import DraftPermissions
from core.state import DraftRow, RecurringPattern, ScheduledPostRow, StateStore
from core.time_picker import TimePicker, resolve_quick_option, resolve_selected_time
from core.utils import ParsedScheduleTime, parse_local_datetime, validate_schedule_time
from telegram.i18n import (
    DEFAULT_LANGUAGE,
    key_values,
    normalize_language,
    resolve_timezone_choice,
    tr,
)
from telegram.handlers.states import (
    ScheduleStates,
    RepeatStates, DraftStates, BroadcastStates, EditStates,
)
from telegram.handlers.keyboards import (
    _main_menu_kb, _destinations_kb,
    _media_collect_kb, _confirm_kb, _queue_cancel_kb, _queue_edit_kb,
    _queue_delete_kb, _queue_paged_kb, _edit_paged_kb, _delete_paged_kb, _edit_field_kb,
    _delete_confirm_kb, _drafts_manage_kb, _draft_detail_kb, _draft_delete_confirm_kb,
    _draft_delete_command_kb, _draft_create_scope_kb, _schedule_calendar_kb, _schedule_time_kb,
    _schedule_datetime_markup, _normalize_selected_chat_ids, _normalize_draft_scope,
    _draft_scope_label, _draft_location_label, _draft_preview_text, _draft_action_labels,
    _draft_post_prompt_text, _repeat_interval_label, _repeat_weekdays_mask,
    _schedule_quick_labels, _schedule_weekday_labels, _format_selected_date,
    _parse_calendar_date_token, _parse_calendar_month_token, _parse_time_token, _short_id,
    _format_local, _selected_date_from_state, _calendar_month_from_state,
    _is_time_selection_state,
)
from telegram.handlers.helpers import (
    _build_scheduled_post_summary,
    _check_bot_admin_and_post,
    _check_user_admin,
    _clear_inline_markup,
    _edit_datetime_prompt,
    _extract_media_item,
    _format_rights_check_error,
    _is_datetime_entry_state,
    _is_valid_tz_name,
    _main_menu_for,
    _move_draft_publish_to_confirmation,
    _move_repeat_to_destination_selection,
    _move_to_post_collection,
    _prompt_draft_scope,
    _prompt_for_datetime,
    _render_broadcast_destinations,
    _render_destinations,
    _resolve_broadcast_destinations,
    _resolve_broadcast_destination_lines,
    _resolve_caption_above,
    _resolve_draft_id,
    _resolve_recurring_pattern_id,
    _resolve_scheduled_post_id,
    _save_scheduled_post_media,
    _save_scheduled_post_time,
    _schedule_time_prompt,
    _schedule_validation_text,
    _send_post_preview,
    _update_draft_from_state,
    _user_lang,
)
from telegram.handlers import broadcast, drafts, queue, recurring, schedule, settings, shared, teams

logger = logging.getLogger(__name__)


_MENU_SCHEDULE_TEXTS = key_values("menu_schedule")
_MENU_QUEUE_TEXTS = key_values("menu_queue")
_MENU_DESTINATIONS_TEXTS = key_values("menu_destinations")


def build_router(store: StateStore) -> Router:
    router = Router()
    router.include_router(shared.build_router(store))   # cross-flow, must be first
    router.include_router(schedule.build_router(store))
    router.include_router(recurring.build_router(store))
    router.include_router(drafts.build_router(store))
    router.include_router(broadcast.build_router(store))
    router.include_router(queue.build_router(store))
    router.include_router(settings.build_router(store))
    router.include_router(teams.build_router(store))

    @router.message(Command("repeat_cancel"))
    async def cmd_repeat_cancel(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(store, message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "repeat_cancel_usage"), reply_markup=await _main_menu_for(store, message.from_user.id))
            return

        pattern_ref = parts[1].strip().lower()
        patterns = await store.list_user_recurring(message.from_user.id, include_inactive=True)
        pattern_id = _resolve_recurring_pattern_id(patterns, pattern_ref)
        if pattern_id is None:
            await message.answer(tr(lang, "repeat_cancel_missing"), reply_markup=await _main_menu_for(store, message.from_user.id))
            return

        ok = await store.cancel_recurring_pattern(user_id=message.from_user.id, pattern_id=pattern_id)
        if not ok:
            await message.answer(tr(lang, "repeat_cancel_missing"), reply_markup=await _main_menu_for(store, message.from_user.id))
            return

        await message.answer(
            tr(lang, "repeat_cancel_ok", pattern_id=_short_id(pattern_id)),
            reply_markup=await _main_menu_for(store, message.from_user.id),
        )

    @router.message(F.text.in_(_MENU_DESTINATIONS_TEXTS))
    @router.message(Command("destinations"))
    async def cmd_destinations(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(store, message.from_user.id)
        total = await store.count_user_destinations(message.from_user.id)
        await message.answer(
            tr(lang, "destinations_info", total=total),
            reply_markup=await _main_menu_for(store, message.from_user.id),
        )

    return router
