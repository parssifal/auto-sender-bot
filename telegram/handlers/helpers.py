from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import Message, ReplyKeyboardMarkup

from core.services._shared import _resolve_draft_id, _resolve_team_id  # noqa: F401  # re-export for existing callers
from core.state import Destination, DraftRow, RecurringPattern, ScheduledPostRow, StateStore
from core.utils import validate_schedule_time
from telegram.i18n import DEFAULT_LANGUAGE, key_values, normalize_language, resolve_timezone_choice, tr
from telegram.handlers.contexts import (
    BroadcastContext,
    DraftContext,
    EditContext,
    RepeatContext,
    ScheduleContext,
    field_names,
)
from telegram.handlers.states import (
    BroadcastStates,
    DraftStates,
    EditStates,
    RepeatStates,
    ScheduleStates,
)
from telegram.handlers.keyboards import (
    _broadcast_destinations_kb,
    _confirm_kb,
    _destinations_kb,
    _draft_create_scope_kb,
    _draft_location_label,
    _draft_preview_text,
    _format_local,
    _format_selected_date,
    _main_menu_kb,
    _media_collect_kb,
    _normalize_selected_chat_ids,
    _schedule_datetime_markup,
    _short_id,
)


# --- Typed FSM context getters (Phase 4) ---------------------------------
# Read-only typed access over flat FSM storage. Each getter hydrates its
# dataclass from ONLY the keys present in state, so a partially-populated or
# pre-deploy flat-key session reads back cleanly (absent keys → defaults).
# Writes stay as `state.update_data(**flat_keys)` at the call sites.


async def _get_ctx(state, cls):
    data = await state.get_data()
    keys = field_names(cls)
    return cls(**{k: v for k, v in data.items() if k in keys})


async def get_schedule_ctx(state) -> ScheduleContext:
    return await _get_ctx(state, ScheduleContext)


async def get_repeat_ctx(state) -> RepeatContext:
    return await _get_ctx(state, RepeatContext)


async def get_broadcast_ctx(state) -> BroadcastContext:
    return await _get_ctx(state, BroadcastContext)


async def get_draft_ctx(state) -> DraftContext:
    return await _get_ctx(state, DraftContext)


async def get_edit_ctx(state) -> EditContext:
    return await _get_ctx(state, EditContext)


# Keys of the reply-keyboard main-menu buttons (see keyboards._main_menu_kb).
# Single source of truth for the labels that must interrupt the compose/datetime
# flow so command/menu handlers in the feature routers can run instead.
_MENU_TEXT_KEYS = (
    "menu_schedule",
    "menu_queue",
    "menu_destinations",
    "menu_timezone",
    "menu_language",
)


def menu_button_texts() -> frozenset[str]:
    """Union of all reply-keyboard menu-button labels across every language."""
    texts: set[str] = set()
    for key in _MENU_TEXT_KEYS:
        texts.update(key_values(key))
    return frozenset(texts)


def _is_datetime_entry_state(state_name: str | None) -> bool:
    return state_name in {
        ScheduleStates.entering_datetime.state,
        RepeatStates.entering_datetime.state,
        DraftStates.entering_datetime.state,
        BroadcastStates.entering_datetime.state,
        EditStates.entering_datetime.state,
    }


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


async def _move_repeat_to_destination_selection(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    scheduled_at_utc: int,
    scheduled_local: str,
) -> None:
    await state.update_data(
        scheduled_at_utc=scheduled_at_utc,
        scheduled_local=scheduled_local,
        chat_id=None,
        dest_page=0,
    )
    await state.set_state(RepeatStates.choosing_destination)
    await _render_destinations(store, message, page=0, user_id=user_id, select_prefix="rdsel", page_prefix="rdpage")


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


async def _user_lang(store: StateStore, user_id: int) -> str:
    saved = await store.get_user_language(user_id)
    return normalize_language(saved)


async def _main_menu_for(store: StateStore, user_id: int) -> ReplyKeyboardMarkup:
    return _main_menu_kb(await _user_lang(store, user_id))


async def _render_destinations(
    store: StateStore,
    message: Message,
    page: int,
    *,
    user_id: int,
    select_prefix: str = "sdsel",
    page_prefix: str = "sdpage",
) -> None:
    lang = await _user_lang(store, user_id)
    page_size = 5
    offset = page * page_size
    items = await store.list_user_destinations(user_id=user_id, offset=offset, limit=page_size + 1)
    has_more = len(items) > page_size
    items = items[:page_size]
    if not items:
        await message.answer(
            tr(lang, "no_destinations"),
            reply_markup=await _main_menu_for(store, user_id),
        )
        return
    await message.answer(
        tr(lang, "choose_destination"),
        reply_markup=_destinations_kb(
            items,
            page=page,
            has_more=has_more,
            select_prefix=select_prefix,
            page_prefix=page_prefix,
        ),
    )


