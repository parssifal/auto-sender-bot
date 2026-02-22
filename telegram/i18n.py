from __future__ import annotations

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "ru")

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "menu_schedule": "Schedule",
        "menu_queue": "Queue",
        "menu_destinations": "My channels/chats",
        "menu_timezone": "Timezone",
        "menu_language": "Language",
        "start_message": (
            "This is a scheduled posting bot.\n"
            "1) Add the bot as admin to a channel/chat (with posting rights).\n"
            "2) Set your timezone (/timezone).\n"
            "3) Use /schedule to create posts."
        ),
        "cancelled": "Okay, cancelled.",
        "no_destinations": (
            "You don't have linked channels/chats yet.\n\n"
            "Add the bot as admin to a channel/chat, then return here and run /schedule.\n"
            "If the channel is private without @username, use /destinations and link it by forwarding a message."
        ),
        "choose_destination": "Choose a channel/chat:",
        "timezone_required": "Set timezone first: /timezone",
        "enter_datetime": "Enter date and time: `DD.MM.YYYY HH:MM` (for example `12.03.2026 12:15`).",
        "invalid_datetime_format": "Invalid format. Example: `12.03.2026 12:15`",
        "datetime_future_required": "Time must be in the future (minimum +30 seconds).",
        "schedule_kind_prompt": "What do you want to schedule?",
        "schedule_text_prompt": "Send message text in one message.",
        "schedule_media_prompt": "Send photo/video (single media or album 2-10). Send caption as text (can be after media).",
        "schedule_unknown_type": "Unknown type.",
        "text_required": "Text is required.",
        "confirm_template": "Confirm:\n- Where: {where}\n- When: {local_time} ({tz_name})\n- Type: {kind}\n",
        "kind_text": "text",
        "kind_media": "media x{count}",
        "caption_updated": "Caption updated. Media: {count}/10",
        "media_limit": "Limit is 10 media. Tap \"Done\" or create another post.",
        "media_send_prompt": "Please send a photo or video (or caption as text).",
        "media_added": "Added: {count}/10 media.",
        "media_cleared": "Cleared. Send photo/video again.",
        "media_need_at_least_one": "Send at least one photo/video first.",
        "caption_position_prompt": "Where should the caption be?",
        "scheduled_ok": "Done! Scheduled for {local_time} ({tz_name}). id={post_id}",
        "queue_empty": "Queue is empty.",
        "queue_header": "Upcoming posts:\n{lines}",
        "queue_cancel_ok": "Cancelled",
        "queue_cancel_missing": "Not found / already sent",
        "done": "Done.",
        "timezone_private_only": (
            "Auto-detect by location works only in private chat with the bot.\n"
            "In this chat, enter IANA TZ manually (for example `Europe/Moscow`)."
        ),
        "timezone_prompt": (
            "Send your location with the button below and I will detect timezone automatically.\n"
            "On Desktop, choose TZ from buttons below without manual input.\n"
            "If Telegram does not send location, check location permissions for Telegram on your device.\n"
            "You can also enter IANA TZ manually (for example `Europe/Moscow`, `Europe/London`, `UTC`)."
        ),
        "timezone_auto_failed": "Could not detect timezone from location. Enter it manually, for example `Europe/Moscow`.",
        "timezone_auto_saved": "Okay, timezone saved automatically: {tz_name}",
        "timezone_location_button": "Share location",
        "timezone_location_not_sent": (
            "Location was not sent to chat. Allow location access for Telegram and tap the button again, "
            "or choose a TZ below, or enter TZ manually (`Europe/Moscow`)."
        ),
        "timezone_invalid": "This does not look like an IANA TZ. Example: `Europe/Moscow`",
        "timezone_saved": "Okay, timezone saved: {tz_name}",
        "timezone_prompt_short": "Send location with the button or enter IANA TZ manually, for example `Europe/Moscow`.",
        "destinations_info": (
            "Linked: {total}\n\n"
            "Add:\n"
            "- send @username with command: /link @channelusername\n"
            "- or forward a message from channel/chat after /link_forward"
        ),
        "link_usage": "Usage: /link @channelusername",
        "link_need_username": "Need @username, for example /link @mychannel",
        "link_not_found": "Could not find chat {username}: {error}",
        "link_ok": "Okay, linked: {title}",
        "link_forward_prompt": "Forward a message from the channel/chat you want to link.",
        "link_forward_not_seen": "I cannot see forwarded chat. Forward a message exactly from the target channel/chat.",
        "rights_not_member": (
            "Bot is not in this channel/chat. Add the bot as admin with posting rights "
            "and repeat linking via /link or /link_forward."
        ),
        "rights_check_failed": "Could not verify {subject} rights: {error}",
        "rights_subject_user": "user",
        "rights_subject_bot": "bot",
        "rights_user_admin_required": "You need admin rights in this chat/channel.",
        "rights_bot_admin_required": "Bot must be admin in this chat/channel.",
        "rights_bot_can_post_required": "In a channel, bot needs posting rights (can_post_messages).",
        "language_prompt": "Choose interface language:",
        "language_saved": "Language saved: {language_name}",
        "language_invalid": "Unknown language. Choose one of the buttons.",
        "language_option_en": "English",
        "language_option_ru": "Russian",
        "btn_text": "Text",
        "btn_media": "Media (photo/video)",
        "btn_cancel": "Cancel",
        "btn_done": "Done",
        "btn_clear": "Clear",
        "btn_caption_above": "Caption above",
        "btn_caption_below": "Caption below",
        "btn_confirm": "Confirm",
        "btn_queue_cancel": "Cancel {label}",
    },
    "ru": {
        "menu_schedule": "Запланировать",
        "menu_queue": "Очередь",
        "menu_destinations": "Мои каналы/чаты",
        "menu_timezone": "Часовой пояс",
        "menu_language": "Язык",
        "start_message": (
            "Это бот для отложенных публикаций.\n"
            "1) Добавьте бота администратором в канал/чат (с правом постинга).\n"
            "2) Настройте часовой пояс (/timezone).\n"
            "3) Используйте /schedule для планирования."
        ),
        "cancelled": "Ок, отменено.",
        "no_destinations": (
            "У вас пока нет привязанных каналов/чатов.\n\n"
            "Добавьте бота администратором в канал/чат, затем вернитесь сюда и откройте /schedule.\n"
            "Если канал приватный без @username — используйте /destinations и привязку через пересланное сообщение."
        ),
        "choose_destination": "Выберите канал/чат:",
        "timezone_required": "Сначала задайте часовой пояс: /timezone",
        "enter_datetime": "Введите дату и время: `ДД.ММ.ГГГГ ЧЧ:ММ` (например `12.03.2026 12:15`).",
        "invalid_datetime_format": "Неверный формат. Пример: `12.03.2026 12:15`",
        "datetime_future_required": "Время должно быть в будущем (минимум +30 секунд).",
        "schedule_kind_prompt": "Что вы хотите запланировать?",
        "schedule_text_prompt": "Отправьте текст сообщения одним сообщением.",
        "schedule_media_prompt": "Отправьте фото/видео (можно несколько или альбом 2–10). Подпись отправьте текстом (можно после медиа).",
        "schedule_unknown_type": "Неизвестный тип.",
        "text_required": "Нужен текст.",
        "confirm_template": "Подтвердите:\n- Куда: {where}\n- Когда: {local_time} ({tz_name})\n- Тип: {kind}\n",
        "kind_text": "текст",
        "kind_media": "медиа x{count}",
        "caption_updated": "Подпись обновлена. Медиа: {count}/10",
        "media_limit": "Лимит 10 медиа. Нажмите «Готово» или сделайте второй пост.",
        "media_send_prompt": "Пожалуйста, отправьте фото или видео (или подпись текстом).",
        "media_added": "Добавлено: {count}/10 медиа.",
        "media_cleared": "Очищено. Отправьте фото/видео заново.",
        "media_need_at_least_one": "Сначала отправьте хотя бы одно фото/видео.",
        "caption_position_prompt": "Где должна быть подпись?",
        "scheduled_ok": "Ок! Запланировано на {local_time} ({tz_name}). id={post_id}",
        "queue_empty": "Очередь пуста.",
        "queue_header": "Ближайшие посты:\n{lines}",
        "queue_cancel_ok": "Отменено",
        "queue_cancel_missing": "Не найдено/уже отправлено",
        "done": "Готово.",
        "timezone_private_only": (
            "Автоопределение по геопозиции работает только в личном чате с ботом.\n"
            "В этом чате введите IANA TZ вручную (например `Europe/Moscow`)."
        ),
        "timezone_prompt": (
            "Отправьте геопозицию кнопкой ниже, и я определю часовой пояс автоматически.\n"
            "На Desktop можно выбрать TZ кнопками ниже без ручного ввода.\n"
            "Если Telegram не отправит геопозицию, проверьте разрешение геолокации для Telegram на устройстве.\n"
            "Также можно ввести вручную IANA TZ (например `Europe/Moscow`, `Europe/London`, `UTC`)."
        ),
        "timezone_auto_failed": "Не удалось определить часовой пояс по геопозиции. Введите его вручную, например `Europe/Moscow`.",
        "timezone_auto_saved": "Ок, TZ сохранён автоматически: {tz_name}",
        "timezone_location_button": "Отправить геопозицию",
        "timezone_location_not_sent": (
            "Геопозиция не была отправлена в чат. Разрешите доступ к геолокации для Telegram "
            "и нажмите кнопку снова, либо выберите TZ кнопкой ниже, либо введите TZ вручную (`Europe/Moscow`)."
        ),
        "timezone_invalid": "Не похоже на IANA TZ. Пример: `Europe/Moscow`",
        "timezone_saved": "Ок, TZ сохранён: {tz_name}",
        "timezone_prompt_short": "Отправьте геопозицию кнопкой или введите IANA TZ вручную, например `Europe/Moscow`.",
        "destinations_info": (
            "Привязано: {total}\n\n"
            "Добавить:\n"
            "- пришлите @username канала/чата командой: /link @channelusername\n"
            "- или перешлите сообщение из канала/чата после команды /link_forward"
        ),
        "link_usage": "Использование: /link @channelusername",
        "link_need_username": "Нужен @username, например /link @mychannel",
        "link_not_found": "Не удалось найти чат {username}: {error}",
        "link_ok": "Ок, привязано: {title}",
        "link_forward_prompt": "Перешлите сообщение из канала/чата, который хотите привязать.",
        "link_forward_not_seen": "Не вижу пересланный чат. Перешлите сообщение именно из канала/чата.",
        "rights_not_member": (
            "Бот не состоит в этом канале/чате. Добавьте бота администратором с правом "
            "публикации и повторите привязку через /link или /link_forward."
        ),
        "rights_check_failed": "Не удалось проверить права {subject}: {error}",
        "rights_subject_user": "пользователя",
        "rights_subject_bot": "бота",
        "rights_user_admin_required": "Нужны права администратора в этом чате/канале.",
        "rights_bot_admin_required": "Бот должен быть администратором в этом чате/канале.",
        "rights_bot_can_post_required": "В канале боту нужно право публиковать сообщения (can_post_messages).",
        "language_prompt": "Выберите язык интерфейса:",
        "language_saved": "Язык сохранён: {language_name}",
        "language_invalid": "Неизвестный язык. Выберите вариант кнопкой.",
        "language_option_en": "English",
        "language_option_ru": "Русский",
        "btn_text": "Текст",
        "btn_media": "Медиа (фото/видео)",
        "btn_cancel": "Отмена",
        "btn_done": "Готово",
        "btn_clear": "Очистить",
        "btn_caption_above": "Подпись сверху",
        "btn_caption_below": "Подпись снизу",
        "btn_confirm": "Подтвердить",
        "btn_queue_cancel": "Отменить {label}",
    },
}

