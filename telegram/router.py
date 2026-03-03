from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
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
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from core.state import Destination, StateStore
from core.time_picker import TimePicker, resolve_quick_option
from core.timezone_resolver import timezone_from_coordinates
from core.utils import ParsedScheduleTime, parse_local_datetime
from telegram.i18n import (
    DEFAULT_LANGUAGE,
    key_values,
    language_choice_rows,
    language_display_name,
    normalize_language,
    resolve_language_choice,
    resolve_timezone_choice,
    timezone_choice_rows,
    tr,
)

logger = logging.getLogger(__name__)


class TimezoneStates(StatesGroup):
    waiting_tz = State()


class LanguageStates(StatesGroup):
    waiting_lang = State()


class DestinationsStates(StatesGroup):
    waiting_username = State()
    waiting_forward = State()


class ScheduleStates(StatesGroup):
    choosing_destination = State()
    entering_datetime = State()
    collecting_post = State()
    confirming = State()


_MENU_SCHEDULE_TEXTS = key_values("menu_schedule")
_MENU_QUEUE_TEXTS = key_values("menu_queue")
_MENU_DESTINATIONS_TEXTS = key_values("menu_destinations")
_MENU_TIMEZONE_TEXTS = key_values("menu_timezone")
_MENU_LANGUAGE_TEXTS = key_values("menu_language")
_TZ_LOCATION_BUTTON_TEXTS = key_values("timezone_location_button")
_TIME_PICKER = TimePicker()


def _main_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=tr(lang, "menu_schedule")), KeyboardButton(text=tr(lang, "menu_queue"))],
            [KeyboardButton(text=tr(lang, "menu_destinations")), KeyboardButton(text=tr(lang, "menu_timezone"))],
            [KeyboardButton(text=tr(lang, "menu_language"))],
        ],
        resize_keyboard=True,
    )


def _timezone_setup_kb(lang: str) -> ReplyKeyboardMarkup:
    keyboard: list[list[KeyboardButton]] = [[KeyboardButton(text=tr(lang, "timezone_location_button"), request_location=True)]]
    for row in timezone_choice_rows(lang):
        keyboard.append([KeyboardButton(text=label) for label in row])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Europe/Moscow",
    )


def _language_kb() -> ReplyKeyboardMarkup:
    keyboard: list[list[KeyboardButton]] = []
    for row in language_choice_rows():
        keyboard.append([KeyboardButton(text=label) for label in row])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)


def _destinations_kb(destinations: list[Destination], page: int, has_more: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for d in destinations:
        title = d.title
        if d.username:
            title = f"{title} (@{d.username})"
        buttons.append([InlineKeyboardButton(text=title[:60], callback_data=f"sdsel:{d.chat_id}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sdpage:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sdpage:{page+1}"))
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _media_collect_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=tr(lang, "btn_done"), callback_data="smedia:done"),
                InlineKeyboardButton(text=tr(lang, "btn_clear"), callback_data="smedia:clear"),
            ],
            [InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")],
        ]
    )


def _confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "btn_confirm"), callback_data="sconf:yes")],
            [InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")],
        ]
    )