async def _list_all_user_destinations(store: StateStore, user_id: int) -> list[Destination]:
    total = await store.count_user_destinations(user_id)
    if total <= 0:
        return []
    return await store.list_user_destinations(user_id=user_id, offset=0, limit=total)


async def _render_broadcast_destinations(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    page: int,
    edit: bool,
) -> None:
    lang = await _user_lang(store, user_id)
    page_size = 5
    current_page = max(page, 0)

    while True:
        offset = current_page * page_size
        items = await store.list_user_destinations(user_id=user_id, offset=offset, limit=page_size + 1)
        if items or current_page == 0:
            break
        current_page -= 1

    has_more = len(items) > page_size
    items = items[:page_size]
    await state.update_data(dest_page=current_page)
    if not items:
        if edit:
            await _clear_inline_markup(message)
        await message.answer(
            tr(lang, "no_destinations"),
            reply_markup=await _main_menu_for(store, user_id),
        )
        return

    ctx = await get_broadcast_ctx(state)
    selected_chat_ids = _normalize_selected_chat_ids(ctx.selected_chat_ids)
    text = tr(lang, "broadcast_choose_destinations", count=len(selected_chat_ids))
    reply_markup = _broadcast_destinations_kb(
        items,
        page=current_page,
        has_more=has_more,
        selected_chat_ids=selected_chat_ids,
        lang=lang,
    )
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def _resolve_broadcast_destinations(store: StateStore, user_id: int, selected_chat_ids: list[int]) -> list[tuple[int, str]]:
    from core.services import broadcast_svc

    return await broadcast_svc.resolve_valid_destinations(store, user_id, selected_chat_ids)


async def _resolve_broadcast_destination_lines(store: StateStore, user_id: int, selected_chat_ids: list[int]) -> tuple[list[int], str]:
    resolved_destinations = await _resolve_broadcast_destinations(store, user_id, selected_chat_ids)
    valid_chat_ids = [chat_id for chat_id, _ in resolved_destinations]
    labels = [f"- {label}" for _, label in resolved_destinations]
    return valid_chat_ids, "\n".join(labels)


async def _build_scheduled_post_summary(store: StateStore, post: ScheduledPostRow, *, lang: str) -> dict[str, str]:
    where = await store.get_destination_title(post.chat_id) or str(post.chat_id)
    if post.kind == "text":
        kind = tr(lang, "kind_text")
        preview = _draft_preview_text(
            post.text,
            fallback=tr(lang, "draft_preview_empty"),
            limit=80,
        )
    else:
        media_count = len(await store.get_post_media(post.id))
        kind = tr(lang, "kind_media", count=media_count)
        preview = _draft_preview_text(
            post.caption,
            fallback=tr(lang, "draft_preview_media_no_caption"),
            limit=80,
        )
    return {
        "where": where,
        "kind": kind,
        "preview": preview,
    }


async def _clear_live_preview(bot: Bot, state: FSMContext | None) -> None:
    """Delete the previously-sent preview messages, if any (best-effort)."""
    if state is None:
        return
    data = await state.get_data()
    chat_id = data.get("preview_chat_id")
    msg_ids = data.get("preview_msg_ids") or []
    if chat_id is not None and msg_ids:
        for msg_id in msg_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                # Already deleted, too old (>48h), or otherwise gone — ignore.
                pass
    if "preview_msg_ids" in data or "preview_chat_id" in data:
        await state.update_data(preview_msg_ids=[], preview_chat_id=None)


async def _send_post_preview(
    store: StateStore, message: Message, *, user_id: int, post_id: str, state: FSMContext | None = None
) -> None:
    lang = await _user_lang(store, user_id)
    post = await store.get_scheduled_post(post_id)
    if post is None or post.user_id != user_id:
        await message.answer(tr(lang, "view_not_found"), reply_markup=await _main_menu_for(store, user_id))
        return

    # Replace any previous live preview instead of stacking a new one.
    await _clear_live_preview(message.bot, state)

    sent_ids: list[int] = []

    tz_name = await store.get_user_timezone(user_id) or "UTC"
    summary = await _build_scheduled_post_summary(store, post, lang=lang)
    info_msg = await message.answer(
        tr(
            lang,
            "view_post_info",
            post_id=_short_id(post.id),
            where=summary["where"],
            local_time=_format_local(post.scheduled_at_utc, tz_name),
            tz_name=tz_name,
            kind=summary["kind"],
        )
    )
    if info_msg is not None:
        sent_ids.append(info_msg.message_id)

    if post.kind == "text" and post.text:
        import json as _json
        from aiogram.types import MessageEntity as _ME
        entities = [_ME.model_validate(e) for e in _json.loads(post.entities_json)] if post.entities_json else None
        body_msg = await message.answer(post.text, entities=entities)
        if body_msg is not None:
            sent_ids.append(body_msg.message_id)
    elif post.kind == "media":
        media_items = await store.get_post_media(post.id)
        if media_items:
            from core.notifier import send_media_post
            stats = await send_media_post(
                bot=message.bot,
                chat_id=message.chat.id,
                media_items=media_items,
                caption=post.caption,
                caption_entities_json=post.caption_entities_json,
                caption_above=post.caption_above,
            )
            sent_ids.extend(stats.message_ids)

    if state is not None:
        await state.update_data(preview_msg_ids=sent_ids, preview_chat_id=message.chat.id)


