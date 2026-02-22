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
from core.timezone_resolver import timezone_from_coordinates
from core.utils import ParsedScheduleTime, parse_local_datetime

logger = logging.getLogger(__name__)


class TimezoneStates(StatesGroup):
    waiting_tz = State()


class DestinationsStates(StatesGroup):
    waiting_username = State()
    waiting_forward = State()


class ScheduleStates(StatesGroup):
    choosing_destination = State()
    entering_datetime = State()
    choosing_kind = State()
    entering_text = State()
    media_collect = State()
    choosing_caption_position = State()
    confirming = State()


_QUICK_TZ_CHOICES: dict[str, str] = {
    "Москва (UTC+3)": "Europe/Moscow",
    "Киев (UTC+2)": "Europe/Kyiv",
    "Берлин (UTC+1)": "Europe/Berlin",
    "Лондон (UTC+0)": "Europe/London",
    "Нью-Йорк (UTC-5)": "America/New_York",
    "Лос-Анджелес (UTC-8)": "America/Los_Angeles",
    "Дубай (UTC+4)": "Asia/Dubai",
    "Алматы (UTC+5)": "Asia/Almaty",
    "Дели (UTC+5:30)": "Asia/Kolkata",
    "Сингапур (UTC+8)": "Asia/Singapore",
    "Токио (UTC+9)": "Asia/Tokyo",
}


def _main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Запланировать"), KeyboardButton(text="Очередь")],
            [KeyboardButton(text="Мои каналы/чаты"), KeyboardButton(text="Часовой пояс")],
        ],
        resize_keyboard=True,
    )


def _timezone_setup_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отправить геопозицию", request_location=True)],
            [KeyboardButton(text="Москва (UTC+3)"), KeyboardButton(text="Киев (UTC+2)")],
            [KeyboardButton(text="Берлин (UTC+1)"), KeyboardButton(text="Лондон (UTC+0)")],
            [KeyboardButton(text="Нью-Йорк (UTC-5)"), KeyboardButton(text="Лос-Анджелес (UTC-8)")],
            [KeyboardButton(text="Дубай (UTC+4)"), KeyboardButton(text="Алматы (UTC+5)")],
            [KeyboardButton(text="Дели (UTC+5:30)"), KeyboardButton(text="Сингапур (UTC+8)")],
            [KeyboardButton(text="Токио (UTC+9)")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Или введите Europe/Moscow вручную",
    )


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


def _schedule_kind_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Текст", callback_data="skind:text"),
                InlineKeyboardButton(text="Медиа (фото/видео)", callback_data="skind:media"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="scancel")],
        ]
    )


def _media_collect_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Готово", callback_data="smedia:done"),
                InlineKeyboardButton(text="Очистить", callback_data="smedia:clear"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="scancel")],
        ]
    )


def _caption_pos_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подпись сверху", callback_data="scap:above"),
                InlineKeyboardButton(text="Подпись снизу", callback_data="scap:below"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="scancel")],
        ]
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="sconf:yes")],
            [InlineKeyboardButton(text="Отмена", callback_data="scancel")],
        ]
    )


