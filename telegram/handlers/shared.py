from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
)

from core.services import broadcast_svc, draft_svc
from core.state import StateStore
from core.time_picker import TimePicker, resolve_quick_option, resolve_selected_time
from core.utils import NonexistentLocalTimeError, ParsedScheduleTime, parse_local_datetime
from telegram.i18n import tr
from telegram.handlers.states import (
    ScheduleStates,
    RepeatStates, DraftStates, BroadcastStates, EditStates,
)
from telegram.handlers.keyboards import (
    _main_menu_kb,
    _media_collect_kb, _confirm_kb,
    _normalize_selected_chat_ids,
    _draft_preview_text,
    _draft_post_prompt_text, _repeat_interval_label, _repeat_weekdays_mask,
    _parse_calendar_date_token, _parse_calendar_month_token, _parse_time_token, _short_id,
    _format_local, _selected_date_from_state,
    _is_time_selection_state,
)
from core.limits import ResourceLimitError
from telegram.handlers.helpers import (
    _check_bot_admin_and_post,
    _check_user_admin,
    _clear_inline_markup,
    limit_message,
    _edit_datetime_prompt,
    _extract_media_item,
    _is_datetime_entry_state,
    _main_menu_for,
    _move_draft_publish_to_confirmation,
    _move_repeat_to_destination_selection,
    _move_to_post_collection,
    _prompt_draft_scope,
    _prompt_for_datetime,
    _render_broadcast_destinations,
    _require_tz,
    _resolve_broadcast_destination_lines,
    _resolve_caption_above,
    _save_scheduled_post_media,
    _save_scheduled_post_time,
    _schedule_time_prompt,
    _schedule_validation_text,
    _update_draft_from_state,
    _user_lang,
    get_broadcast_ctx,
    get_draft_ctx,
    get_edit_ctx,
    get_repeat_ctx,
    get_schedule_ctx,
    _not_command_or_menu,
    patch_broadcast_ctx,
    patch_content_ctx,
    patch_schedule_ctx,
)
from telegram.handlers.teams import _handle_team_invite_start
from telegram.menu_button import set_user_menu_button


_PICKER_SIBLING = {
    RepeatStates.entering_datetime.state: RepeatStates.selecting_time,
    RepeatStates.selecting_time.state: RepeatStates.entering_datetime,
    DraftStates.entering_datetime.state: DraftStates.selecting_time,
    DraftStates.selecting_time.state: DraftStates.entering_datetime,
    BroadcastStates.entering_datetime.state: BroadcastStates.selecting_time,
    BroadcastStates.selecting_time.state: BroadcastStates.entering_datetime,
    EditStates.entering_datetime.state: EditStates.selecting_time,
    EditStates.selecting_time.state: EditStates.entering_datetime,
    ScheduleStates.entering_datetime.state: ScheduleStates.selecting_time,
    ScheduleStates.selecting_time.state: ScheduleStates.entering_datetime,
}


async def _get_content_ctx(state, current_state):
    """Return the flow-correct typed context for whichever flow owns ``current_state``.

    The cross-flow media/confirm handlers share the ``PostContent`` fields but also
    read flow-specific fields (interval_type, chat_id, draft_publish_id, edit_*);
    returning the flow's own dataclass keeps those typed too.
    """
    if current_state in RepeatStates:
        return await get_repeat_ctx(state)
    if current_state in BroadcastStates:
        return await get_broadcast_ctx(state)
    if current_state in DraftStates:
        return await get_draft_ctx(state)
    if current_state in EditStates:
        return await get_edit_ctx(state)
    return await get_schedule_ctx(state)


async def _datetime_entry_prompt_text(store, state, lang: str, state_name: str) -> str:
    """Prompt text shown while a datetime-entry state is active.

    Reads the flow-specific fields (destination / draft id / edited post id) via
    the typed FSM contexts (Phase 4/4c) instead of raw ``data.get(...)``. Shared by
    the three datetime-picker nav handlers (calendar-nav, back-to-calendar, time),
    which otherwise duplicated this branch.
    """
    if state_name == DraftStates.entering_datetime.state:
        ctx = await get_draft_ctx(state)
        where = await store.get_destination_title(int(ctx.chat_id or 0)) or str(ctx.chat_id or "")
        return _draft_post_prompt_text(lang, draft_id=ctx.draft_publish_id, where=where)
    if state_name == RepeatStates.entering_datetime.state:
        return tr(lang, "repeat_enter_datetime")
    if state_name == EditStates.entering_datetime.state:
        ctx = await get_edit_ctx(state)
        return tr(lang, "edit_time_prompt", post_id=_short_id(str(ctx.edit_post_id or "")))
    return tr(lang, "enter_datetime")