def _queue_cancel_kb(posts: list[dict[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_queue_cancel", label=item["label"]),
                    callback_data=f"qcancel:{item['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _schedule_datetime_kb(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "1h": tr(lang, "schedule_quick_1h"),
        "today_20": tr(lang, "schedule_quick_today_20"),
        "tomorrow_9": tr(lang, "schedule_quick_tomorrow_9"),
        "next_monday": tr(lang, "schedule_quick_next_monday"),
    }
    quick_markup = _TIME_PICKER.quick_buttons(labels)
    rows = [list(row) for row in quick_markup.inline_keyboard]
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_id(post_id: str) -> str:
    return post_id[:8]


def _format_local(epoch_utc: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(epoch_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%d.%m.%Y %H:%M")


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


async def _prompt_for_datetime(message: Message, *, lang: str, text: str) -> None:
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=_schedule_datetime_kb(lang),
    )


async def _move_to_post_collection(message: Message, state: FSMContext, *, parsed: ParsedScheduleTime, lang: str) -> None:
    await state.update_data(
        scheduled_at_utc=parsed.utc_epoch,
        scheduled_local=str(parsed.local_dt),
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
    await state.set_state(ScheduleStates.collecting_post)
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


def build_router(store: StateStore) -> Router:
    router = Router()

    async def _user_lang(user_id: int) -> str:
        saved = await store.get_user_language(user_id)
        return normalize_language(saved)

    async def _main_menu_for(user_id: int) -> ReplyKeyboardMarkup:
        return _main_menu_kb(await _user_lang(user_id))

    async def _render_destinations(message: Message, page: int) -> None:
        lang = await _user_lang(message.from_user.id)
        page_size = 5
        offset = page * page_size
        items = await store.list_user_destinations(user_id=message.from_user.id, offset=offset, limit=page_size + 1)
        has_more = len(items) > page_size
        items = items[:page_size]
        if not items:
            await message.answer(
                tr(lang, "no_destinations"),
                reply_markup=await _main_menu_for(message.from_user.id),
            )
            return
        await message.answer(tr(lang, "choose_destination"), reply_markup=_destinations_kb(items, page=page, has_more=has_more))

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.clear()
        await message.answer(
            tr(lang, "start_message"),
            reply_markup=_main_menu_kb(lang),
        )

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(message.from_user.id)
        await state.clear()
        await message.answer(tr(lang, "cancelled"), reply_markup=await _main_menu_for(message.from_user.id))

    @router.message(F.text.in_(_MENU_SCHEDULE_TEXTS))
    @router.message(Command("schedule"))
    async def cmd_schedule(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await state.clear()
        await state.set_state(ScheduleStates.choosing_destination)
        await state.update_data(dest_page=0)
        await _render_destinations(message, page=0)

    @router.callback_query(F.data.startswith("sdpage:"))
    async def cb_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        page = int(query.data.split(":")[1])
        await state.update_data(dest_page=page)
        await query.answer()
        await _render_destinations(query.message, page=page)

    @router.callback_query(F.data.startswith("sdsel:"))
    async def cb_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(query.from_user.id)
        chat_id = int(query.data.split(":")[1])
        await state.update_data(chat_id=chat_id)
        await state.set_state(ScheduleStates.entering_datetime)
        await query.answer()
        await _prompt_for_datetime(query.message, lang=lang, text=tr(lang, "enter_datetime"))

    @router.callback_query(F.data.startswith("tp:quick:"))
    async def cb_schedule_quick(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != ScheduleStates.entering_datetime.state:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        option = query.data.split(":", 2)[2]
        try:
            parsed = resolve_quick_option(option, tz_name=tz_name)
        except ValueError:
            await query.answer(tr(lang, "invalid_datetime_format"), show_alert=True)
            return

        await query.answer()
        await _move_to_post_collection(query.message, state, parsed=parsed, lang=lang)

    @router.message(ScheduleStates.entering_datetime)
    async def schedule_enter_datetime(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(message.from_user.id))
            await state.clear()
            return

        try:
            parsed: ParsedScheduleTime = parse_local_datetime(message.text, tz_name=tz_name)
        except Exception:
            await _prompt_for_datetime(message, lang=lang, text=tr(lang, "invalid_datetime_format"))
            return

        now_utc = int(time.time())
        if parsed.utc_epoch <= now_utc + 30:
            await message.answer(tr(lang, "datetime_future_required"), reply_markup=_schedule_datetime_kb(lang))
            return

        await _move_to_post_collection(message, state, parsed=parsed, lang=lang)

    @router.message(ScheduleStates.collecting_post)
    async def schedule_collect_post(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(message.from_user.id)
        data = await state.get_data()
        media: list[dict[str, str]] = list(data.get("media_items", []))
        draft_text: str | None = data.get("draft_text")
        draft_entities_json: str | None = data.get("draft_entities_json")
        caption_above = bool(data.get("caption_above", False))
        text_before_media = bool(data.get("text_before_media", False))
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
            await state.update_data(
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

        caption_above = _resolve_caption_above(
            current=caption_above,
            had_media_before=had_media_before,
            text_before_media=text_before_media,
            text_after_media=False,
            explicit_above=explicit_above,
        )

        await state.update_data(
            media_items=media,
            draft_text=draft_text,
            draft_entities_json=draft_entities_json,
            caption_above=caption_above,
            text_before_media=text_before_media,
        )
        await message.answer(tr(lang, "media_added", count=len(media)), reply_markup=_media_collect_kb(lang))

    @router.callback_query(F.data == "smedia:clear")
    async def cb_media_clear(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(query.from_user.id)
        await query.answer()
        await state.update_data(
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
        await state.set_state(ScheduleStates.collecting_post)
        await query.message.answer(tr(lang, "media_cleared"), reply_markup=_media_collect_kb(lang))

    @router.callback_query(F.data == "smedia:done")
    async def cb_media_done(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(query.from_user.id)
        await query.answer()
        data = await state.get_data()
        media: list[dict[str, str]] = list(data.get("media_items", []))
        draft_text = data.get("draft_text")
        draft_text_valid = bool(str(draft_text).strip()) if draft_text is not None else False

        if media:
            await state.update_data(
                kind="media",
                caption=draft_text if draft_text_valid else None,
                caption_entities_json=data.get("draft_entities_json") if draft_text_valid else None,
                text=None,
                entities_json=None,
            )
            await _send_confirmation(query.message, state, store)
            return

        if draft_text_valid:
            await state.update_data(
                kind="text",
                text=draft_text,
                entities_json=data.get("draft_entities_json"),
                caption=None,
                caption_entities_json=None,
                caption_above=False,
            )
            await _send_confirmation(query.message, state, store)
            return

        await query.message.answer(tr(lang, "post_need_content"), reply_markup=_media_collect_kb(lang))

    async def _send_confirmation(message: Message, state: FSMContext, store_: StateStore) -> None:
        lang = await _user_lang(message.from_user.id)
        data = await state.get_data()
        await state.set_state(ScheduleStates.confirming)
        tz_name = await store_.get_user_timezone(message.from_user.id) or "UTC"
        local_time = _format_local(int(data["scheduled_at_utc"]), tz_name)
        chat_id = int(data["chat_id"])
        kind = data.get("kind")
        if kind == "text":
            summary = tr(lang, "kind_text")
        else:
            media = list(data.get("media_items", []))
            summary = tr(lang, "kind_media", count=len(media))
        title = await store_.get_destination_title(chat_id) or str(chat_id)
        await message.answer(
            tr(lang, "confirm_template", where=title, local_time=local_time, tz_name=tz_name, kind=summary),
            reply_markup=_confirm_kb(lang),
        )

    @router.callback_query(F.data == "sconf:yes")
    async def cb_confirm_yes(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(query.from_user.id)
        await query.answer()
        data = await state.get_data()
        user_id = query.from_user.id
        chat_id = int(data["chat_id"])

        ok, err = await _check_user_admin(query.bot, chat_id=chat_id, user_id=user_id, lang=lang)
        if not ok:
            await query.message.answer(err, reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return
        ok, err = await _check_bot_admin_and_post(query.bot, chat_id=chat_id, lang=lang)
        if not ok:
            await query.message.answer(err, reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        scheduled_at_utc = int(data["scheduled_at_utc"])
        kind = data.get("kind")
        if kind == "text":
            post_id = await store.create_scheduled_text_post(
                user_id=user_id,
                chat_id=chat_id,
                scheduled_at_utc=scheduled_at_utc,
                text=str(data.get("text") or ""),
                entities_json=data.get("entities_json"),
            )
        else:
            media_items: list[dict[str, str]] = list(data.get("media_items", []))
            post_id = await store.create_scheduled_media_post(
                user_id=user_id,
                chat_id=chat_id,
                scheduled_at_utc=scheduled_at_utc,
                caption=data.get("caption"),
                caption_entities_json=data.get("caption_entities_json"),
                caption_above=bool(data.get("caption_above", False)),
                media_items=media_items,
            )

        await state.clear()
        await state.set_state(ScheduleStates.entering_datetime)
        await state.update_data(chat_id=chat_id)
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        local_time = _format_local(scheduled_at_utc, tz_name)
        title = await store.get_destination_title(chat_id) or str(chat_id)
        await query.message.answer(
            tr(lang, "scheduled_ok", local_time=local_time, tz_name=tz_name, post_id=_short_id(post_id)),
        )
        await query.message.answer(
            tr(lang, "schedule_next_prompt", where=title),
            reply_markup=_schedule_datetime_kb(lang),
        )

    @router.callback_query(F.data == "scancel")
    async def cb_cancel(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(query.from_user.id)
        await query.answer()
        await state.clear()
        await query.message.answer(tr(lang, "cancelled"), reply_markup=await _main_menu_for(query.from_user.id))

    @router.message(F.text.in_(_MENU_QUEUE_TEXTS))
    @router.message(Command("queue"))
    async def cmd_queue(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id) or "UTC"
        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=10)
        if not posts:
            await message.answer(tr(lang, "queue_empty"), reply_markup=await _main_menu_for(message.from_user.id))
            return
        lines: list[str] = []
        cancel_buttons: list[dict[str, str]] = []
        for p in posts:
            when = _format_local(p.scheduled_at_utc, tz_name)
            title = await store.get_destination_title(p.chat_id) or str(p.chat_id)
            label = _short_id(p.id)
            if p.kind == "text":
                k = tr(lang, "kind_text")
            else:
                media = await store.get_post_media(p.id)
                k = tr(lang, "kind_media", count=len(media))
            lines.append(f"{label} — {when} — {title} — {k}")
            cancel_buttons.append({"id": p.id, "label": label})
        await message.answer(
            tr(lang, "queue_header", lines="\n".join(lines)),
            reply_markup=_queue_cancel_kb(cancel_buttons, lang),
        )

    @router.callback_query(F.data.startswith("qcancel:"))
    async def cb_queue_cancel(query: CallbackQuery) -> None:
        lang = await _user_lang(query.from_user.id)
        post_id = query.data.split(":")[1]
        ok = await store.cancel_post(user_id=query.from_user.id, post_id=post_id)
        await query.answer(tr(lang, "queue_cancel_ok") if ok else tr(lang, "queue_cancel_missing"), show_alert=False)
        await query.message.answer(tr(lang, "done"), reply_markup=await _main_menu_for(query.from_user.id))

    @router.message(F.text.in_(_MENU_TIMEZONE_TEXTS))
    @router.message(Command("timezone"))
    async def cmd_timezone(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.set_state(TimezoneStates.waiting_tz)
        if message.chat.type != "private":
            await message.answer(
                tr(lang, "timezone_private_only"),
                parse_mode="Markdown",
                reply_markup=await _main_menu_for(message.from_user.id),
            )
            return
        await message.answer(
            tr(lang, "timezone_prompt"),
            parse_mode="Markdown",
            reply_markup=_timezone_setup_kb(lang),
        )

    @router.message(TimezoneStates.waiting_tz)
    async def set_timezone(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(message.from_user.id)
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
                reply_markup=await _main_menu_for(message.from_user.id),
            )
            return

        tz_raw = (message.text or "").strip()
        if tz_raw in _TZ_LOCATION_BUTTON_TEXTS:
            await message.answer(
                tr(lang, "timezone_location_not_sent"),
                parse_mode="Markdown",
                reply_markup=_timezone_setup_kb(lang),
            )
            return
        if tz_raw:
            resolved_tz = _resolve_timezone_input(tz_raw)
            if not resolved_tz:
                await message.answer(tr(lang, "timezone_invalid"), parse_mode="Markdown")
                return
            await store.set_user_timezone(message.from_user.id, resolved_tz)
            await state.clear()
            await message.answer(
                tr(lang, "timezone_saved", tz_name=resolved_tz),
                reply_markup=await _main_menu_for(message.from_user.id),
            )
            return

        await message.answer(
            tr(lang, "timezone_prompt_short"),
            parse_mode="Markdown",
            reply_markup=_timezone_setup_kb(lang),
        )

    @router.message(F.text.in_(_MENU_LANGUAGE_TEXTS))
    @router.message(Command("language"))
    async def cmd_language(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.set_state(LanguageStates.waiting_lang)
        await message.answer(tr(lang, "language_prompt"), reply_markup=_language_kb())

    @router.message(LanguageStates.waiting_lang)
    async def set_language(message: Message, state: FSMContext) -> None:
        chosen = resolve_language_choice((message.text or "").strip())
        current_lang = await _user_lang(message.from_user.id)
        if not chosen:
            await message.answer(tr(current_lang, "language_invalid"), reply_markup=_language_kb())
            return
        await store.set_user_language(message.from_user.id, chosen)
        await state.clear()
        await message.answer(
            tr(chosen, "language_saved", language_name=language_display_name(chosen)),
            reply_markup=_main_menu_kb(chosen),
        )

    @router.message(F.text.in_(_MENU_DESTINATIONS_TEXTS))
    @router.message(Command("destinations"))
    async def cmd_destinations(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        total = await store.count_user_destinations(message.from_user.id)
        await message.answer(
            tr(lang, "destinations_info", total=total),
            reply_markup=await _main_menu_for(message.from_user.id),
        )

    @router.message(Command("link"))
    async def cmd_link(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
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

        ok, err = await _check_user_admin(message.bot, chat_id=chat.id, user_id=message.from_user.id, lang=lang)
        if not ok:
            await message.answer(err)
            return
        ok, err = await _check_bot_admin_and_post(message.bot, chat_id=chat.id, lang=lang)
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
            reply_markup=await _main_menu_for(message.from_user.id),
        )

    @router.message(Command("link_forward"))
    async def cmd_link_forward(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.set_state(DestinationsStates.waiting_forward)
        await message.answer(tr(lang, "link_forward_prompt"))

    @router.message(DestinationsStates.waiting_forward)
    async def handle_link_forward(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(message.from_user.id)
        # Support both legacy forward_from_chat and new forward_origin structures.
        forward_chat = getattr(message, "forward_from_chat", None)
        if forward_chat is None:
            origin = getattr(message, "forward_origin", None)
            forward_chat = getattr(origin, "chat", None) if origin else None

        if not forward_chat:
            await message.answer(tr(lang, "link_forward_not_seen"))
            return

        ok, err = await _check_user_admin(message.bot, chat_id=forward_chat.id, user_id=message.from_user.id, lang=lang)
        if not ok:
            await message.answer(err)
            return
        ok, err = await _check_bot_admin_and_post(message.bot, chat_id=forward_chat.id, lang=lang)
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
            reply_markup=await _main_menu_for(message.from_user.id),
        )

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
            await store.link_user_destination(from_user.id, chat.id, linked_via="my_chat_member")

    return router
