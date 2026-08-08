from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from core.state import ScheduledPostRow, StateStore
from telegram.i18n import key_values, tr
from telegram.handlers import states, keyboards as kb, helpers as h


_MENU_QUEUE_TEXTS = key_values("menu_queue")


async def _render_edit_posts(store: StateStore, message: Message, *, user_id: int, page: int = 0, edit: bool = False) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    page_size = 8
    while True:
        offset = page * page_size
        posts = await store.list_editable_pending_posts(user_id=user_id, limit=page_size + 1, offset=offset)
        if posts or page == 0:
            break
        page -= 1

    has_more = len(posts) > page_size
    posts = posts[:page_size]

    if not posts:
        text = tr(lang, "edit_empty")
        if edit:
            await message.edit_text(text, reply_markup=None)
        else:
            await message.answer(text, reply_markup=await h._main_menu_for(store, user_id))
        return

    lines: list[str] = []
    edit_buttons: list[dict[str, str]] = []
    for post in posts:
        summary = await h._build_scheduled_post_summary(store, post, lang=lang)
        lines.append(
            tr(
                lang,
                "edit_list_item",
                post_id=kb._short_id(post.id),
                where=summary["where"],
                local_time=kb._format_local(post.scheduled_at_utc, tz_name),
                kind=summary["kind"],
                preview=summary["preview"],
            )
        )
        edit_buttons.append({"id": post.id, "label": kb._short_id(post.id)})

    text = tr(lang, "edit_list_header", lines="\n\n".join(lines))
    reply_markup = kb._edit_paged_kb(edit_buttons, page=page, has_more=has_more, lang=lang)
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def _render_delete_posts(store: StateStore, message: Message, *, user_id: int, page: int = 0, edit: bool = False) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    page_size = 8
    while True:
        offset = page * page_size
        posts = await store.list_editable_pending_posts(user_id=user_id, limit=page_size + 1, offset=offset)
        if posts or page == 0:
            break
        page -= 1

    has_more = len(posts) > page_size
    posts = posts[:page_size]

    if not posts:
        text = tr(lang, "delete_empty")
        if edit:
            await message.edit_text(text, reply_markup=None)
        else:
            await message.answer(text, reply_markup=await h._main_menu_for(store, user_id))
        return

    lines: list[str] = []
    delete_buttons: list[dict[str, str]] = []
    for post in posts:
        summary = await h._build_scheduled_post_summary(store, post, lang=lang)
        lines.append(
            tr(
                lang,
                "delete_list_item",
                post_id=kb._short_id(post.id),
                where=summary["where"],
                local_time=kb._format_local(post.scheduled_at_utc, tz_name),
                kind=summary["kind"],
                preview=summary["preview"],
            )
        )
        delete_buttons.append({"id": post.id, "label": kb._short_id(post.id)})

    text = tr(lang, "delete_list_header", lines="\n\n".join(lines))
    reply_markup = kb._delete_paged_kb(delete_buttons, page=page, has_more=has_more, lang=lang)
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


def _app_button(lang: str, webapp_url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=tr(lang, "app_open_btn"),
        web_app=WebAppInfo(url=f"{webapp_url.rstrip('/')}/app"),
    )


async def _render_queue_page(
    store: StateStore,
    message: Message,
    page: int,
    user_id: int,
    *,
    edit: bool = False,
    webapp_url: str | None = None,
) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    page_size = 8
    while True:
        offset = page * page_size
        posts = await store.list_pending_posts(user_id=user_id, limit=page_size + 1, offset=offset)
        if posts or page == 0:
            break
        page -= 1

    has_more = len(posts) > page_size
    posts = posts[:page_size]

    if not posts:
        text = tr(lang, "queue_empty")
        app_kb = (
            InlineKeyboardMarkup(inline_keyboard=[[_app_button(lang, webapp_url)]])
            if webapp_url
            else None
        )
        if edit:
            await message.edit_text(text, reply_markup=app_kb)
        else:
            await message.answer(
                text,
                reply_markup=app_kb if app_kb is not None else await h._main_menu_for(store, user_id),
            )
        return

    lines: list[str] = []
    buttons: list[dict[str, str]] = []
    for p in posts:
        when = kb._format_local(p.scheduled_at_utc, tz_name)
        title = await store.get_destination_title(p.chat_id) or str(p.chat_id)
        label = kb._short_id(p.id)
        if p.kind == "text":
            k = tr(lang, "kind_text")
        else:
            media = await store.get_post_media(p.id)
            k = tr(lang, "kind_media", count=len(media))
        lines.append(f"{label} — {when} — {title} — {k}")
        buttons.append({"id": p.id, "label": label})

    text = tr(lang, "queue_header", lines="\n".join(lines))
    reply_markup = kb._queue_paged_kb(buttons, page=page, has_more=has_more, lang=lang)
    if webapp_url:
        reply_markup.inline_keyboard.append([_app_button(lang, webapp_url)])
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def _start_scheduled_post_edit(store: StateStore, message: Message, state: FSMContext, *, user_id: int, post: ScheduledPostRow) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    summary = await h._build_scheduled_post_summary(store, post, lang=lang)
    await state.clear()
    await h.patch_edit_ctx(
        state,
        edit_post_id=post.id,
        chat_id=post.chat_id,
    )
    await state.set_state(states.EditStates.choosing_field)
    await message.answer(
        tr(
            lang,
            "edit_choose_field",
            post_id=kb._short_id(post.id),
            where=summary["where"],
            local_time=kb._format_local(post.scheduled_at_utc, tz_name),
            tz_name=tz_name,
            kind=summary["kind"],
            preview=summary["preview"],
        ),
        reply_markup=kb._edit_field_kb(post_id=post.id, lang=lang),
    )