async def _apply_parsed_datetime(
    store,
    message,
    state,
    *,
    user_id: int,
    current_state: str | None,
    parsed: ParsedScheduleTime,
    lang: str,
) -> None:
    """Route a resolved schedule time to the flow that owns ``current_state``.

    Reached from three picker entries — quick button, time button, typed text —
    which each ran an identical five-way dispatch on the flow's
    entering_datetime/selecting_time states. ``current_state`` is always one of
    the active flow's two picker states, so membership covers both.
    """
    if current_state in {RepeatStates.entering_datetime.state, RepeatStates.selecting_time.state}:
        await _move_repeat_to_destination_selection(
            store,
            message,
            state,
            user_id=user_id,
            scheduled_at_utc=parsed.utc_epoch,
            scheduled_local=str(parsed.local_dt),
        )
        return
    if current_state in {DraftStates.entering_datetime.state, DraftStates.selecting_time.state}:
        await _move_draft_publish_to_confirmation(
            store,
            message,
            state,
            user_id=user_id,
            scheduled_at_utc=parsed.utc_epoch,
            scheduled_local=str(parsed.local_dt),
        )
        return
    if current_state in {EditStates.entering_datetime.state, EditStates.selecting_time.state}:
        await _save_scheduled_post_time(
            store,
            message,
            state,
            user_id=user_id,
            scheduled_at_utc=parsed.utc_epoch,
        )
        return
    if current_state in {BroadcastStates.entering_datetime.state, BroadcastStates.selecting_time.state}:
        await _move_to_post_collection(
            message,
            state,
            scheduled_at_utc=parsed.utc_epoch,
            scheduled_local=str(parsed.local_dt),
            collecting_state=BroadcastStates.collecting_post,
            lang=lang,
        )
        return
    await _move_to_post_collection(
        message,
        state,
        scheduled_at_utc=parsed.utc_epoch,
        scheduled_local=str(parsed.local_dt),
        collecting_state=ScheduleStates.collecting_post,
        lang=lang,
    )