async def _build_draft_summary(store: StateStore, draft: DraftRow, *, lang: str) -> dict[str, str]:
    where = await store.get_destination_title(draft.chat_id) or str(draft.chat_id)
    team_name: str | None = None
    if draft.team_id is not None:
        team = await store.get_team(draft.team_id)
        team_name = team.name if team is not None else _short_id(draft.team_id)

    if draft.kind == "text":
        kind = tr(lang, "kind_text")
        preview = _draft_preview_text(
            draft.text,
            fallback=tr(lang, "draft_preview_empty"),
            limit=80,
        )
    else:
        media_count = len(await store.get_draft_media(draft.id))
        kind = tr(lang, "kind_media", count=media_count)
        preview = _draft_preview_text(
            draft.caption,
            fallback=tr(lang, "draft_preview_media_no_caption"),
            limit=80,
        )

    return {
        "where": where,
        "location": _draft_location_label(lang, team_name),
        "kind": kind,
        "preview": preview,
    }


async def _save_draft_from_state(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    team_id: str | None,
) -> bool:
    ctx = await get_draft_ctx(state)
    chat_id = ctx.chat_id
    kind = ctx.kind
    lang = await _user_lang(store, user_id)
    if not isinstance(chat_id, int) or kind not in {"text", "media"}:
        return False

    try:
        if kind == "text":
            draft_id = await store.create_draft(
                author_user_id=user_id,
                team_id=team_id,
                chat_id=chat_id,
                kind="text",
                text=ctx.text,
                entities_json=ctx.entities_json,
            )
            kind_label = tr(lang, "kind_text")
        else:
            media_items: list[dict[str, str]] = list(ctx.media_items)
            draft_id = await store.create_draft(
                author_user_id=user_id,
                team_id=team_id,
                chat_id=chat_id,
                kind="media",
                caption=ctx.caption,
                caption_entities_json=ctx.caption_entities_json,
                caption_above=bool(ctx.caption_above),
                media_items=media_items,
            )
            kind_label = tr(lang, "kind_media", count=len(media_items))
    except ValueError:
        return False

    team_name: str | None = None
    if team_id is not None:
        team = await store.get_team(team_id)
        team_name = team.name if team is not None else _short_id(team_id)
    where = await store.get_destination_title(chat_id) or str(chat_id)

    await state.clear()
    await message.answer(
        tr(
            lang,
            "draft_created_ok",
            draft_id=_short_id(draft_id),
            location=_draft_location_label(lang, team_name),
            where=where,
            kind=kind_label,
        ),
        reply_markup=await _main_menu_for(store, user_id),
    )
    return True


async def _prompt_draft_scope(store: StateStore, message: Message, state: FSMContext, *, user_id: int) -> None:
    lang = await _user_lang(store, user_id)
    writable_teams = await store.list_writable_teams(user_id)
    if not writable_teams:
        await _save_draft_from_state(store, message, state, user_id=user_id, team_id=None)
        return

    await state.set_state(DraftStates.choosing_scope)
    await message.answer(
        tr(lang, "draft_create_scope_prompt"),
        reply_markup=_draft_create_scope_kb(writable_teams, lang),
    )


async def _update_draft_from_state(store: StateStore, message: Message, state: FSMContext, *, user_id: int) -> bool:
    ctx = await get_draft_ctx(state)
    draft_id = ctx.edit_draft_id
    chat_id = ctx.chat_id
    team_id = ctx.team_id
    kind = ctx.kind
    lang = await _user_lang(store, user_id)
    if not isinstance(draft_id, str) or not isinstance(chat_id, int) or kind not in {"text", "media"}:
        return False

    try:
        if kind == "text":
            updated = await store.update_draft(
                draft_id,
                user_id,
                chat_id=chat_id,
                kind="text",
                text=ctx.text,
                entities_json=ctx.entities_json,
            )
            kind_label = tr(lang, "kind_text")
        else:
            media_items: list[dict[str, str]] = list(ctx.media_items)
            updated = await store.update_draft(
                draft_id,
                user_id,
                chat_id=chat_id,
                kind="media",
                caption=ctx.caption,
                caption_entities_json=ctx.caption_entities_json,
                caption_above=bool(ctx.caption_above),
                media_items=media_items,
            )
            kind_label = tr(lang, "kind_media", count=len(media_items))
    except ValueError:
        return False

    if not updated:
        return False

    team_name: str | None = None
    if isinstance(team_id, str):
        team = await store.get_team(team_id)
        team_name = team.name if team is not None else _short_id(team_id)
    where = await store.get_destination_title(chat_id) or str(chat_id)

    await state.clear()
    await message.answer(
        tr(
            lang,
            "draft_updated_ok",
            draft_id=_short_id(draft_id),
            location=_draft_location_label(lang, team_name),
            where=where,
            kind=kind_label,
        ),
        reply_markup=await _main_menu_for(store, user_id),
    )
    return True