async def _start_scheduled_post_text_edit(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    post: ScheduledPostRow,
) -> None:
    lang = await h._user_lang(store, user_id)
    summary = await h._build_scheduled_post_summary(store, post, lang=lang)
    await state.clear()
    await h.patch_edit_ctx(
        state,
        edit_post_id=post.id,
        chat_id=post.chat_id,
    )
    await state.set_state(states.EditStates.entering_text)
    await message.answer(
        tr(
            lang,
            "edit_text_prompt",
            post_id=kb._short_id(post.id),
            kind=summary["kind"],
            preview=summary["preview"],
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")]]
        ),
    )


async def _start_scheduled_post_time_edit(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    post: ScheduledPostRow,
) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id)
    if not tz_name:
        await message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, user_id))
        return

    summary = await h._build_scheduled_post_summary(store, post, lang=lang)
    local_dt = datetime.fromtimestamp(post.scheduled_at_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    await state.clear()
    await h.patch_edit_ctx(
        state,
        edit_post_id=post.id,
        chat_id=post.chat_id,
        selected_date=local_dt.date().isoformat(),
        calendar_year=local_dt.year,
        calendar_month=local_dt.month,
    )
    await state.set_state(states.EditStates.entering_datetime)
    await h._prompt_for_datetime(
        message,
        lang=lang,
        tz_name=tz_name,
        text=tr(
            lang,
            "edit_time_prompt",
            post_id=kb._short_id(post.id),
            where=summary["where"],
            local_time=kb._format_local(post.scheduled_at_utc, tz_name),
            tz_name=tz_name,
        ),
        data=await state.get_data(),
        state_name=states.EditStates.entering_datetime.state,
    )


async def _start_scheduled_post_media_edit(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    post: ScheduledPostRow,
) -> None:
    lang = await h._user_lang(store, user_id)
    summary = await h._build_scheduled_post_summary(store, post, lang=lang)
    existing_text = post.caption if post.kind == "media" else post.text
    existing_entities = post.caption_entities_json if post.kind == "media" else post.entities_json
    existing_caption_above = None if post.caption_above is None else bool(post.caption_above)
    await state.clear()
    await h.patch_edit_ctx(
        state,
        edit_post_id=post.id,
        chat_id=post.chat_id,
        kind=None,
        text=None,
        entities_json=None,
        caption=None,
        caption_entities_json=None,
        caption_above=False if existing_caption_above is None else existing_caption_above,
        media_items=[],
        draft_text=existing_text,
        draft_entities_json=existing_entities,
        text_before_media=post.kind == "text" and bool(existing_text),
        edit_preserve_caption_above=post.kind == "media" and existing_caption_above is not None,
    )
    await state.set_state(states.EditStates.collecting_media)
    await message.answer(
        tr(
            lang,
            "edit_media_prompt",
            post_id=kb._short_id(post.id),
            preview=summary["preview"],
        ),
        reply_markup=kb._media_collect_kb(lang),
    )


async def _send_delete_unavailable(store: StateStore, message: Message, *, user_id: int, reason: str) -> None:
    lang = await h._user_lang(store, user_id)
    key = "delete_post_recurring_blocked" if reason == "recurring" else "delete_post_missing"
    await message.answer(tr(lang, key), reply_markup=await h._main_menu_for(store, user_id))


async def _render_delete_confirm(store: StateStore, message: Message, *, user_id: int, post: ScheduledPostRow) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    summary = await h._build_scheduled_post_summary(store, post, lang=lang)
    await message.answer(
        tr(
            lang,
            "delete_confirm",
            post_id=kb._short_id(post.id),
            where=summary["where"],
            local_time=kb._format_local(post.scheduled_at_utc, tz_name),
            tz_name=tz_name,
            kind=summary["kind"],
            preview=summary["preview"],
        ),
        reply_markup=kb._delete_confirm_kb(post_id=post.id, lang=lang),
    )


async def _confirm_delete_post(store: StateStore, message: Message, *, user_id: int, post_id: str) -> bool:
    deleted = await store.hard_delete_post(user_id=user_id, post_id=post_id)
    if not deleted:
        _, reason = await h._load_pending_post_for_edit(store, user_id, post_id)
        await _send_delete_unavailable(store, message, user_id=user_id, reason=str(reason or "missing"))
        return False

    lang = await h._user_lang(store, user_id)
    await message.answer(
        tr(lang, "delete_post_ok", post_id=kb._short_id(post_id)),
        reply_markup=await h._main_menu_for(store, user_id),
    )
    return True


async def _save_scheduled_post_text(
    store: StateStore,
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    text: str,
    entities_json: str | None,
) -> bool:
    ctx = await h.get_edit_ctx(state)
    post_id = ctx.edit_post_id
    if not isinstance(post_id, str):
        return False

    post, reason = await h._load_pending_post_for_edit(store, user_id, post_id)
    if post is None:
        await state.clear()
        await h._send_edit_unavailable(store, message, user_id=user_id, reason=str(reason or "missing"))
        return False

    lang = await h._user_lang(store, user_id)
    if not str(text).strip():
        await message.answer(tr(lang, "text_required"))
        return False

    if post.kind == "media":
        media_items = await store.get_post_media(post_id)
        updates: dict[str, object] = {
            "kind": "media",
            "caption": text,
            "caption_entities_json": entities_json,
            "caption_above": None if post.caption_above is None else bool(post.caption_above),
            "media_items": media_items,
        }
    else:
        updates = {
            "kind": "text",
            "text": text,
            "entities_json": entities_json,
        }
    updated = await store.update_scheduled_post(post_id, user_id, updates)

    if not updated:
        await state.clear()
        await h._send_edit_unavailable(store, message, user_id=user_id, reason="missing")
        return False

    await state.clear()
    await message.answer(
        tr(lang, "edit_text_updated_ok", post_id=kb._short_id(post_id)),
        reply_markup=await h._main_menu_for(store, user_id),
    )
    return True


def build_router(store: StateStore, *, webapp_url: str | None = None) -> Router:
    router = Router(name="queue")

    @router.message(Command("edit"))
    async def cmd_edit(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_edit_posts(store, message, user_id=message.from_user.id)
            return

        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=200)
        post_id, ambiguous = h._resolve_scheduled_post_id(posts, parts[1].strip().lower())
        lang = await h._user_lang(store, message.from_user.id)
        if post_id is None:
            key = "edit_post_ambiguous" if ambiguous else "edit_post_missing"
            await message.answer(tr(lang, key), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        post, reason = await h._load_pending_post_for_edit(store, message.from_user.id, post_id)
        if post is None:
            await h._send_edit_unavailable(store, message, user_id=message.from_user.id, reason=str(reason or "missing"))
            return

        await _start_scheduled_post_edit(store, message, state, user_id=message.from_user.id, post=post)

    @router.message(Command("delete"))
    async def cmd_delete(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_delete_posts(store, message, user_id=message.from_user.id)
            return

        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=200)
        post_id, ambiguous = h._resolve_scheduled_post_id(posts, parts[1].strip().lower())
        lang = await h._user_lang(store, message.from_user.id)
        if post_id is None:
            key = "delete_post_ambiguous" if ambiguous else "delete_post_missing"
            await message.answer(tr(lang, key), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        post, reason = await h._load_pending_post_for_edit(store, message.from_user.id, post_id)
        if post is None:
            await _send_delete_unavailable(store, message, user_id=message.from_user.id, reason=str(reason or "missing"))
            return

        await _render_delete_confirm(store, message, user_id=message.from_user.id, post=post)

    @router.message(Command("view"))
    async def cmd_view(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        lang = await h._user_lang(store, message.from_user.id)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "view_not_found"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=200)
        post_id, _ = h._resolve_scheduled_post_id(posts, parts[1].strip().lower())
        if post_id is None:
            await message.answer(tr(lang, "view_not_found"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await h._send_post_preview(store, message, user_id=message.from_user.id, post_id=post_id, state=state)

    @router.callback_query(F.data.startswith("epage:"))
    async def cb_edit_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_edit_posts(store, query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("delpage:"))
    async def cb_delete_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_delete_posts(store, query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("qedit:"))
    async def cb_queue_edit(query: CallbackQuery, state: FSMContext) -> None:
        post_id = query.data.split(":", 1)[1]
        post, reason = await h._load_pending_post_for_edit(store, query.from_user.id, post_id)
        lang = await h._user_lang(store, query.from_user.id)
        if post is None:
            key = "edit_post_recurring_blocked" if reason == "recurring" else "edit_post_missing"
            await query.answer(tr(lang, key), show_alert=True)
            return

        await query.answer()
        await _start_scheduled_post_edit(store, query.message, state, user_id=query.from_user.id, post=post)

    @router.callback_query(F.data.startswith("qdelask:"))
    async def cb_queue_delete_prompt(query: CallbackQuery) -> None:
        post_id = query.data.split(":", 1)[1]
        post, reason = await h._load_pending_post_for_edit(store, query.from_user.id, post_id)
        lang = await h._user_lang(store, query.from_user.id)
        if post is None:
            key = "delete_post_recurring_blocked" if reason == "recurring" else "delete_post_missing"
            await query.answer(tr(lang, key), show_alert=True)
            return

        await query.answer()
        await _render_delete_confirm(store, query.message, user_id=query.from_user.id, post=post)

    @router.callback_query(F.data.startswith("qdelyes:"))
    async def cb_queue_delete_confirm(query: CallbackQuery) -> None:
        post_id = query.data.split(":", 1)[1]
        await query.answer()
        await h._clear_inline_markup(query.message)
        await _confirm_delete_post(store, query.message, user_id=query.from_user.id, post_id=post_id)

    @router.callback_query(F.data.startswith("eact:"))
    async def cb_edit_action(query: CallbackQuery, state: FSMContext) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        action = parts[1]
        post_id = parts[2]
        post, reason = await h._load_pending_post_for_edit(store, query.from_user.id, post_id)
        lang = await h._user_lang(store, query.from_user.id)
        if post is None:
            key = "edit_post_recurring_blocked" if reason == "recurring" else "edit_post_missing"
            await query.answer(tr(lang, key), show_alert=True)
            return

        await query.answer()
        if action == "text":
            await _start_scheduled_post_text_edit(store, query.message, state, user_id=query.from_user.id, post=post)
            return
        if action == "time":
            await _start_scheduled_post_time_edit(store, query.message, state, user_id=query.from_user.id, post=post)
            return
        if action == "media":
            await _start_scheduled_post_media_edit(store, query.message, state, user_id=query.from_user.id, post=post)
            return

        await query.message.answer(tr(lang, "edit_post_missing"), reply_markup=await h._main_menu_for(store, query.from_user.id))

    @router.message(states.EditStates.entering_text, h._not_command_or_menu)
    async def edit_enter_text(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        if not message.text:
            await message.answer(tr(lang, "text_required"))
            return
        await _save_scheduled_post_text(
            store,
            message,
            state,
            user_id=message.from_user.id,
            text=message.text,
            entities_json=store.dump_entities(message.entities),
        )

    @router.message(F.text.in_(_MENU_QUEUE_TEXTS))
    @router.message(Command("queue"))
    async def cmd_queue(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        await _render_queue_page(store, message, page=0, user_id=message.from_user.id, webapp_url=webapp_url)

    @router.callback_query(F.data.startswith("qpage:"))
    async def cb_queue_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_queue_page(store, query.message, page=page, user_id=query.from_user.id, edit=True, webapp_url=webapp_url)

    @router.callback_query(F.data.startswith("qview:"))
    async def cb_queue_view(query: CallbackQuery, state: FSMContext) -> None:
        post_id = query.data.split(":", 1)[1]
        await query.answer()
        await h._send_post_preview(store, query.message, user_id=query.from_user.id, post_id=post_id, state=state)

    @router.callback_query(F.data.startswith("qcancel:"))
    async def cb_queue_cancel(query: CallbackQuery) -> None:
        lang = await h._user_lang(store, query.from_user.id)
        post_id = query.data.split(":")[1]
        ok = await store.cancel_post(user_id=query.from_user.id, post_id=post_id)
        await query.answer(tr(lang, "queue_cancel_ok") if ok else tr(lang, "queue_cancel_missing"), show_alert=False)
        if not ok:
            return
        await query.message.answer(tr(lang, "done"), reply_markup=await h._main_menu_for(store, query.from_user.id))

    return router