def _queue_cancel_kb(posts: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append([InlineKeyboardButton(text=f"Отменить {item['label']}", callback_data=f"qcancel:{item['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_id(post_id: str) -> str:
    return post_id[:8]


def _format_local(epoch_utc: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(epoch_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%d.%m.%Y %H:%M")


def _format_rights_check_error(exc: Exception, *, subject: str) -> str:
    if isinstance(exc, TelegramForbiddenError):
        text = str(exc).lower()
        if "not a member" in text or "bot was kicked" in text:
            return (
                "Бот не состоит в этом канале/чате. Добавьте бота администратором с правом "
                "публикации и повторите привязку через /link или /link_forward."
            )
    return f"Не удалось проверить права {subject}: {exc}"


def _is_valid_tz_name(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
    except Exception:
        return False
    return True


def _resolve_timezone_input(tz_raw: str) -> str | None:
    mapped = _QUICK_TZ_CHOICES.get(tz_raw)
    if mapped:
        return mapped
    if _is_valid_tz_name(tz_raw):
        return tz_raw
    return None


async def _check_user_admin(bot: Bot, chat_id: int, user_id: int) -> tuple[bool, str]:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as exc:
        return False, _format_rights_check_error(exc, subject="пользователя")
    if member.status not in {"creator", "administrator"}:
        return False, "Нужны права администратора в этом чате/канале."
    return True, ""


async def _check_bot_admin_and_post(bot: Bot, chat_id: int) -> tuple[bool, str]:
    try:
        me = await bot.me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    except Exception as exc:
        return False, _format_rights_check_error(exc, subject="бота")

    if member.status != "administrator":
        return False, "Бот должен быть администратором в этом чате/канале."
    can_post = getattr(member, "can_post_messages", None)
    if can_post is False:
        return False, "В канале боту нужно право публиковать сообщения (can_post_messages)."
    return True, ""


def build_router(store: StateStore) -> Router:
    router = Router()

    async def _render_destinations(message: Message, page: int) -> None:
        page_size = 5
        offset = page * page_size
        items = await store.list_user_destinations(user_id=message.from_user.id, offset=offset, limit=page_size + 1)
        has_more = len(items) > page_size
        items = items[:page_size]
        if not items:
            await message.answer(
                "У вас пока нет привязанных каналов/чатов.\n\n"
                "Добавьте бота администратором в канал/чат, затем вернитесь сюда и откройте /schedule.\n"
                "Если канал приватный без @username — используйте /destinations и привязку через пересланное сообщение.",
                reply_markup=_main_menu_kb(),
            )
            return
        await message.answer("Выберите канал/чат:", reply_markup=_destinations_kb(items, page=page, has_more=has_more))

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        await message.answer(
            "Это бот для отложенных публикаций.\n"
            "1) Добавьте бота администратором в канал/чат (с правом постинга).\n"
            "2) Настройте часовой пояс (/timezone).\n"
            "3) Используйте /schedule для планирования.",
            reply_markup=_main_menu_kb(),
        )

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Ок, отменено.", reply_markup=_main_menu_kb())

    @router.message(F.text == "Запланировать")
    @router.message(Command("schedule"))
    async def cmd_schedule(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer("Сначала задайте часовой пояс: /timezone", reply_markup=_main_menu_kb())
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
        chat_id = int(query.data.split(":")[1])
        await state.update_data(chat_id=chat_id)
        await state.set_state(ScheduleStates.entering_datetime)
        await query.answer()
        await query.message.answer("Введите дату и время: `ДД.ММ.ГГГГ ЧЧ:ММ` (например `12.03.2026 12:15`).", parse_mode="Markdown")

    @router.message(ScheduleStates.entering_datetime)
    async def schedule_enter_datetime(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer("Сначала задайте часовой пояс: /timezone", reply_markup=_main_menu_kb())
            await state.clear()
            return

        try:
            parsed: ParsedScheduleTime = parse_local_datetime(message.text, tz_name=tz_name)
        except Exception:
            await message.answer("Неверный формат. Пример: `12.03.2026 12:15`", parse_mode="Markdown")
            return

        now_utc = int(time.time())
        if parsed.utc_epoch <= now_utc + 30:
            await message.answer("Время должно быть в будущем (минимум +30 секунд).")
            return

        await state.update_data(scheduled_at_utc=parsed.utc_epoch, scheduled_local=str(parsed.local_dt))
        await state.set_state(ScheduleStates.choosing_kind)
        await message.answer("Что вы хотите запланировать?", reply_markup=_schedule_kind_kb())

    @router.callback_query(F.data.startswith("skind:"))
    async def cb_kind(query: CallbackQuery, state: FSMContext) -> None:
        kind = query.data.split(":")[1]
        await query.answer()
        if kind == "text":
            await state.update_data(kind="text")
            await state.set_state(ScheduleStates.entering_text)
            await query.message.answer("Отправьте текст сообщения одним сообщением.")
        elif kind == "media":
            await state.update_data(kind="media", media_items=[], caption=None, caption_entities_json=None)
            await state.set_state(ScheduleStates.media_collect)
            await query.message.answer(
                "Отправьте фото/видео (можно несколько или альбом 2–10). "
                "Подпись отправьте текстом (можно после медиа).",
                reply_markup=_media_collect_kb(),
            )
        else:
            await query.message.answer("Неизвестный тип.")

    @router.message(ScheduleStates.entering_text)
    async def schedule_enter_text(message: Message, state: FSMContext) -> None:
        if not message.text:
            await message.answer("Нужен текст.")
            return
        entities_json = store.dump_entities(message.entities)
        await state.update_data(text=message.text, entities_json=entities_json)
        await state.set_state(ScheduleStates.confirming)

        data = await state.get_data()
        tz_name = await store.get_user_timezone(message.from_user.id) or "UTC"
        local_time = _format_local(int(data["scheduled_at_utc"]), tz_name)
        await message.answer(
            f"Подтвердите:\n"
            f"- Куда: `{data['chat_id']}`\n"
            f"- Когда: {local_time} ({tz_name})\n"
            f"- Тип: текст\n",
            parse_mode="Markdown",
            reply_markup=_confirm_kb(),
        )

    @router.message(ScheduleStates.media_collect)
    async def schedule_collect_media(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        media: list[dict[str, str]] = list(data.get("media_items", []))
        caption: str | None = data.get("caption")
        caption_entities_json: str | None = data.get("caption_entities_json")

        if message.text and not message.photo and not message.video:
            caption = message.text
            caption_entities_json = store.dump_entities(message.entities)
            await state.update_data(caption=caption, caption_entities_json=caption_entities_json)
            await message.answer(f"Подпись обновлена. Медиа: {len(media)}/10", reply_markup=_media_collect_kb())
            return

        if message.photo:
            if len(media) >= 10:
                await message.answer("Лимит 10 медиа. Нажмите «Готово» или сделайте второй пост.", reply_markup=_media_collect_kb())
                return
            file_id = message.photo[-1].file_id
            media.append({"type": "photo", "file_id": file_id})
            if message.caption:
                caption = message.caption
                caption_entities_json = store.dump_entities(message.caption_entities)
        elif message.video:
            if len(media) >= 10:
                await message.answer("Лимит 10 медиа. Нажмите «Готово» или сделайте второй пост.", reply_markup=_media_collect_kb())
                return
            file_id = message.video.file_id
            media.append({"type": "video", "file_id": file_id})
            if message.caption:
                caption = message.caption
                caption_entities_json = store.dump_entities(message.caption_entities)
        else:
            await message.answer("Пожалуйста, отправьте фото или видео (или подпись текстом).", reply_markup=_media_collect_kb())
            return

        await state.update_data(media_items=media, caption=caption, caption_entities_json=caption_entities_json)
        await message.answer(f"Добавлено: {len(media)}/10 медиа.", reply_markup=_media_collect_kb())

    @router.callback_query(F.data == "smedia:clear")
    async def cb_media_clear(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.update_data(media_items=[], caption=None, caption_entities_json=None)
        await query.message.answer("Очищено. Отправьте фото/видео заново.", reply_markup=_media_collect_kb())

    @router.callback_query(F.data == "smedia:done")
    async def cb_media_done(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        data = await state.get_data()
        media: list[dict[str, str]] = list(data.get("media_items", []))
        caption = (data.get("caption") or "").strip()
        if not media:
            await query.message.answer("Сначала отправьте хотя бы одно фото/видео.", reply_markup=_media_collect_kb())
            return

        if caption:
            await state.set_state(ScheduleStates.choosing_caption_position)
            await query.message.answer("Где должна быть подпись?", reply_markup=_caption_pos_kb())
        else:
            await state.update_data(caption_above=False)
            await _send_confirmation(query.message, state, store)

    @router.callback_query(F.data.startswith("scap:"))
    async def cb_caption_pos(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        pos = query.data.split(":")[1]
        await state.update_data(caption_above=(pos == "above"))
        await _send_confirmation(query.message, state, store)

    async def _send_confirmation(message: Message, state: FSMContext, store_: StateStore) -> None:
        data = await state.get_data()
        await state.set_state(ScheduleStates.confirming)
        tz_name = await store_.get_user_timezone(message.from_user.id) or "UTC"
        local_time = _format_local(int(data["scheduled_at_utc"]), tz_name)
        chat_id = int(data["chat_id"])
        kind = data.get("kind")
        if kind == "text":
            summary = "текст"
        else:
            media = list(data.get("media_items", []))
            summary = f"медиа x{len(media)}"
        title = await store_.get_destination_title(chat_id) or str(chat_id)
        await message.answer(
            "Подтвердите:\n"
            f"- Куда: {title}\n"
            f"- Когда: {local_time} ({tz_name})\n"
            f"- Тип: {summary}\n",
            reply_markup=_confirm_kb(),
        )

    @router.callback_query(F.data == "sconf:yes")
    async def cb_confirm_yes(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        data = await state.get_data()
        user_id = query.from_user.id
        chat_id = int(data["chat_id"])

        ok, err = await _check_user_admin(query.bot, chat_id=chat_id, user_id=user_id)
        if not ok:
            await query.message.answer(err, reply_markup=_main_menu_kb())
            await state.clear()
            return
        ok, err = await _check_bot_admin_and_post(query.bot, chat_id=chat_id)
        if not ok:
            await query.message.answer(err, reply_markup=_main_menu_kb())
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
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        local_time = _format_local(scheduled_at_utc, tz_name)
        await query.message.answer(
            f"Ок! Запланировано на {local_time} ({tz_name}). id={_short_id(post_id)}",
            reply_markup=_main_menu_kb(),
        )

    @router.callback_query(F.data == "scancel")
    async def cb_cancel(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        await query.message.answer("Ок, отменено.", reply_markup=_main_menu_kb())

    @router.message(F.text == "Очередь")
    @router.message(Command("queue"))
    async def cmd_queue(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id) or "UTC"
        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=10)
        if not posts:
            await message.answer("Очередь пуста.", reply_markup=_main_menu_kb())
            return
        lines: list[str] = []
        cancel_buttons: list[dict[str, str]] = []
        for p in posts:
            when = _format_local(p.scheduled_at_utc, tz_name)
            title = await store.get_destination_title(p.chat_id) or str(p.chat_id)
            label = _short_id(p.id)
            if p.kind == "text":
                k = "text"
            else:
                media = await store.get_post_media(p.id)
                k = f"media x{len(media)}"
            lines.append(f"{label} — {when} — {title} — {k}")
            cancel_buttons.append({"id": p.id, "label": label})
        await message.answer("Ближайшие посты:\n" + "\n".join(lines), reply_markup=_queue_cancel_kb(cancel_buttons))

    @router.callback_query(F.data.startswith("qcancel:"))
    async def cb_queue_cancel(query: CallbackQuery) -> None:
        post_id = query.data.split(":")[1]
        ok = await store.cancel_post(user_id=query.from_user.id, post_id=post_id)
        await query.answer("Отменено" if ok else "Не найдено/уже отправлено", show_alert=False)
        await query.message.answer("Готово.", reply_markup=_main_menu_kb())

    @router.message(F.text == "Часовой пояс")
    @router.message(Command("timezone"))
    async def cmd_timezone(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.set_state(TimezoneStates.waiting_tz)
        if message.chat.type != "private":
            await message.answer(
                "Автоопределение по геопозиции работает только в личном чате с ботом.\n"
                "В этом чате введите IANA TZ вручную (например `Europe/Moscow`).",
                parse_mode="Markdown",
                reply_markup=_main_menu_kb(),
            )
            return
        await message.answer(
            "Отправьте геопозицию кнопкой ниже, и я определю часовой пояс автоматически.\n"
            "На Desktop можно выбрать TZ кнопками ниже без ручного ввода.\n"
            "Если Telegram не отправит геопозицию, проверьте разрешение геолокации для Telegram на устройстве.\n"
            "Также можно ввести вручную IANA TZ (например `Europe/Moscow`, `Europe/Kyiv`, `UTC`).",
            parse_mode="Markdown",
            reply_markup=_timezone_setup_kb(),
        )

    @router.message(TimezoneStates.waiting_tz)
    async def set_timezone(message: Message, state: FSMContext) -> None:
        location = message.location
        if location is not None:
            tz_name = timezone_from_coordinates(latitude=location.latitude, longitude=location.longitude)
            if not tz_name:
                await message.answer(
                    "Не удалось определить часовой пояс по геопозиции. "
                    "Введите его вручную, например `Europe/Moscow`.",
                    parse_mode="Markdown",
                )
                return
            await store.set_user_timezone(message.from_user.id, tz_name)
            await state.clear()
            await message.answer(f"Ок, TZ сохранён автоматически: {tz_name}", reply_markup=_main_menu_kb())
            return

        tz_raw = (message.text or "").strip()
        if tz_raw == "Отправить геопозицию":
            await message.answer(
                "Геопозиция не была отправлена в чат. Разрешите доступ к геолокации для Telegram "
                "и нажмите кнопку снова, либо выберите TZ кнопкой ниже, либо введите TZ вручную (`Europe/Moscow`).",
                parse_mode="Markdown",
                reply_markup=_timezone_setup_kb(),
            )
            return
        if tz_raw:
            resolved_tz = _resolve_timezone_input(tz_raw)
            if not resolved_tz:
                await message.answer("Не похоже на IANA TZ. Пример: `Europe/Moscow`", parse_mode="Markdown")
                return
            await store.set_user_timezone(message.from_user.id, resolved_tz)
            await state.clear()
            await message.answer(f"Ок, TZ сохранён: {resolved_tz}", reply_markup=_main_menu_kb())
            return

        await message.answer(
            "Отправьте геопозицию кнопкой или введите IANA TZ вручную, например `Europe/Moscow`.",
            parse_mode="Markdown",
            reply_markup=_timezone_setup_kb(),
        )

    @router.message(F.text == "Мои каналы/чаты")
    @router.message(Command("destinations"))
    async def cmd_destinations(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        total = await store.count_user_destinations(message.from_user.id)
        await message.answer(
            f"Привязано: {total}\n\n"
            "Добавить:\n"
            "- пришлите @username канала/чата командой: /link @channelusername\n"
            "- или перешлите сообщение из канала/чата после команды /link_forward\n",
            reply_markup=_main_menu_kb(),
        )

    @router.message(Command("link"))
    async def cmd_link(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Использование: /link @channelusername")
            return
        username = parts[1].strip()
        if not username.startswith("@"):
            await message.answer("Нужен @username, например /link @mychannel")
            return

        try:
            chat = await message.bot.get_chat(username)
        except Exception as exc:
            await message.answer(f"Не удалось найти чат {username}: {exc}")
            return

        ok, err = await _check_user_admin(message.bot, chat_id=chat.id, user_id=message.from_user.id)
        if not ok:
            await message.answer(err)
            return
        ok, err = await _check_bot_admin_and_post(message.bot, chat_id=chat.id)
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
        await message.answer(f"Ок, привязано: {chat.title or username}", reply_markup=_main_menu_kb())

    @router.message(Command("link_forward"))
    async def cmd_link_forward(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.set_state(DestinationsStates.waiting_forward)
        await message.answer("Перешлите сообщение из канала/чата, который хотите привязать.")

    @router.message(DestinationsStates.waiting_forward)
    async def handle_link_forward(message: Message, state: FSMContext) -> None:
        # Support both legacy forward_from_chat and new forward_origin structures.
        forward_chat = getattr(message, "forward_from_chat", None)
        if forward_chat is None:
            origin = getattr(message, "forward_origin", None)
            forward_chat = getattr(origin, "chat", None) if origin else None

        if not forward_chat:
            await message.answer("Не вижу пересланный чат. Перешлите сообщение именно из канала/чата.")
            return

        ok, err = await _check_user_admin(message.bot, chat_id=forward_chat.id, user_id=message.from_user.id)
        if not ok:
            await message.answer(err)
            return
        ok, err = await _check_bot_admin_and_post(message.bot, chat_id=forward_chat.id)
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
        await message.answer(f"Ок, привязано: {forward_chat.title}", reply_markup=_main_menu_kb())

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