async def _move_draft_publish_to_confirmation(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    scheduled_at_utc: int,
    scheduled_local: str,
) -> None:
    lang = await _user_lang(store, user_id)
    draft_id = (await state.get_data()).get("draft_publish_id")
    if not isinstance(draft_id, str):
        await state.clear()
        await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(store, user_id))
        return

    permissions = await store.get_draft_permissions(draft_id, user_id)
    draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_publish else None
    if draft is None or permissions is None or not permissions.can_publish:
        await state.clear()
        await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(store, user_id))
        return

    await state.update_data(
        scheduled_at_utc=scheduled_at_utc,
        scheduled_local=scheduled_local,
        chat_id=draft.chat_id,
    )
    await state.set_state(DraftStates.confirming)

    tz_name = await store.get_user_timezone(user_id) or "UTC"
    local_time = _format_local(scheduled_at_utc, tz_name)
    summary = await _build_draft_summary(store, draft, lang=lang)
    text = tr(lang, "confirm_template", where=summary["where"], local_time=local_time, tz_name=tz_name, kind=summary["kind"])
    await message.answer(text, reply_markup=_confirm_kb(lang))


async def _load_pending_post_for_edit(store: StateStore, user_id: int, post_id: str) -> tuple[ScheduledPostRow | None, str | None]:
    post = await store.get_scheduled_post(post_id)
    if post is None or post.user_id != user_id:
        return None, "missing"
    if post.status != "pending":
        return None, "unavailable"
    if await store.get_recurring_instance_by_post_id(post_id) is not None:
        return None, "recurring"
    return post, None


async def _send_edit_unavailable(store: StateStore, message: Message, *, user_id: int, reason: str) -> None:
    lang = await _user_lang(store, user_id)
    key = "edit_post_recurring_blocked" if reason == "recurring" else "edit_post_missing"
    await message.answer(tr(lang, key), reply_markup=await _main_menu_for(store, user_id))


async def _save_scheduled_post_time(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    scheduled_at_utc: int,
) -> bool:
    ctx = await get_edit_ctx(state)
    post_id = ctx.edit_post_id
    if not isinstance(post_id, str):
        return False

    updated = await store.update_scheduled_post(
        post_id,
        user_id,
        {"scheduled_at_utc": scheduled_at_utc},
    )
    if not updated:
        await state.clear()
        _, reason = await _load_pending_post_for_edit(store, user_id, post_id)
        await _send_edit_unavailable(store, message, user_id=user_id, reason=str(reason or "missing"))
        return False

    lang = await _user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    await state.clear()
    await message.answer(
        tr(
            lang,
            "edit_time_updated_ok",
            post_id=_short_id(post_id),
            local_time=_format_local(scheduled_at_utc, tz_name),
            tz_name=tz_name,
        ),
        reply_markup=await _main_menu_for(store, user_id),
    )
    return True


async def _save_scheduled_post_media(store: StateStore, message: Message, state: FSMContext, *, user_id: int) -> bool:
    ctx = await get_edit_ctx(state)
    post_id = ctx.edit_post_id
    if not isinstance(post_id, str):
        return False

    media_items = list(ctx.media_items)
    if not media_items:
        return False

    draft_text = ctx.draft_text
    draft_text_valid = bool(str(draft_text).strip()) if draft_text is not None else False
    updated = await store.update_scheduled_post(
        post_id,
        user_id,
        {
            "kind": "media",
            "caption": draft_text if draft_text_valid else None,
            "caption_entities_json": ctx.draft_entities_json if draft_text_valid else None,
            "caption_above": bool(ctx.caption_above) if draft_text_valid else None,
            "media_items": media_items,
        },
    )
    if not updated:
        await state.clear()
        await _send_edit_unavailable(store, message, user_id=user_id, reason="missing")
        return False

    lang = await _user_lang(store, user_id)
    await state.clear()
    await message.answer(
        tr(
            lang,
            "edit_media_updated_ok",
            post_id=_short_id(post_id),
            kind=tr(lang, "kind_media", count=len(media_items)),
        ),
        reply_markup=await _main_menu_for(store, user_id),
    )
    return True