_TZ_CHOICE_IDS = (
    ("moscow",),
    ("berlin", "london"),
    ("new_york", "los_angeles"),
    ("dubai", "almaty"),
    ("delhi", "singapore"),
    ("tokyo",),
)

_TZ_BY_ID: dict[str, str] = {
    "moscow": "Europe/Moscow",
    "berlin": "Europe/Berlin",
    "london": "Europe/London",
    "new_york": "America/New_York",
    "los_angeles": "America/Los_Angeles",
    "dubai": "Asia/Dubai",
    "almaty": "Asia/Almaty",
    "delhi": "Asia/Kolkata",
    "singapore": "Asia/Singapore",
    "tokyo": "Asia/Tokyo",
}

_TZ_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "moscow": "Moscow (UTC+3)",
        "berlin": "Berlin (UTC+1)",
        "london": "London (UTC+0)",
        "new_york": "New York (UTC-5)",
        "los_angeles": "Los Angeles (UTC-8)",
        "dubai": "Dubai (UTC+4)",
        "almaty": "Almaty (UTC+5)",
        "delhi": "Delhi (UTC+5:30)",
        "singapore": "Singapore (UTC+8)",
        "tokyo": "Tokyo (UTC+9)",
    },
    "ru": {
        "moscow": "Москва (UTC+3)",
        "berlin": "Берлин (UTC+1)",
        "london": "Лондон (UTC+0)",
        "new_york": "Нью-Йорк (UTC-5)",
        "los_angeles": "Лос-Анджелес (UTC-8)",
        "dubai": "Дубай (UTC+4)",
        "almaty": "Алматы (UTC+5)",
        "delhi": "Дели (UTC+5:30)",
        "singapore": "Сингапур (UTC+8)",
        "tokyo": "Токио (UTC+9)",
    },
}


