from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.state import StateStore
from core.timezone_resolver import timezone_from_coordinates
from telegram.i18n import key_values, language_display_name, resolve_language_choice, tr
from telegram.handlers import states, keyboards as kb, helpers as h


_MENU_TIMEZONE_TEXTS = key_values("menu_timezone")
_MENU_LANGUAGE_TEXTS = key_values("menu_language")
_MENU_DESTINATIONS_TEXTS = key_values("menu_destinations")
_TZ_LOCATION_BUTTON_TEXTS = key_values("timezone_location_button")


def build_router(store: StateStore) -> Router:
    router = Router(name="settings")

    @router.message(F.text.in_(_MENU_DESTINATIONS_TEXTS))
    @router.message(Command("destinations"))
    async def cmd_destinations(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        total = await store.count_user_destinations(message.from_user.id)
        await message.answer(
            tr(lang, "destinations_info", total=total),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    @router.message(F.text.in_(_MENU_TIMEZONE_TEXTS))
    @router.message(Command("timezone"))
    async def cmd_timezone(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        await state.set_state(states.TimezoneStates.waiting_tz)
        if message.chat.type != "private":
            await message.answer(
                tr(lang, "timezone_private_only"),
                parse_mode="Markdown",
                reply_markup=await h._main_menu_for(store, message.from_user.id),
            )
            return
        await message.answer(
            tr(lang, "timezone_prompt"),
            parse_mode="Markdown",
            reply_markup=kb._timezone_setup_kb(lang),
        )

    @router.message(states.TimezoneStates.waiting_tz)
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

    @router.message(states.LanguageStates.waiting_lang)
    async def set_language(message: Message, state: FSMContext) -> None:
        chosen = resolve_language_choice((message.text or "").strip())
        current_lang = await h._user_lang(store, message.from_user.id)
        if not chosen:
            await message.answer(tr(current_lang, "language_invalid"), reply_markup=kb._language_kb())
            return
        await store.set_user_language(message.from_user.id, chosen)
        await state.clear()
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
        await store.link_user_destination(message.from_user.id, chat.id, linked_via="username")
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

    @router.message(states.DestinationsStates.waiting_forward)
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
        await store.link_user_destination(message.from_user.id, forward_chat.id, linked_via="forward")
        await state.clear()
        await message.answer(
            tr(lang, "link_ok", title=forward_chat.title),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    return router
