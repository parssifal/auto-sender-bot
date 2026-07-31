from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.rbac import DraftPermissions
from core.services import draft_svc
from core.state import DraftRow, StateStore
from telegram.i18n import tr
from telegram.handlers import states, keyboards as kb, helpers as h


async def _move_to_draft_collection(store: StateStore, message: Message, state: FSMContext, *, chat_id: int, lang: str) -> None:
    await h.patch_draft_ctx(
        state,
        chat_id=chat_id,
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
    await state.set_state(states.DraftStates.collecting_post)
    await message.answer(tr(lang, "schedule_post_prompt"), reply_markup=kb._media_collect_kb(lang))


async def _render_drafts(store: StateStore, message: Message, *, user_id: int, scope: str, page: int, edit: bool) -> None:
    lang = await h._user_lang(store, user_id)
    current_scope = kb._normalize_draft_scope(scope)
    page_size = 5
    while True:
        offset = page * page_size
        items = await store.list_drafts(user_id=user_id, scope=current_scope, offset=offset, limit=page_size + 1)
        if items or page == 0:
            break
        page -= 1

    has_more = len(items) > page_size
    items = items[:page_size]
    reply_markup = kb._drafts_manage_kb(items, scope=current_scope, page=page, has_more=has_more, lang=lang)

    if not items:
        text = tr(lang, "draft_list_empty", scope=kb._draft_scope_label(lang, current_scope))
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)
        return

    lines: list[str] = []
    for draft in items:
        summary = await h._build_draft_summary(store, draft, lang=lang)
        lines.append(
            tr(
                lang,
                "draft_list_item",
                draft_id=kb._short_id(draft.id),
                location=summary["location"],
                where=summary["where"],
                kind=summary["kind"],
                preview=summary["preview"],
            )
        )

    text = tr(lang, "draft_list_header", scope=kb._draft_scope_label(lang, current_scope), lines="\n\n".join(lines))
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def _render_draft_detail(
    store: StateStore,
    message: Message,
    *,
    user_id: int,
    scope: str,
    page: int,
    draft: DraftRow,
    permissions: DraftPermissions,
    edit: bool,
) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id) or "UTC"
    summary = await h._build_draft_summary(store, draft, lang=lang)
    text = tr(
        lang,
        "draft_detail_header",
        draft_id=kb._short_id(draft.id),
        location=summary["location"],
        where=summary["where"],
        kind=summary["kind"],
        updated_at=kb._format_local(draft.updated_at, tz_name),
        preview=summary["preview"],
        actions=kb._draft_action_labels(lang, permissions),
    )
    reply_markup = kb._draft_detail_kb(
        draft_id=draft.id,
        permissions=permissions,
        scope=scope,
        page=page,
        lang=lang,
    )
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def _render_draft_delete_confirm(
    store: StateStore,
    message: Message,
    *,
    user_id: int,
    draft: DraftRow,
    scope: str | None,
    page: int | None,
    edit: bool,
) -> None:
    lang = await h._user_lang(store, user_id)
    summary = await h._build_draft_summary(store, draft, lang=lang)
    text = tr(
        lang,
        "draft_delete_confirm",
        draft_id=kb._short_id(draft.id),
        location=summary["location"],
        where=summary["where"],
        kind=summary["kind"],
    )
    if scope is None or page is None:
        reply_markup = kb._draft_delete_command_kb(draft_id=draft.id, lang=lang)
    else:
        reply_markup = kb._draft_delete_confirm_kb(
            draft_id=draft.id,
            scope=kb._normalize_draft_scope(scope),
            page=page,
            lang=lang,
        )
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def _start_draft_edit(store: StateStore, message: Message, state: FSMContext, *, user_id: int, draft: DraftRow) -> None:
    lang = await h._user_lang(store, user_id)
    summary = await h._build_draft_summary(store, draft, lang=lang)
    await state.clear()
    await h.patch_draft_ctx(
        state,
        edit_draft_id=draft.id,
        chat_id=draft.chat_id,
        team_id=draft.team_id,
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
    await state.set_state(states.DraftStates.editing_post)
    await message.answer(
        tr(
            lang,
            "draft_edit_prompt",
            draft_id=kb._short_id(draft.id),
            location=summary["location"],
            where=summary["where"],
            kind=summary["kind"],
        ),
        reply_markup=kb._media_collect_kb(lang),
    )


async def _start_draft_publish(store: StateStore, message: Message, state: FSMContext, *, user_id: int, draft: DraftRow) -> None:
    lang = await h._user_lang(store, user_id)
    tz_name = await store.get_user_timezone(user_id)
    if not tz_name:
        await message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, user_id))
        return

    where = await store.get_destination_title(draft.chat_id) or str(draft.chat_id)
    await state.clear()
    await h.patch_draft_ctx(
        state,
        draft_publish_id=draft.id,
        chat_id=draft.chat_id,
        selected_date=None,
        calendar_year=None,
        calendar_month=None,
    )
    await state.set_state(states.DraftStates.entering_datetime)
    await h._prompt_for_datetime(
        message,
        lang=lang,
        tz_name=tz_name,
        text=kb._draft_post_prompt_text(lang, draft_id=draft.id, where=where),
        data=await state.get_data(),
        state_name=states.DraftStates.entering_datetime.state,
    )