def normalize_language(language: str | None) -> str:
    if not language:
        return DEFAULT_LANGUAGE
    base = language.strip().lower().split("-", 1)[0]
    if base in SUPPORTED_LANGUAGES:
        return base
    return DEFAULT_LANGUAGE


def tr(language: str | None, key: str, **kwargs: object) -> str:
    lang = normalize_language(language)
    default_table = _TRANSLATIONS[DEFAULT_LANGUAGE]
    template = _TRANSLATIONS.get(lang, default_table).get(key, default_table[key])
    return template.format(**kwargs)


def key_values(key: str) -> tuple[str, ...]:
    out: list[str] = []
    for lang in SUPPORTED_LANGUAGES:
        value = _TRANSLATIONS[lang].get(key)
        if value and value not in out:
            out.append(value)
    return tuple(out)


def language_choice_rows() -> list[list[str]]:
    return [[tr("en", "language_option_en"), tr("en", "language_option_ru")]]


def resolve_language_choice(text: str) -> str | None:
    raw = text.strip()
    if raw.lower() in SUPPORTED_LANGUAGES:
        return raw.lower()
    en_label_en = tr("en", "language_option_en")
    en_label_ru = tr("en", "language_option_ru")
    ru_label_ru = tr("ru", "language_option_ru")
    if raw == en_label_en:
        return "en"
    if raw in {en_label_ru, ru_label_ru}:
        return "ru"
    return None


def language_display_name(language: str) -> str:
    lang = normalize_language(language)
    if lang == "ru":
        return tr("ru", "language_option_ru")
    return tr("en", "language_option_en")


def timezone_choice_rows(language: str | None) -> list[list[str]]:
    lang = normalize_language(language)
    labels = _TZ_LABELS[lang]
    rows: list[list[str]] = []
    for row in _TZ_CHOICE_IDS:
        rows.append([labels[item_id] for item_id in row])
    return rows


def resolve_timezone_choice(text: str) -> str | None:
    raw = text.strip()
    if not raw:
        return None
    for lang in SUPPORTED_LANGUAGES:
        labels = _TZ_LABELS[lang]
        for item_id, label in labels.items():
            if raw == label:
                return _TZ_BY_ID[item_id]
    return None