def build_router(store: StateStore, *, webapp_url: str | None = None) -> Router:
    router = Router(name="shared")

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext, bot: Bot) -> None:
        await store.ensure_user(
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        parts = (message.text or "").split(maxsplit=1)
        start_arg = parts[1].strip() if len(parts) == 2 else ""
        if start_arg.startswith("ti_") and len(start_arg) > 3:
            await _handle_team_invite_start(store, message, state, user_id=message.from_user.id, token=start_arg[3:])
            return

        lang = await _user_lang(store, message.from_user.id)
        await state.clear()
        if webapp_url:
            # Localize this user's blue "Menu" button to open the Mini App.
            await set_user_menu_button(
                bot, chat_id=message.chat.id, webapp_url=webapp_url, language=lang
            )
        await message.answer(
            tr(lang, "start_message"),
            reply_markup=_main_menu_kb(lang),
        )

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(store, message.from_user.id)
        await state.clear()
        await message.answer(tr(lang, "cancelled"), reply_markup=await _main_menu_for(store, message.from_user.id))

    @router.callback_query(F.data == TimePicker.NOOP_CALLBACK)
    async def cb_time_picker_noop(query: CallbackQuery) -> None:
        await query.answer()

    @router.callback_query(F.data.startswith("tp:nav:"))
    async def cb_schedule_calendar_nav(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_datetime_entry_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        tz_name = await _require_tz(store, query.message, query.from_user.id, lang, query=query, state=state)
        if tz_name is None:
            return

        token = query.data.split(":", 2)[2]
        try:
            year, month = _parse_calendar_month_token(token)
        except ValueError:
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            return

        await patch_content_ctx(state, current_state, calendar_year=year, calendar_month=month)
        data = await state.get_data()
        text = await _datetime_entry_prompt_text(store, state, lang, current_state)
        await query.answer()
        await _edit_datetime_prompt(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=text,
            data=data,
            state_name=current_state,
        )

    @router.callback_query(F.data.startswith("tp:date:"))
    async def cb_schedule_calendar_date(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_datetime_entry_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        tz_name = await _require_tz(store, query.message, query.from_user.id, lang, query=query, state=state)
        if tz_name is None:
            return

        token = query.data.split(":", 2)[2]
        try:
            selected_date = _parse_calendar_date_token(token)
        except ValueError:
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            return

        await patch_content_ctx(
            state,
            current_state,
            selected_date=selected_date.isoformat(),
            calendar_year=selected_date.year,
            calendar_month=selected_date.month,
        )
        next_state = _PICKER_SIBLING.get(current_state, ScheduleStates.selecting_time)
        await state.set_state(next_state)
        await query.answer()
        await _edit_datetime_prompt(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=_schedule_time_prompt(lang, selected_date=selected_date),
            data=await state.get_data(),
            state_name=next_state.state,
        )

    @router.callback_query(F.data.startswith("tp:quick:"))
    async def cb_schedule_quick(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_datetime_entry_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        tz_name = await _require_tz(store, query.message, query.from_user.id, lang, query=query, state=state)
        if tz_name is None:
            return

        option = query.data.split(":", 2)[2]
        try:
            parsed = resolve_quick_option(option, tz_name=tz_name)
        except NonexistentLocalTimeError:
            await query.answer(tr(lang, "datetime_dst_gap"), show_alert=True)
            return
        except ValueError:
            await query.answer(tr(lang, "invalid_datetime_format"), show_alert=True)
            return

        validation_text = _schedule_validation_text(lang, parsed.utc_epoch)
        if validation_text is not None:
            await query.answer(validation_text, show_alert=True)
            return

        await query.answer()
        await _clear_inline_markup(query.message)
        await _apply_parsed_datetime(
            store,
            query.message,
            state,
            user_id=query.from_user.id,
            current_state=current_state,
            parsed=parsed,
            lang=lang,
        )

    @router.callback_query(F.data == "tp:back:calendar")
    async def cb_schedule_back_to_calendar(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_time_selection_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        tz_name = await _require_tz(store, query.message, query.from_user.id, lang, query=query, state=state)
        if tz_name is None:
            return

        data = await state.get_data()
        selected_date = _selected_date_from_state(data)
        if selected_date is not None:
            await patch_content_ctx(state, current_state, calendar_year=selected_date.year, calendar_month=selected_date.month)
        previous_state = _PICKER_SIBLING.get(current_state, ScheduleStates.entering_datetime)
        await state.set_state(previous_state)
        data = await state.get_data()
        text = await _datetime_entry_prompt_text(store, state, lang, previous_state.state)
        await query.answer()
        await _edit_datetime_prompt(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=text,
            data=data,
            state_name=previous_state.state,
        )

    @router.callback_query(F.data.startswith("tp:time:"))
    async def cb_schedule_time(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_time_selection_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        tz_name = await _require_tz(store, query.message, query.from_user.id, lang, query=query, state=state)
        if tz_name is None:
            return

        data = await state.get_data()
        selected_date = _selected_date_from_state(data)
        if selected_date is None:
            previous_state = _PICKER_SIBLING.get(current_state, ScheduleStates.entering_datetime)
            await state.set_state(previous_state)
            data = await state.get_data()
            text = await _datetime_entry_prompt_text(store, state, lang, previous_state.state)
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            await _edit_datetime_prompt(
                query.message,
                lang=lang,
                tz_name=tz_name,
                text=text,
                data=data,
                state_name=previous_state.state,
            )
            return

        token = query.data.split(":", 2)[2]
        try:
            hour, minute = _parse_time_token(token)
            parsed = resolve_selected_time(selected_date, hour=hour, minute=minute, tz_name=tz_name)
        except NonexistentLocalTimeError:
            await query.answer(tr(lang, "datetime_dst_gap"), show_alert=True)
            return
        except ValueError:
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            return

        validation_text = _schedule_validation_text(lang, parsed.utc_epoch)
        if validation_text is not None:
            await query.answer(validation_text, show_alert=True)
            return

        await query.answer()
        await _clear_inline_markup(query.message)
        await _apply_parsed_datetime(
            store,
            query.message,
            state,
            user_id=query.from_user.id,
            current_state=current_state,
            parsed=parsed,
            lang=lang,
        )

    @router.message(BroadcastStates.selecting_time, _not_command_or_menu)
    @router.message(BroadcastStates.entering_datetime, _not_command_or_menu)
    @router.message(EditStates.selecting_time, _not_command_or_menu)
    @router.message(EditStates.entering_datetime, _not_command_or_menu)
    @router.message(DraftStates.selecting_time, _not_command_or_menu)
    @router.message(DraftStates.entering_datetime, _not_command_or_menu)
    @router.message(RepeatStates.selecting_time, _not_command_or_menu)
    @router.message(RepeatStates.entering_datetime, _not_command_or_menu)
    @router.message(ScheduleStates.selecting_time, _not_command_or_menu)
    @router.message(ScheduleStates.entering_datetime, _not_command_or_menu)
    async def schedule_enter_datetime(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(store, message.from_user.id)
        tz_name = await _require_tz(store, message, message.from_user.id, lang, state=state)
        if tz_name is None:
            return

        current_state = await state.get_state()
        data = await state.get_data()
        try:
            parsed: ParsedScheduleTime = parse_local_datetime(message.text, tz_name=tz_name)
        except NonexistentLocalTimeError:
            await _prompt_for_datetime(
                message,
                lang=lang,
                tz_name=tz_name,
                text=tr(lang, "datetime_dst_gap"),
                data=data,
                state_name=current_state,
            )
            return
        except Exception:
            await _prompt_for_datetime(
                message,
                lang=lang,
                tz_name=tz_name,
                text=tr(lang, "invalid_datetime_format"),
                data=data,
                state_name=current_state,
            )
            return

        validation_text = _schedule_validation_text(lang, parsed.utc_epoch)
        if validation_text is not None:
            await _prompt_for_datetime(
                message,
                lang=lang,
                tz_name=tz_name,
                text=validation_text,
                data=data,
                state_name=current_state,
            )
            return

        await _apply_parsed_datetime(
            store,
            message,
            state,
            user_id=message.from_user.id,
            current_state=current_state,
            parsed=parsed,
            lang=lang,
        )

    @router.message(EditStates.collecting_media, _not_command_or_menu)
    @router.message(BroadcastStates.collecting_post, _not_command_or_menu)
    @router.message(DraftStates.editing_post, _not_command_or_menu)
    @router.message(DraftStates.collecting_post, _not_command_or_menu)
    @router.message(RepeatStates.collecting_post, _not_command_or_menu)
    @router.message(ScheduleStates.collecting_post, _not_command_or_menu)
    async def schedule_collect_post(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(store, message.from_user.id)
        current_state = await state.get_state()
        ctx = await _get_content_ctx(state, current_state)
        media: list[dict[str, str]] = list(ctx.media_items)
        draft_text: str | None = ctx.draft_text
        draft_entities_json: str | None = ctx.draft_entities_json
        caption_above = bool(ctx.caption_above)
        text_before_media = bool(ctx.text_before_media)
        media_item = _extract_media_item(message)

        if message.text and media_item is None:
            draft_text = message.text
            draft_entities_json = store.dump_entities(message.entities)
            text_after_media = bool(media)
            text_before_media = not text_after_media
            caption_above = _resolve_caption_above(
                current=caption_above,
                had_media_before=bool(media),
                text_before_media=text_before_media,
                text_after_media=text_after_media,
                explicit_above=None,
            )
            await patch_content_ctx(
                state,
                current_state,
                draft_text=draft_text,
                draft_entities_json=draft_entities_json,
                caption_above=caption_above,
                text_before_media=text_before_media,
            )
            if media:
                await message.answer(tr(lang, "caption_updated", count=len(media)), reply_markup=_media_collect_kb(lang))
            else:
                await message.answer(tr(lang, "text_saved"), reply_markup=_media_collect_kb(lang))
            return

        if media_item is None:
            await message.answer(tr(lang, "media_send_prompt"), reply_markup=_media_collect_kb(lang))
            return

        if len(media) >= 10:
            await message.answer(tr(lang, "media_limit"), reply_markup=_media_collect_kb(lang))
            return

        had_media_before = bool(media)
        media.append(media_item)

        explicit_above: bool | None = None
        caption_from_message = (message.caption or "").strip()
        if caption_from_message:
            draft_text = message.caption
            draft_entities_json = store.dump_entities(message.caption_entities)
            incoming = getattr(message, "show_caption_above_media", None)
            explicit_above = None if incoming is None else bool(incoming)
            text_before_media = False

        if (
            current_state == EditStates.collecting_media.state
            and bool(ctx.edit_preserve_caption_above)
            and not had_media_before
            and not caption_from_message
        ):
            caption_above = bool(ctx.caption_above)
        else:
            caption_above = _resolve_caption_above(
                current=caption_above,
                had_media_before=had_media_before,
                text_before_media=text_before_media,
                text_after_media=False,
                explicit_above=explicit_above,
            )

        await patch_content_ctx(
            state,
            current_state,
            media_items=media,
            draft_text=draft_text,
            draft_entities_json=draft_entities_json,
            caption_above=caption_above,
            text_before_media=text_before_media,
        )
        await message.answer(tr(lang, "media_added", count=len(media)), reply_markup=_media_collect_kb(lang))

    @router.callback_query(F.data == "smedia:clear")
    async def cb_media_clear(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(store, query.from_user.id)
        current_state = await state.get_state()
        if current_state not in {
            BroadcastStates.collecting_post.state,
            ScheduleStates.collecting_post.state,
            RepeatStates.collecting_post.state,
            DraftStates.collecting_post.state,
            DraftStates.editing_post.state,
            EditStates.collecting_media.state,
        }:
            await query.answer()
            return

        await query.answer()
        await patch_content_ctx(
            state,
            current_state,
            media_items=[],
            text=None,
            entities_json=None,
            caption=None,
            caption_entities_json=None,
            caption_above=False,
            kind=None,
            draft_text=None,
            draft_entities_json=None,
            text_before_media=False,
        )
        await query.message.answer(tr(lang, "media_cleared"), reply_markup=_media_collect_kb(lang))

    @router.callback_query(F.data == "smedia:done")
    async def cb_media_done(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if current_state not in {
            BroadcastStates.collecting_post.state,
            ScheduleStates.collecting_post.state,
            RepeatStates.collecting_post.state,
            DraftStates.collecting_post.state,
            DraftStates.editing_post.state,
            EditStates.collecting_media.state,
        }:
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        await query.answer()
        ctx = await _get_content_ctx(state, current_state)
        media: list[dict[str, str]] = list(ctx.media_items)
        draft_text = ctx.draft_text
        draft_text_valid = bool(str(draft_text).strip()) if draft_text is not None else False

        if current_state == EditStates.collecting_media.state:
            if not media:
                await query.message.answer(tr(lang, "media_need_at_least_one"), reply_markup=_media_collect_kb(lang))
                return
            await patch_content_ctx(
                state,
                current_state,
                kind="media",
                caption=draft_text if draft_text_valid else None,
                caption_entities_json=ctx.draft_entities_json if draft_text_valid else None,
                text=None,
                entities_json=None,
            )
            if await _save_scheduled_post_media(store, query.message, state, user_id=query.from_user.id):
                return
            return

        if media:
            await patch_content_ctx(
                state,
                current_state,
                kind="media",
                caption=draft_text if draft_text_valid else None,
                caption_entities_json=ctx.draft_entities_json if draft_text_valid else None,
                text=None,
                entities_json=None,
            )
            if current_state == DraftStates.editing_post.state:
                if await _update_draft_from_state(store, query.message, state, user_id=query.from_user.id):
                    return
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(store, query.from_user.id))
                return
            if current_state == DraftStates.collecting_post.state:
                await _prompt_draft_scope(store, query.message, state, user_id=query.from_user.id)
                return
            await _send_confirmation(query.message, state, store, user_id=query.from_user.id)
            return

        if draft_text_valid:
            await patch_content_ctx(
                state,
                current_state,
                kind="text",
                text=draft_text,
                entities_json=ctx.draft_entities_json,
                caption=None,
                caption_entities_json=None,
                caption_above=False,
            )
            if current_state == DraftStates.editing_post.state:
                if await _update_draft_from_state(store, query.message, state, user_id=query.from_user.id):
                    return
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(store, query.from_user.id))
                return
            if current_state == DraftStates.collecting_post.state:
                await _prompt_draft_scope(store, query.message, state, user_id=query.from_user.id)
                return
            await _send_confirmation(query.message, state, store, user_id=query.from_user.id)
            return

        await query.message.answer(tr(lang, "post_need_content"), reply_markup=_media_collect_kb(lang))

    async def _abort_incomplete_session(message: Message, state: FSMContext, *, user_id: int, lang: str) -> None:
        # A mid-flow deploy can leave flat FSM keys defaulted to None (see
        # contexts.py); bare int(None) below would crash after the callback was
        # already answered, leaving the user with no feedback. Recover instead.
        await state.clear()
        await message.answer(tr(lang, "cancelled"), reply_markup=await _main_menu_for(store, user_id))

    async def _send_confirmation(message: Message, state: FSMContext, store_: StateStore, *, user_id: int) -> None:
        lang = await _user_lang(store, user_id)
        current_state = await state.get_state()
        ctx = await _get_content_ctx(state, current_state)
        is_repeat = current_state == RepeatStates.collecting_post.state
        is_broadcast = current_state == BroadcastStates.collecting_post.state
        tz_name = await store_.get_user_timezone(user_id) or "UTC"
        if ctx.scheduled_at_utc is None or (not is_broadcast and ctx.chat_id is None):
            await _abort_incomplete_session(message, state, user_id=user_id, lang=lang)
            return
        local_time = _format_local(int(ctx.scheduled_at_utc), tz_name)
        kind = ctx.kind
        if kind == "text":
            summary = tr(lang, "kind_text")
            preview = _draft_preview_text(ctx.text, fallback=tr(lang, "draft_preview_empty"), limit=240)
        else:
            media = list(ctx.media_items)
            summary = tr(lang, "kind_media", count=len(media))
            preview = _draft_preview_text(
                ctx.caption,
                fallback=tr(lang, "draft_preview_media_no_caption"),
                limit=240,
            )
        if is_repeat:
            await state.set_state(RepeatStates.confirming)
            chat_id = int(ctx.chat_id)
            title = await store_.get_destination_title(chat_id) or str(chat_id)
            interval_label = _repeat_interval_label(lang, str(ctx.interval_type or ""))
            text = tr(
                lang,
                "repeat_confirm_template",
                where=title,
                local_time=local_time,
                tz_name=tz_name,
                interval=interval_label,
                kind=summary,
            )
        elif is_broadcast:
            selected_chat_ids, where_lines = await _resolve_broadcast_destination_lines(
                store,
                user_id,
                _normalize_selected_chat_ids(ctx.selected_chat_ids),
            )
            if not selected_chat_ids:
                await patch_broadcast_ctx(state, selected_chat_ids=[], dest_page=0)
                await state.set_state(BroadcastStates.choosing_destinations)
                await _render_broadcast_destinations(store, message, state, user_id=user_id, page=0, edit=False)
                return
            await patch_broadcast_ctx(state, selected_chat_ids=selected_chat_ids)
            await state.set_state(BroadcastStates.confirming)
            text = tr(
                lang,
                "broadcast_confirm_template",
                count=len(selected_chat_ids),
                where_lines=where_lines,
                local_time=local_time,
                tz_name=tz_name,
                kind=summary,
                preview=preview,
            )
        else:
            await state.set_state(ScheduleStates.confirming)
            chat_id = int(ctx.chat_id)
            title = await store_.get_destination_title(chat_id) or str(chat_id)
            text = tr(lang, "confirm_template", where=title, local_time=local_time, tz_name=tz_name, kind=summary)
        await message.answer(text, reply_markup=_confirm_kb(lang))

    @router.callback_query(F.data == "sconf:yes")
    async def cb_confirm_yes(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if current_state not in {
            ScheduleStates.confirming.state,
            RepeatStates.confirming.state,
            DraftStates.confirming.state,
            BroadcastStates.confirming.state,
        }:
            await query.answer()
            return

        lang = await _user_lang(store, query.from_user.id)
        await query.answer()
        ctx = await _get_content_ctx(state, current_state)
        user_id = query.from_user.id
        if ctx.scheduled_at_utc is None:
            await _abort_incomplete_session(query.message, state, user_id=user_id, lang=lang)
            return
        scheduled_at_utc = int(ctx.scheduled_at_utc)
        kind = ctx.kind
        tz_name = await store.get_user_timezone(user_id) or "UTC"

        if current_state == BroadcastStates.confirming.state:
            resolved_destinations = await broadcast_svc.resolve_valid_destinations(
                store,
                user_id,
                _normalize_selected_chat_ids(ctx.selected_chat_ids),
            )
            if not resolved_destinations:
                await patch_broadcast_ctx(state, selected_chat_ids=[], dest_page=0)
                await state.set_state(BroadcastStates.choosing_destinations)
                await _render_broadcast_destinations(store, query.message, state, user_id=user_id, page=0, edit=False)
                return

            selected_chat_ids = [chat_id for chat_id, _ in resolved_destinations]
            await patch_broadcast_ctx(state, selected_chat_ids=selected_chat_ids)
            for chat_id, _ in resolved_destinations:
                ok, err = await _check_user_admin(query.bot, chat_id=chat_id, user_id=user_id, lang=lang)
                if not ok:
                    await query.message.answer(err, reply_markup=_confirm_kb(lang))
                    return
                ok, err = await _check_bot_admin_and_post(query.bot, chat_id=chat_id, lang=lang)
                if not ok:
                    await query.message.answer(err, reply_markup=_confirm_kb(lang))
                    return

            if kind == "text":
                content = broadcast_svc.PostContent(kind="text", text=ctx.text,
                                                    entities_json=ctx.entities_json)
            else:
                content = broadcast_svc.PostContent(
                    kind="media", caption=ctx.caption,
                    caption_entities_json=ctx.caption_entities_json,
                    caption_above=bool(ctx.caption_above),
                    media_items=list(ctx.media_items),
                )
            try:
                post_ids = await broadcast_svc.create_broadcast(
                    store, user_id=user_id, chat_ids=selected_chat_ids,
                    scheduled_at_utc=scheduled_at_utc, content=content,
                )
            except ResourceLimitError as exc:
                await state.clear()
                await query.message.answer(
                    limit_message(lang, exc),
                    reply_markup=await _main_menu_for(store, query.from_user.id),
                )
                return

            await state.clear()
            local_time = _format_local(scheduled_at_utc, tz_name)
            lines = "\n".join(
                f"- {label}: id={_short_id(post_id)}"
                for (_, label), post_id in zip(resolved_destinations, post_ids)
            )
            await query.message.answer(
                tr(
                    lang,
                    "broadcast_created_ok",
                    count=len(post_ids),
                    local_time=local_time,
                    tz_name=tz_name,
                    lines=lines,
                ),
                reply_markup=await _main_menu_for(store, query.from_user.id),
            )
            return

        if current_state == RepeatStates.confirming.state:
            if ctx.chat_id is None:
                await _abort_incomplete_session(query.message, state, user_id=user_id, lang=lang)
                return
            chat_id = int(ctx.chat_id)
            ok, err = await _check_user_admin(query.bot, chat_id=chat_id, user_id=user_id, lang=lang)
            if not ok:
                await query.message.answer(err, reply_markup=await _main_menu_for(store, query.from_user.id))
                await state.clear()
                return
            ok, err = await _check_bot_admin_and_post(query.bot, chat_id=chat_id, lang=lang)
            if not ok:
                await query.message.answer(err, reply_markup=await _main_menu_for(store, query.from_user.id))
                await state.clear()
                return

            local_dt = datetime.fromtimestamp(scheduled_at_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
            time_of_day_minutes = local_dt.hour * 60 + local_dt.minute
            interval_type = str(ctx.interval_type or "")
            try:
                pattern_id, post_id = await store.create_recurring_series(
                    user_id=user_id,
                    chat_id=chat_id,
                    interval_type=interval_type,
                    weekdays_mask=_repeat_weekdays_mask(interval_type),
                    time_of_day_minutes=time_of_day_minutes,
                    timezone=tz_name,
                    start_at_utc=scheduled_at_utc,
                    kind=str(kind or ""),
                    text=str(ctx.text or "") if kind == "text" else None,
                    entities_json=ctx.entities_json if kind == "text" else None,
                    caption=ctx.caption if kind == "media" else None,
                    caption_entities_json=ctx.caption_entities_json if kind == "media" else None,
                    caption_above=bool(ctx.caption_above) if kind == "media" else None,
                    media_items=list(ctx.media_items) if kind == "media" else None,
                )
            except ResourceLimitError as exc:
                await state.clear()
                await query.message.answer(
                    limit_message(lang, exc),
                    reply_markup=await _main_menu_for(store, query.from_user.id),
                )
                return
            await state.clear()
            local_time = _format_local(scheduled_at_utc, tz_name)
            await query.message.answer(
                tr(
                    lang,
                    "repeat_created_ok",
                    interval=_repeat_interval_label(lang, interval_type),
                    local_time=local_time,
                    tz_name=tz_name,
                    pattern_id=_short_id(pattern_id),
                ),
                reply_markup=await _main_menu_for(store, query.from_user.id),
            )
            return

        if current_state == DraftStates.confirming.state:
            draft_id = ctx.draft_publish_id
            if not isinstance(draft_id, str):
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(store, query.from_user.id))
                return

            draft = await draft_svc.resolve_publishable_draft(store, draft_id, user_id)
            if draft is None:
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(store, query.from_user.id))
                return

            ok, err = await _check_user_admin(query.bot, chat_id=draft.chat_id, user_id=user_id, lang=lang)
            if not ok:
                await query.message.answer(err, reply_markup=await _main_menu_for(store, query.from_user.id))
                await state.clear()
                return
            ok, err = await _check_bot_admin_and_post(query.bot, chat_id=draft.chat_id, lang=lang)
            if not ok:
                await query.message.answer(err, reply_markup=await _main_menu_for(store, query.from_user.id))
                await state.clear()
                return

            try:
                post_id = await draft_svc.publish_draft(
                    store, user_id=user_id, draft=draft, scheduled_at_utc=scheduled_at_utc
                )
            except ResourceLimitError as exc:
                await state.clear()
                await query.message.answer(
                    limit_message(lang, exc),
                    reply_markup=await _main_menu_for(store, query.from_user.id),
                )
                return

            await state.clear()
            local_time = _format_local(scheduled_at_utc, tz_name)
            await query.message.answer(
                tr(
                    lang,
                    "draft_post_created_ok",
                    draft_id=_short_id(draft.id),
                    local_time=local_time,
                    tz_name=tz_name,
                    post_id=_short_id(post_id),
                ),
                reply_markup=await _main_menu_for(store, query.from_user.id),
            )
            return

        if ctx.chat_id is None:
            await _abort_incomplete_session(query.message, state, user_id=user_id, lang=lang)
            return
        chat_id = int(ctx.chat_id)
        ok, err = await _check_user_admin(query.bot, chat_id=chat_id, user_id=user_id, lang=lang)
        if not ok:
            await query.message.answer(err, reply_markup=await _main_menu_for(store, query.from_user.id))
            await state.clear()
            return
        ok, err = await _check_bot_admin_and_post(query.bot, chat_id=chat_id, lang=lang)
        if not ok:
            await query.message.answer(err, reply_markup=await _main_menu_for(store, query.from_user.id))
            await state.clear()
            return

        try:
            if kind == "text":
                post_id = await store.create_scheduled_text_post(
                    user_id=user_id,
                    chat_id=chat_id,
                    scheduled_at_utc=scheduled_at_utc,
                    text=str(ctx.text or ""),
                    entities_json=ctx.entities_json,
                )
            else:
                media_items: list[dict[str, str]] = list(ctx.media_items)
                post_id = await store.create_scheduled_media_post(
                    user_id=user_id,
                    chat_id=chat_id,
                    scheduled_at_utc=scheduled_at_utc,
                    caption=ctx.caption,
                    caption_entities_json=ctx.caption_entities_json,
                    caption_above=bool(ctx.caption_above),
                    media_items=media_items,
                )
        except ResourceLimitError as exc:
            await state.clear()
            await query.message.answer(
                limit_message(lang, exc),
                reply_markup=await _main_menu_for(store, query.from_user.id),
            )
            return

        await state.clear()
        await state.set_state(ScheduleStates.entering_datetime)
        await patch_schedule_ctx(state, chat_id=chat_id, selected_date=None, calendar_year=None, calendar_month=None)
        local_time = _format_local(scheduled_at_utc, tz_name)
        title = await store.get_destination_title(chat_id) or str(chat_id)
        await query.message.answer(
            tr(lang, "scheduled_ok", local_time=local_time, tz_name=tz_name, post_id=_short_id(post_id)),
        )
        await _prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "schedule_next_prompt", where=title),
            data=await state.get_data(),
            state_name=ScheduleStates.entering_datetime.state,
        )

    @router.callback_query(F.data == "scancel")
    async def cb_cancel(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(store, query.from_user.id)
        await query.answer()
        await state.clear()
        await query.message.answer(tr(lang, "cancelled"), reply_markup=await _main_menu_for(store, query.from_user.id))

    @router.my_chat_member()
    async def on_my_chat_member(event) -> None:
        # event: ChatMemberUpdated
        chat = event.chat
        new = event.new_chat_member
        status = getattr(new, "status", "unknown")
        can_post = getattr(new, "can_post_messages", None)

        await store.upsert_destination(
            chat_id=chat.id,
            type_=chat.type,
            title=chat.title or (chat.username or str(chat.id)),
            username=chat.username,
            bot_status=status,
            bot_can_post=can_post,
        )

        from_user = getattr(event, "from_user", None)
        if from_user:
            await store.ensure_user(from_user.id)
            try:
                await store.link_user_destination(from_user.id, chat.id, linked_via="my_chat_member")
            except ResourceLimitError as exc:
                # At the destination cap: don't auto-link. Best-effort DM notice
                # (silently ignore if the user never started the bot).
                try:
                    lang = await _user_lang(store, from_user.id)
                    await event.bot.send_message(from_user.id, limit_message(lang, exc))
                except Exception:
                    pass

    return router