def build_router(store: StateStore) -> Router:
    router = Router(name="drafts")

    @router.message(Command("drafts"))
    async def cmd_drafts(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        await _render_drafts(store, message, user_id=message.from_user.id, scope="all", page=0, edit=False)

    @router.message(Command("draft_create"))
    async def cmd_draft_create(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        await state.set_state(states.DraftStates.choosing_destination)
        await h.patch_draft_ctx(state, dest_page=0)
        await h._render_destinations(store, message, page=0, user_id=message.from_user.id, select_prefix="ddsel", page_prefix="ddpage")

    @router.message(Command("draft_edit"))
    async def cmd_draft_edit(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_drafts(store, message, user_id=message.from_user.id, scope="all", page=0, edit=False)
            return

        draft_ref = parts[1].strip().lower()
        draft, permissions = await draft_svc.resolve_draft_by_ref(store, message.from_user.id, draft_ref, need="edit")
        if draft is None:
            lang = await h._user_lang(store, message.from_user.id)
            await message.answer(tr(lang, "draft_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await _start_draft_edit(store, message, state, user_id=message.from_user.id, draft=draft)

    @router.message(Command("draft_delete"))
    async def cmd_draft_delete(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        lang = await h._user_lang(store, message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "draft_delete_usage"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        draft_ref = parts[1].strip().lower()
        draft, permissions = await draft_svc.resolve_draft_by_ref(store, message.from_user.id, draft_ref, need="delete")
        if draft is None:
            await message.answer(tr(lang, "draft_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await _render_draft_delete_confirm(
            store,
            message,
            user_id=message.from_user.id,
            draft=draft,
            scope=None,
            page=None,
            edit=False,
        )

    @router.message(Command("draft_post"))
    async def cmd_draft_post(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_drafts(store, message, user_id=message.from_user.id, scope="all", page=0, edit=False)
            return

        draft_ref = parts[1].strip().lower()
        draft, permissions = await draft_svc.resolve_draft_by_ref(store, message.from_user.id, draft_ref, need="publish")
        if draft is None:
            lang = await h._user_lang(store, message.from_user.id)
            await message.answer(tr(lang, "draft_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await _start_draft_publish(store, message, state, user_id=message.from_user.id, draft=draft)

    @router.callback_query(F.data.startswith("dscope:"))
    async def cb_drafts_scope(query: CallbackQuery) -> None:
        scope = kb._normalize_draft_scope(query.data.split(":", 1)[1])
        await query.answer()
        await _render_drafts(store, query.message, user_id=query.from_user.id, scope=scope, page=0, edit=True)

    @router.callback_query(F.data.startswith("dpage:"))
    async def cb_drafts_page(query: CallbackQuery) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        scope = kb._normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        await query.answer()
        await _render_drafts(store, query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)

    @router.callback_query(F.data.startswith("dback:"))
    async def cb_draft_back(query: CallbackQuery) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        scope = kb._normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        await query.answer()
        await _render_drafts(store, query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)

    @router.callback_query(F.data.startswith("dopen:"))
    async def cb_draft_open(query: CallbackQuery) -> None:
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            await query.answer()
            return

        scope = kb._normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        draft_id = parts[3]
        lang = await h._user_lang(store, query.from_user.id)
        draft, permissions = await draft_svc.resolve_draft_by_id(store, draft_id, query.from_user.id, need="view")
        if draft is None:
            await query.answer(tr(lang, "draft_missing"), show_alert=True)
            if (query.message.text or "").startswith("draft="):
                await _render_drafts(store, query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)
            return

        await query.answer()
        await _render_draft_detail(
            store,
            query.message,
            user_id=query.from_user.id,
            scope=scope,
            page=page,
            draft=draft,
            permissions=permissions,
            edit=True,
        )

    @router.callback_query(F.data.startswith("ddelask:"))
    async def cb_draft_delete_prompt(query: CallbackQuery) -> None:
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            await query.answer()
            return

        scope = kb._normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        draft_id = parts[3]
        lang = await h._user_lang(store, query.from_user.id)
        draft, permissions = await draft_svc.resolve_draft_by_id(store, draft_id, query.from_user.id, need="delete")
        if draft is None:
            await query.answer(tr(lang, "draft_missing"), show_alert=True)
            await _render_drafts(store, query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)
            return

        await query.answer()
        await _render_draft_delete_confirm(
            store,
            query.message,
            user_id=query.from_user.id,
            draft=draft,
            scope=scope,
            page=page,
            edit=True,
        )

    @router.callback_query(F.data.startswith("ddelyes:"))
    async def cb_draft_delete_confirm(query: CallbackQuery) -> None:
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            await query.answer()
            return

        scope = kb._normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        draft_id = parts[3]
        lang = await h._user_lang(store, query.from_user.id)
        ok = await store.delete_draft(draft_id, query.from_user.id)
        await query.answer(
            tr(lang, "draft_delete_ok", draft_id=kb._short_id(draft_id)) if ok else tr(lang, "draft_missing"),
            show_alert=not ok,
        )
        await _render_drafts(store, query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)

    @router.callback_query(F.data.startswith("ddelcmd:"))
    async def cb_draft_delete_command_confirm(query: CallbackQuery) -> None:
        draft_id = query.data.split(":", 1)[1]
        lang = await h._user_lang(store, query.from_user.id)
        ok = await store.delete_draft(draft_id, query.from_user.id)
        await query.answer(
            tr(lang, "draft_delete_ok", draft_id=kb._short_id(draft_id)) if ok else tr(lang, "draft_missing"),
            show_alert=not ok,
        )
        await query.message.edit_text(
            tr(lang, "draft_delete_ok", draft_id=kb._short_id(draft_id)) if ok else tr(lang, "draft_missing"),
            reply_markup=None,
        )

    @router.callback_query(F.data.startswith("dact:"))
    async def cb_draft_action(query: CallbackQuery, state: FSMContext) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        action = parts[1]
        draft_id = parts[2]
        lang = await h._user_lang(store, query.from_user.id)
        if action == "edit":
            draft, permissions = await draft_svc.resolve_draft_by_id(store, draft_id, query.from_user.id, need="edit")
            if draft is None:
                await query.answer(tr(lang, "draft_missing"), show_alert=True)
                return

            await query.answer()
            await _start_draft_edit(store, query.message, state, user_id=query.from_user.id, draft=draft)
            return

        if action == "publish":
            draft, permissions = await draft_svc.resolve_draft_by_id(store, draft_id, query.from_user.id, need="publish")
            if draft is None:
                await query.answer(tr(lang, "draft_missing"), show_alert=True)
                return

            await query.answer()
            await _start_draft_publish(store, query.message, state, user_id=query.from_user.id, draft=draft)
            return

        permissions = await store.get_draft_permissions(draft_id, query.from_user.id)
        allowed = False
        if permissions is not None:
            allowed = action == "delete" and permissions.can_delete
        await query.answer(
            tr(lang, "draft_action_unavailable") if allowed else tr(lang, "draft_missing"),
            show_alert=True,
        )

    @router.callback_query(F.data.startswith("ddpage:"))
    async def cb_draft_create_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.DraftStates.choosing_destination.state:
            await query.answer()
            return

        page = int(query.data.split(":")[1])
        await h.patch_draft_ctx(state, dest_page=page)
        await query.answer()
        await h._render_destinations(
            store,
            query.message,
            page=page,
            user_id=query.from_user.id,
            select_prefix="ddsel",
            page_prefix="ddpage",
        )

    @router.callback_query(F.data.startswith("ddsel:"))
    async def cb_draft_create_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.DraftStates.choosing_destination.state:
            await query.answer()
            return

        chat_id = int(query.data.split(":")[1])
        lang = await h._user_lang(store, query.from_user.id)
        await h.patch_draft_ctx(state, chat_id=chat_id)
        await query.answer()
        await _move_to_draft_collection(store, query.message, state, chat_id=chat_id, lang=lang)

    @router.callback_query(F.data.startswith("dcscope:"))
    async def cb_draft_create_scope(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.DraftStates.choosing_scope.state:
            await query.answer()
            return

        lang = await h._user_lang(store, query.from_user.id)
        parts = query.data.split(":", 2)
        if len(parts) < 2:
            await query.answer()
            return

        team_id: str | None = None
        if parts[1] == "team":
            if len(parts) != 3:
                await query.answer()
                return
            team_id = parts[2]
            writable_teams = await store.list_writable_teams(query.from_user.id)
            if team_id not in {team.id for team in writable_teams}:
                await query.answer(tr(lang, "draft_create_scope_invalid"), show_alert=True)
                return

        await query.answer()
        if not await h._save_draft_from_state(store, query.message, state, user_id=query.from_user.id, team_id=team_id):
            await query.message.answer(
                tr(lang, "draft_create_scope_prompt"),
                reply_markup=kb._draft_create_scope_kb(await store.list_writable_teams(query.from_user.id), lang),
            )
            return

    @router.message(states.DraftStates.choosing_scope)
    async def draft_choose_scope(message: Message) -> None:
        lang = await h._user_lang(store, message.from_user.id)
        writable_teams = await store.list_writable_teams(message.from_user.id)
        await message.answer(
            tr(lang, "draft_create_scope_prompt"),
            reply_markup=kb._draft_create_scope_kb(writable_teams, lang),
        )

    return router
