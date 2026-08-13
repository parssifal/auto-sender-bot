from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.limits import ResourceLimitError
from core.state import StateStore
from core.timezone_resolver import timezone_from_coordinates
from telegram.i18n import key_values, language_display_name, resolve_language_choice, tr
from telegram.handlers import states, keyboards as kb, helpers as h
from telegram.menu_button import set_user_menu_button


_MENU_TIMEZONE_TEXTS = key_values("menu_timezone")
_MENU_LANGUAGE_TEXTS = key_values("menu_language")
_MENU_DESTINATIONS_TEXTS = key_values("menu_destinations")
_TZ_LOCATION_BUTTON_TEXTS = key_values("timezone_location_button")


def build_router(store: StateStore, *, webapp_url: str | None = None) -> Router:
    router = Router(name="settings")

    async def _render_destinations(message: Message, *, user_id: int, page: int = 0, edit: bool = False) -> None:
        lang = await h._user_lang(store, user_id)
        page_size = 8
        destinations, page = await h._page_back_to_content(
            lambda offset: store.list_user_destinations(user_id, offset, page_size + 1),
            page,
            page_size,
        )
        total = await store.count_user_destinations(user_id)
        has_more = len(destinations) > page_size
        destinations = destinations[:page_size]
        if destinations:
            lines = []
            for destination in destinations:
                username = f" (@{destination.username})" if destination.username else ""
                lines.append(
                    tr(
                        lang,
                        "destinations_list_item",
                        title=destination.title,
                        username=username,
                        chat_id=destination.chat_id,
                        status=destination.bot_status,
                    )
                )
            text = tr(lang, "destinations_list", total=total, lines="\n".join(lines))
            reply_markup = kb._destinations_manage_kb(destinations, page, has_more, lang)
        else:
            text = tr(lang, "destinations_empty")
            reply_markup = None
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup or await h._main_menu_for(store, user_id))

    @router.message(F.text.in_(_MENU_DESTINATIONS_TEXTS))
    @router.message(Command("destinations"))
    async def cmd_destinations(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        await _render_destinations(message, user_id=message.from_user.id)

    @router.callback_query(F.data.startswith("udpage:"))
    async def cb_destinations_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_destinations(query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("udunlink:"))
    async def cb_destination_unlink(query: CallbackQuery) -> None:
        _, page_raw, chat_id_raw = query.data.split(":", 2)
        lang = await h._user_lang(store, query.from_user.id)
        removed = await store.unlink_user_destination(query.from_user.id, int(chat_id_raw))
        await query.answer(tr(lang, "destination_unlink_ok") if removed else tr(lang, "destination_unlink_missing"))
        await _render_destinations(query.message, user_id=query.from_user.id, page=int(page_raw), edit=True)

    @router.message(F.text.in_(_MENU_TIMEZONE_TEXTS))
    @router.message(Command("timezone"))
    async def cmd_timezone(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        if message.chat.type != "private":
            await message.answer(
                tr(lang, "timezone_private_only"),
                parse_mode="Markdown",
                reply_markup=await h._main_menu_for(store, message.from_user.id),
            )
            return
        await state.set_state(states.TimezoneStates.waiting_tz)
        await message.answer(
            tr(lang, "timezone_prompt"),
            parse_mode="Markdown",
            reply_markup=kb._timezone_setup_kb(lang),
        )

    @router.message(states.TimezoneStates.waiting_tz, h._not_command_or_menu)
    async def set_timezone(message: Message, state: FSMContext) -> None:
        lang = await h._user_lang(store, message.from_user.id)
        location = message.location
        if location is not None:
            tz_name = timezone_from_coordinates(latitude=location.latitude, longitude=location.longitude)
            if not tz_name:
                await message.answer(
                    tr(lang, "timezone_auto_failed"),
                    parse_mode="Markdown",
                )
                return
            await store.set_user_timezone(message.from_user.id, tz_name)
            await state.clear()
            await message.answer(
                tr(lang, "timezone_auto_saved", tz_name=tz_name),
                reply_markup=await h._main_menu_for(store, message.from_user.id),
            )
            return

        tz_raw = (message.text or "").strip()
        if tz_raw in _TZ_LOCATION_BUTTON_TEXTS:
            await message.answer(
                tr(lang, "timezone_location_not_sent"),
                parse_mode="Markdown",
                reply_markup=kb._timezone_setup_kb(lang),
            )
            return
        if tz_raw:
            resolved_tz = h._resolve_timezone_input(tz_raw)
            if not resolved_tz:
                await message.answer(tr(lang, "timezone_invalid"), parse_mode="Markdown")
                return
            await store.set_user_timezone(message.from_user.id, resolved_tz)
            await state.clear()
            await message.answer(
                tr(lang, "timezone_saved", tz_name=resolved_tz),
                reply_markup=await h._main_menu_for(store, message.from_user.id),
            )
            return

        await message.answer(
            tr(lang, "timezone_prompt_short"),
            parse_mode="Markdown",
            reply_markup=kb._timezone_setup_kb(lang),
        )

    @router.message(F.text.in_(_MENU_LANGUAGE_TEXTS))
    @router.message(Command("language"))
    async def cmd_language(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        await state.set_state(states.LanguageStates.waiting_lang)
        await message.answer(tr(lang, "language_prompt"), reply_markup=kb._language_kb())

    @router.message(states.LanguageStates.waiting_lang, h._not_command_or_menu)
    async def set_language(message: Message, state: FSMContext, bot: Bot) -> None:
        chosen = resolve_language_choice((message.text or "").strip())
        current_lang = await h._user_lang(store, message.from_user.id)
        if not chosen:
            await message.answer(tr(current_lang, "language_invalid"), reply_markup=kb._language_kb())
            return
        await store.set_user_language(message.from_user.id, chosen)
        await state.clear()
        if webapp_url:
            # Re-localize the blue "Menu" button to the newly chosen language.
            await set_user_menu_button(
                bot, chat_id=message.chat.id, webapp_url=webapp_url, language=chosen
            )
        await message.answer(
            tr(chosen, "language_saved", language_name=language_display_name(chosen)),
            reply_markup=kb._main_menu_kb(chosen),
        )

    @router.message(Command("link"))
    async def cmd_link(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer(tr(lang, "link_usage"))
            return
        username = parts[1].strip()
        if not username.startswith("@"):
            await message.answer(tr(lang, "link_need_username"))
            return

        try:
            chat = await message.bot.get_chat(username)
        except Exception as exc:
            await message.answer(tr(lang, "link_not_found", username=username, error=exc))
            return

        ok, err = await h._check_user_admin(message.bot, chat_id=chat.id, user_id=message.from_user.id, lang=lang)
        if not ok:
            await message.answer(err)
            return
        ok, err = await h._check_bot_admin_and_post(message.bot, chat_id=chat.id, lang=lang)
        if not ok:
            await message.answer(err)
            return

        await store.upsert_destination(
            chat_id=chat.id,
            type_=chat.type,
            title=chat.title or (chat.username or str(chat.id)),
            username=chat.username,
            bot_status="administrator",
            bot_can_post=True,
        )
        try:
            await store.link_user_destination(message.from_user.id, chat.id, linked_via="username")
        except ResourceLimitError as exc:
            await message.answer(h.limit_message(lang, exc))
            return
        await message.answer(
            tr(lang, "link_ok", title=chat.title or username),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    @router.message(Command("link_forward"))
    async def cmd_link_forward(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        await state.set_state(states.DestinationsStates.waiting_forward)
        await message.answer(tr(lang, "link_forward_prompt"))

    @router.message(states.DestinationsStates.waiting_forward, h._not_command_or_menu)
    async def handle_link_forward(message: Message, state: FSMContext) -> None:
        lang = await h._user_lang(store, message.from_user.id)
        # Support both legacy forward_from_chat and new forward_origin structures.
        forward_chat = getattr(message, "forward_from_chat", None)
        if forward_chat is None:
            origin = getattr(message, "forward_origin", None)
            forward_chat = getattr(origin, "chat", None) if origin else None

        if not forward_chat:
            await message.answer(tr(lang, "link_forward_not_seen"))
            return

        ok, err = await h._check_user_admin(message.bot, chat_id=forward_chat.id, user_id=message.from_user.id, lang=lang)
        if not ok:
            await message.answer(err)
            return
        ok, err = await h._check_bot_admin_and_post(message.bot, chat_id=forward_chat.id, lang=lang)
        if not ok:
            await message.answer(err)
            return

        await store.upsert_destination(
            chat_id=forward_chat.id,
            type_=forward_chat.type,
            title=forward_chat.title or (forward_chat.username or str(forward_chat.id)),
            username=forward_chat.username,
            bot_status="administrator",
            bot_can_post=True,
        )
        try:
            await store.link_user_destination(message.from_user.id, forward_chat.id, linked_via="forward")
        except ResourceLimitError as exc:
            await state.clear()
            await message.answer(h.limit_message(lang, exc))
            return
        await state.clear()
        await message.answer(
            tr(lang, "link_ok", title=forward_chat.title),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    return router
