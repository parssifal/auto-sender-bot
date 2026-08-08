from __future__ import annotations

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "ru", "de", "ar", "hi", "zh", "ja")

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
        "broadcast_choose_destinations": "Choose one or more channels/chats.\nSelected: {count}",
        "broadcast_choose_one": "Choose at least one channel/chat first.",
        "broadcast_destination_missing": "This channel/chat is no longer available. Refresh the list.",
        "timezone_required": "Set timezone first: /timezone",
        "enter_datetime": (
            "Enter date and time: `DD.MM.YYYY HH:MM` (for example `12.03.2026 12:15`).\n"
            "Or use the calendar and quick buttons below."
        ),
        "invalid_datetime_format": "Invalid format. Example: `12.03.2026 12:15`",
        "datetime_dst_gap": "That time doesn't exist on that day (clocks move forward for DST). Choose another time.",
        "datetime_future_required": "Time must be in the future.",
        "datetime_min_lead_required": "Time must be at least 5 minutes in the future.",
        "schedule_time_prompt": "Choose time for {date_label}, or send full date and time manually.",
        "schedule_picker_invalid": "This date/time selection is no longer valid. Choose again.",
        "schedule_quick_1h": "In 1 hour",
        "schedule_quick_today_20": "Today 20:00",
        "schedule_quick_tomorrow_9": "Tomorrow 09:00",
        "schedule_quick_next_monday": "Next Monday 09:00",
        "repeat_choose_interval": "Choose a repeat interval:",
        "repeat_enter_datetime": (
            "Choose the first date and time for the recurring post.\n"
            "Use the calendar and quick buttons below, or send `DD.MM.YYYY HH:MM`."
        ),
        "repeat_confirm_template": (
            "Confirm recurring post:\n"
            "- Where: {where}\n"
            "- First run: {local_time} ({tz_name})\n"
            "- Interval: {interval}\n"
            "- Type: {kind}\n"
        ),
        "broadcast_confirm_template": (
            "Confirm broadcast:\n"
            "- Channels ({count}):\n{where_lines}\n"
            "- When: {local_time} ({tz_name})\n"
            "- Type: {kind}\n"
            "- Preview: {preview}\n"
        ),
        "broadcast_created_ok": (
            "Broadcast scheduled.\n"
            "- Channels: {count}\n"
            "- When: {local_time} ({tz_name})\n"
            "- Posts:\n{lines}"
        ),
        "repeat_created_ok": (
            "Done! Recurring post created: {interval}, first run {local_time} ({tz_name}). "
            "series={pattern_id}"
        ),
        "repeat_interval_daily": "Every day",
        "repeat_interval_weekly": "Every week",
        "repeat_interval_weekdays": "Weekdays",
        "repeat_interval_custom": "Custom",
        "repeat_custom_unavailable": "Custom repeat is not supported yet.",
        "repeat_interval_invalid": "Unknown repeat interval.",
        "repeat_cancel_usage": "Usage: /repeat_cancel <series_id>",
        "repeat_cancel_ok": "Recurring series stopped. series={pattern_id}",
        "repeat_cancel_missing": "Recurring series not found.",
        "repeat_list_empty": "No active recurring series.",
        "repeat_list_header": "Active recurring series:\n\n{lines}",
        "repeat_list_item": (
            "series={pattern_id}\n"
            "- Where: {where}\n"
            "- Interval: {interval}\n"
            "- Next: {next_run}\n"
            "- Count: {count}"
        ),
        "repeat_list_next_missing": "unavailable",
        "team_role_owner": "owner",
        "team_role_editor": "editor",
        "team_role_viewer": "viewer",
        "team_create_usage": "Usage: /team_create <name>",
        "team_create_ok": (
            "Team created. team={team_id}\n"
            "- Name: {team_name}\n"
            "- Your role: {role}"
        ),
        "team_missing": "Team not found or unavailable.",
        "team_invite_usage": "Usage: /team_invite <team_id> [viewer|editor]",
        "team_invite_role_invalid": "Invite role must be viewer or editor.",
        "team_invite_created": (
            "Invite created. team={team_id}\n"
            "- Name: {team_name}\n"
            "- Role: {role}\n"
            "- Expires: {expires_at} ({tz_name})\n"
            "- Link: {link}"
        ),
        "team_invite_missing": "Invite link is invalid or unavailable.",
        "team_invite_accept_ok": (
            "You joined the team. team={team_id}\n"
            "- Name: {team_name}\n"
            "- Role: {role}"
        ),
        "team_invite_expired": "This invite link has expired.",
        "team_invite_used": "This invite link is already used.",
        "team_invite_already_member": (
            "You are already in this team. team={team_id}\n"
            "- Name: {team_name}\n"
            "- Role: {role}"
        ),
        "team_members_usage": "Usage: /team_members <team_id>",
        "team_members_none": "You do not have teams yet.",
        "team_members_choose": "Your teams:\n\n{lines}\n\nRun /team_members <team_id>.",
        "team_members_choose_item": "team={team_id} - {team_name} - {role}",
        "team_members_header": (
            "team={team_id}\n"
            "- Name: {team_name}\n"
            "- Your role: {role}\n"
            "- Members:\n{lines}"
        ),
        "team_members_item": "- {role}: user {user_id}",
        "draft_filter_all": "All",
        "draft_filter_mine": "Mine",
        "draft_filter_team": "Teams",
        "draft_list_empty": "No drafts in {scope}.",
        "draft_list_header": "Drafts: {scope}\n\n{lines}",
        "draft_list_item": (
            "draft={draft_id}\n"
            "- Space: {location}\n"
            "- Where: {where}\n"
            "- Type: {kind}\n"
            "- Preview: {preview}"
        ),
        "draft_detail_header": (
            "draft={draft_id}\n"
            "- Space: {location}\n"
            "- Where: {where}\n"
            "- Type: {kind}\n"
            "- Updated: {updated_at}\n"
            "- Preview: {preview}\n"
            "- Actions: {actions}"
        ),
        "draft_location_personal": "Personal",
        "draft_location_team": "Team: {team_name}",
        "draft_preview_empty": "empty",
        "draft_preview_media_no_caption": "media without caption",
        "draft_actions_view_only": "view only",
        "draft_missing": "Draft not found or unavailable.",
        "draft_action_unavailable": "This action is not available yet.",
        "draft_create_scope_prompt": "Save draft as:",
        "draft_create_scope_invalid": "This draft space is no longer available.",
        "draft_created_ok": (
            "Draft saved. draft={draft_id}\n"
            "- Space: {location}\n"
            "- Where: {where}\n"
            "- Type: {kind}"
        ),
        "draft_edit_prompt": (
            "Editing draft={draft_id}\n"
            "- Space: {location}\n"
            "- Where: {where}\n"
            "- Current type: {kind}\n"
            "Send new text, photo, or video to replace the content. Tap Done when ready."
        ),
        "draft_updated_ok": (
            "Draft updated. draft={draft_id}\n"
            "- Space: {location}\n"
            "- Where: {where}\n"
            "- Type: {kind}"
        ),
        "draft_delete_usage": "Usage: /draft_delete <draft_id>",
        "draft_delete_confirm": (
            "Delete draft={draft_id}?\n"
            "- Space: {location}\n"
            "- Where: {where}\n"
            "- Type: {kind}"
        ),
        "draft_delete_ok": "Draft deleted. draft={draft_id}",
        "draft_post_enter_datetime": (
            "Choose date and time for draft={draft_id}.\n"
            "- Where: {where}\n"
            "Use the calendar and quick buttons below, or send `DD.MM.YYYY HH:MM`."
        ),
        "draft_post_created_ok": (
            "Draft scheduled. draft={draft_id}\n"
            "- When: {local_time} ({tz_name})\n"
            "- Post id: {post_id}"
        ),
        "schedule_weekday_mon": "Mo",
        "schedule_weekday_tue": "Tu",
        "schedule_weekday_wed": "We",
        "schedule_weekday_thu": "Th",
        "schedule_weekday_fri": "Fr",
        "schedule_weekday_sat": "Sa",
        "schedule_weekday_sun": "Su",
        "schedule_kind_prompt": "What do you want to schedule?",
        "schedule_text_prompt": "Send message text in one message.",
        "schedule_media_prompt": "Send photo/video (single media or album 2-10). Send caption as text (can be after media).",
        "schedule_post_prompt": (
            "Send a post: text, photo, or video.\n"
            "If text is sent before media, it will be above media; if after media, below media.\n"
            "Tap \"Done\" when your post is ready."
        ),
        "schedule_unknown_type": "Unknown type.",
        "text_required": "Text is required.",
        "confirm_template": "Confirm:\n- Where: {where}\n- When: {local_time} ({tz_name})\n- Type: {kind}\n",
        "kind_text": "text",
        "kind_media": "media x{count}",
        "text_saved": "Text saved. Tap \"Done\" to schedule text, or send photo/video to switch to media.",
        "caption_updated": "Caption updated. Media: {count}/10",
        "media_limit": "Limit is 10 media. Tap \"Done\" or create another post.",
        "limit_posts": "⚠️ You've reached the limit of {limit} scheduled posts. Cancel some from your queue, then try again.",
        "limit_drafts": "⚠️ You've reached the limit of {limit} drafts. Delete some drafts, then try again.",
        "limit_destinations": "⚠️ You've reached the limit of {limit} linked channels/chats. Unlink some, then try again.",
        "limit_recurring": "⚠️ You've reached the limit of {limit} recurring posts. Cancel some, then try again.",
        "media_send_prompt": "Please send text, a photo, or a video.",
        "media_added": "Added: {count}/10 media.",
        "media_cleared": "Draft cleared. Send text/photo/video again.",
        "post_need_content": "Send text or at least one photo/video first.",
        "media_need_at_least_one": "Send at least one photo/video first.",
        "caption_position_prompt": "Where should the caption be?",
        "scheduled_ok": "Done! Scheduled for {local_time} ({tz_name}). id={post_id}",
        "schedule_next_prompt": (
            "Send next date and time for the same destination ({where}) in format DD.MM.YYYY HH:MM,\n"
            "or use the calendar and quick buttons below.\n"
            "Use /schedule to choose another channel/chat, or /cancel to stop."
        ),
        "queue_empty": "Queue is empty.",
        "queue_header": "Upcoming posts:\n{lines}",
        "queue_cancel_ok": "Cancelled",
        "queue_cancel_missing": "Not found / already sent",
        "edit_empty": "No editable pending posts.",
        "edit_list_header": "Choose a post to edit:\n\n{lines}",
        "edit_list_item": (
            "id={post_id}\n"
            "- Where: {where}\n"
            "- When: {local_time}\n"
            "- Type: {kind}\n"
            "- Preview: {preview}"
        ),
        "edit_choose_field": (
            "Editing post id={post_id}\n"
            "- Where: {where}\n"
            "- When: {local_time} ({tz_name})\n"
            "- Type: {kind}\n"
            "- Preview: {preview}\n\n"
            "Choose what to change."
        ),
        "edit_text_prompt": "Send new text/caption for post id={post_id}.\nCurrent: {preview}",
        "edit_media_prompt": (
            "Send new photo/video for post id={post_id}.\n"
            "Current text will be kept unless you send a new caption.\n"
            "Current: {preview}"
        ),
        "edit_time_prompt": "Choose new date and time for post id={post_id}.",
        "edit_post_missing": "Post not found or unavailable.",
        "edit_post_ambiguous": "This short id matches multiple pending posts. Use a longer id.",
        "edit_post_recurring_blocked": "This post belongs to a recurring series and cannot be edited here yet.",
        "edit_text_updated_ok": "Text updated. id={post_id}",
        "edit_media_updated_ok": "Media updated. id={post_id}\n- Type: {kind}",
        "edit_time_updated_ok": "Time updated. id={post_id}\n- When: {local_time} ({tz_name})",
        "delete_empty": "No deletable pending posts.",
        "delete_list_header": "Choose a post to delete:\n\n{lines}",
        "delete_list_item": (
            "id={post_id}\n"
            "- Where: {where}\n"
            "- When: {local_time}\n"
            "- Type: {kind}\n"
            "- Preview: {preview}"
        ),
        "delete_confirm": (
            "Delete post id={post_id}?\n"
            "- Where: {where}\n"
            "- When: {local_time} ({tz_name})\n"
            "- Type: {kind}\n"
            "- Preview: {preview}"
        ),
        "delete_post_missing": "Post not found or unavailable.",
        "delete_post_ambiguous": "This short id matches multiple pending posts. Use a longer id.",
        "delete_post_recurring_blocked": "This post belongs to a recurring series and cannot be deleted here yet.",
        "delete_post_ok": "Post deleted. id={post_id}",
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
        "admin_intro": "📊 Admin panel — open the dashboard to see bot stats.",
        "admin_open_btn": "Open dashboard",
        "admin_not_configured": "Admin panel is not configured. Set WEBAPP_URL to enable the mini app.",
        "app_intro": "📱 Your posts — open the app to view, reschedule, or cancel.",
        "app_open_btn": "Open my posts",
        "app_not_configured": "The app is not configured yet.",
        "menu_button_app": "Menu",
        "admin_broadcast_prompt": "Send the text to broadcast to ALL users. /cancel to abort.",
        "admin_broadcast_empty": "Empty message. Send some text, or /cancel.",
        "admin_broadcast_confirm": "Broadcast this to {count} user(s)?\n\n———\n{preview}",
        "admin_broadcast_confirm_btn": "Send to all",
        "admin_broadcast_cancel_btn": "Cancel",
        "admin_broadcast_sending": "Sending…",
        "admin_broadcast_report": "Done. Delivered: {delivered}, blocked: {blocked}, failed: {failed} (of {total}).",
        "admin_broadcast_cancelled": "Broadcast cancelled.",
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
        "language_option_ru": "Русский",
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
        "btn_text": "Text",
        "btn_media": "Media (photo/video)",
        "btn_cancel": "Cancel",
        "btn_done": "Done",
        "btn_clear": "Clear",
        "btn_back": "Back",
        "btn_caption_above": "Caption above",
        "btn_caption_below": "Caption below",
        "btn_confirm": "Confirm",
        "btn_queue_cancel": "Cancel {label}",
        "btn_edit_post": "Edit {label}",
        "btn_edit_text": "Text",
        "btn_edit_time": "Time",
        "btn_edit_media": "Media",
        "btn_delete_post": "Delete {label}",
        "btn_repeat_stop": "Stop {label}",
        "btn_draft_open": "Open {label}",
        "btn_draft_edit": "Edit",
        "btn_draft_delete": "Delete",
        "btn_draft_publish": "Publish",
        "btn_view_post": "👁 {label}",
        "view_not_found": "Post not found or unavailable.",
        "view_post_info": "Preview — id={post_id}\n• Where: {where}\n• When: {local_time} ({tz_name})\n• Type: {kind}",
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
        "broadcast_choose_destinations": "Выберите один или несколько каналов/чатов.\nВыбрано: {count}",
        "broadcast_choose_one": "Сначала выберите хотя бы один канал/чат.",
        "broadcast_destination_missing": "Этот канал/чат больше недоступен. Обновите список.",
        "timezone_required": "Сначала задайте часовой пояс: /timezone",
        "enter_datetime": (
            "Введите дату и время: `ДД.ММ.ГГГГ ЧЧ:ММ` (например `12.03.2026 12:15`).\n"
            "Или используйте календарь и быстрые кнопки ниже."
        ),
        "invalid_datetime_format": "Неверный формат. Пример: `12.03.2026 12:15`",
        "datetime_dst_gap": "Это время в этот день не существует (перевод часов на летнее время). Выберите другое время.",
        "datetime_future_required": "Время должно быть в будущем.",
        "datetime_min_lead_required": "Время должно быть минимум через 5 минут.",
        "schedule_time_prompt": "Выберите время для {date_label} или отправьте полную дату и время вручную.",
        "schedule_picker_invalid": "Этот выбор даты/времени уже неактуален. Выберите заново.",
        "schedule_quick_1h": "Через 1 час",
        "schedule_quick_today_20": "Сегодня 20:00",
        "schedule_quick_tomorrow_9": "Завтра 09:00",
        "schedule_quick_next_monday": "Следующий понедельник 09:00",
        "repeat_choose_interval": "Выберите интервал повторения:",
        "repeat_enter_datetime": (
            "Выберите первую дату и время для повторяющегося поста.\n"
            "Используйте календарь и быстрые кнопки ниже или отправьте `ДД.ММ.ГГГГ ЧЧ:ММ`."
        ),
        "repeat_confirm_template": (
            "Подтвердите повторяющийся пост:\n"
            "- Куда: {where}\n"
            "- Первый запуск: {local_time} ({tz_name})\n"
            "- Интервал: {interval}\n"
            "- Тип: {kind}\n"
        ),
        "broadcast_confirm_template": (
            "Подтвердите бродкаст:\n"
            "- Каналы ({count}):\n{where_lines}\n"
            "- Когда: {local_time} ({tz_name})\n"
            "- Тип: {kind}\n"
            "- Превью: {preview}\n"
        ),
        "broadcast_created_ok": (
            "Бродкаст поставлен в очередь.\n"
            "- Каналы: {count}\n"
            "- Когда: {local_time} ({tz_name})\n"
            "- Post id:\n{lines}"
        ),
        "repeat_created_ok": (
            "Ок! Повторяющийся пост создан: {interval}, первый запуск {local_time} ({tz_name}). "
            "series={pattern_id}"
        ),
        "repeat_interval_daily": "Каждый день",
        "repeat_interval_weekly": "Каждую неделю",
        "repeat_interval_weekdays": "По будням",
        "repeat_interval_custom": "Свое правило",
        "repeat_custom_unavailable": "Свое правило пока не поддерживается.",
        "repeat_interval_invalid": "Неизвестный интервал повторения.",
        "repeat_cancel_usage": "Использование: /repeat_cancel <series_id>",
        "repeat_cancel_ok": "Повторяющаяся серия остановлена. series={pattern_id}",
        "repeat_cancel_missing": "Повторяющаяся серия не найдена.",
        "repeat_list_empty": "Активных повторяющихся серий нет.",
        "repeat_list_header": "Активные повторяющиеся серии:\n\n{lines}",
        "repeat_list_item": (
            "series={pattern_id}\n"
            "- Куда: {where}\n"
            "- Интервал: {interval}\n"
            "- Следующий запуск: {next_run}\n"
            "- Счётчик: {count}"
        ),
        "repeat_list_next_missing": "недоступен",
        "team_role_owner": "владелец",
        "team_role_editor": "редактор",
        "team_role_viewer": "наблюдатель",
        "team_create_usage": "Использование: /team_create <name>",
        "team_create_ok": (
            "Команда создана. team={team_id}\n"
            "- Название: {team_name}\n"
            "- Ваша роль: {role}"
        ),
        "team_missing": "Команда не найдена или недоступна.",
        "team_invite_usage": "Использование: /team_invite <team_id> [viewer|editor]",
        "team_invite_role_invalid": "Роль приглашения должна быть viewer или editor.",
        "team_invite_created": (
            "Приглашение создано. team={team_id}\n"
            "- Название: {team_name}\n"
            "- Роль: {role}\n"
            "- Действует до: {expires_at} ({tz_name})\n"
            "- Ссылка: {link}"
        ),
        "team_invite_missing": "Ссылка-приглашение недействительна или недоступна.",
        "team_invite_accept_ok": (
            "Вы вступили в команду. team={team_id}\n"
            "- Название: {team_name}\n"
            "- Роль: {role}"
        ),
        "team_invite_expired": "Срок действия ссылки-приглашения истёк.",
        "team_invite_used": "Эта ссылка-приглашение уже использована.",
        "team_invite_already_member": (
            "Вы уже состоите в этой команде. team={team_id}\n"
            "- Название: {team_name}\n"
            "- Роль: {role}"
        ),
        "team_members_usage": "Использование: /team_members <team_id>",
        "team_members_none": "У вас пока нет команд.",
        "team_members_choose": "Ваши команды:\n\n{lines}\n\nЗапустите /team_members <team_id>.",
        "team_members_choose_item": "team={team_id} - {team_name} - {role}",
        "team_members_header": (
            "team={team_id}\n"
            "- Название: {team_name}\n"
            "- Ваша роль: {role}\n"
            "- Участники:\n{lines}"
        ),
        "team_members_item": "- {role}: user {user_id}",
        "draft_filter_all": "Все",
        "draft_filter_mine": "Мои",
        "draft_filter_team": "Команды",
        "draft_list_empty": "В разделе {scope} черновиков нет.",
        "draft_list_header": "Черновики: {scope}\n\n{lines}",
        "draft_list_item": (
            "draft={draft_id}\n"
            "- Область: {location}\n"
            "- Куда: {where}\n"
            "- Тип: {kind}\n"
            "- Превью: {preview}"
        ),
        "draft_detail_header": (
            "draft={draft_id}\n"
            "- Область: {location}\n"
            "- Куда: {where}\n"
            "- Тип: {kind}\n"
            "- Обновлён: {updated_at}\n"
            "- Превью: {preview}\n"
            "- Действия: {actions}"
        ),
        "draft_location_personal": "Личный",
        "draft_location_team": "Команда: {team_name}",
        "draft_preview_empty": "пусто",
        "draft_preview_media_no_caption": "медиа без подписи",
        "draft_actions_view_only": "только просмотр",
        "draft_missing": "Черновик не найден или недоступен.",
        "draft_action_unavailable": "Это действие пока недоступно.",
        "draft_create_scope_prompt": "Куда сохранить черновик:",
        "draft_create_scope_invalid": "Этот вариант сохранения черновика больше недоступен.",
        "draft_created_ok": (
            "Черновик сохранён. draft={draft_id}\n"
            "- Область: {location}\n"
            "- Куда: {where}\n"
            "- Тип: {kind}"
        ),
        "draft_edit_prompt": (
            "Редактирование draft={draft_id}\n"
            "- Область: {location}\n"
            "- Куда: {where}\n"
            "- Текущий тип: {kind}\n"
            "Отправьте новый текст, фото или видео, чтобы заменить содержимое. Когда всё готово, нажмите «Готово»."
        ),
        "draft_updated_ok": (
            "Черновик обновлён. draft={draft_id}\n"
            "- Область: {location}\n"
            "- Куда: {where}\n"
            "- Тип: {kind}"
        ),
        "draft_delete_usage": "Использование: /draft_delete <draft_id>",
        "draft_delete_confirm": (
            "Удалить draft={draft_id}?\n"
            "- Область: {location}\n"
            "- Куда: {where}\n"
            "- Тип: {kind}"
        ),
        "draft_delete_ok": "Черновик удалён. draft={draft_id}",
        "draft_post_enter_datetime": (
            "Выберите дату и время для draft={draft_id}.\n"
            "- Куда: {where}\n"
            "Используйте календарь и быстрые кнопки ниже или отправьте `ДД.ММ.ГГГГ ЧЧ:ММ`."
        ),
        "draft_post_created_ok": (
            "Черновик поставлен в очередь. draft={draft_id}\n"
            "- Когда: {local_time} ({tz_name})\n"
            "- Post id: {post_id}"
        ),
        "schedule_weekday_mon": "Пн",
        "schedule_weekday_tue": "Вт",
        "schedule_weekday_wed": "Ср",
        "schedule_weekday_thu": "Чт",
        "schedule_weekday_fri": "Пт",
        "schedule_weekday_sat": "Сб",
        "schedule_weekday_sun": "Вс",
        "schedule_kind_prompt": "Что вы хотите запланировать?",
        "schedule_text_prompt": "Отправьте текст сообщения одним сообщением.",
        "schedule_media_prompt": "Отправьте фото/видео (можно несколько или альбом 2–10). Подпись отправьте текстом (можно после медиа).",
        "schedule_post_prompt": (
            "Отправьте пост: текст, фото или видео.\n"
            "Если текст отправлен до медиа — он будет сверху, если после медиа — снизу.\n"
            "Когда пост готов, нажмите «Готово»."
        ),
        "schedule_unknown_type": "Неизвестный тип.",
        "text_required": "Нужен текст.",
        "confirm_template": "Подтвердите:\n- Куда: {where}\n- Когда: {local_time} ({tz_name})\n- Тип: {kind}\n",
        "kind_text": "текст",
        "kind_media": "медиа x{count}",
        "text_saved": "Текст сохранён. Нажмите «Готово», чтобы запланировать текст, или отправьте фото/видео, чтобы сделать медиа-пост.",
        "caption_updated": "Подпись обновлена. Медиа: {count}/10",
        "media_limit": "Лимит 10 медиа. Нажмите «Готово» или сделайте второй пост.",
        "limit_posts": "⚠️ Достигнут лимит запланированных постов ({limit}). Отмените часть в очереди и попробуйте снова.",
        "limit_drafts": "⚠️ Достигнут лимит черновиков ({limit}). Удалите часть и попробуйте снова.",
        "limit_destinations": "⚠️ Достигнут лимит привязанных каналов/чатов ({limit}). Отвяжите часть и попробуйте снова.",
        "limit_recurring": "⚠️ Достигнут лимит повторяющихся постов ({limit}). Отмените часть и попробуйте снова.",
        "media_send_prompt": "Пожалуйста, отправьте текст, фото или видео.",
        "media_added": "Добавлено: {count}/10 медиа.",
        "media_cleared": "Черновик очищен. Отправьте текст/фото/видео заново.",
        "post_need_content": "Сначала отправьте текст или хотя бы одно фото/видео.",
        "media_need_at_least_one": "Сначала отправьте хотя бы одно фото/видео.",
        "caption_position_prompt": "Где должна быть подпись?",
        "scheduled_ok": "Ок! Запланировано на {local_time} ({tz_name}). id={post_id}",
        "schedule_next_prompt": (
            "Введите следующую дату и время для этого же канала/чата ({where}) в формате ДД.ММ.ГГГГ ЧЧ:ММ,\n"
            "или используйте календарь и быстрые кнопки ниже.\n"
            "Используйте /schedule, чтобы выбрать другой канал/чат, или /cancel для выхода."
        ),
        "queue_empty": "Очередь пуста.",
        "queue_header": "Ближайшие посты:\n{lines}",
        "queue_cancel_ok": "Отменено",
        "queue_cancel_missing": "Не найдено/уже отправлено",
        "edit_empty": "Нет доступных для редактирования pending-постов.",
        "edit_list_header": "Выберите пост для редактирования:\n\n{lines}",
        "edit_list_item": (
            "id={post_id}\n"
            "- Куда: {where}\n"
            "- Когда: {local_time}\n"
            "- Тип: {kind}\n"
            "- Превью: {preview}"
        ),
        "edit_choose_field": (
            "Редактирование post id={post_id}\n"
            "- Куда: {where}\n"
            "- Когда: {local_time} ({tz_name})\n"
            "- Тип: {kind}\n"
            "- Превью: {preview}\n\n"
            "Выберите, что изменить."
        ),
        "edit_text_prompt": "Отправьте новый текст/подпись для post id={post_id}.\nТекущее: {preview}",
        "edit_media_prompt": (
            "Отправьте новое фото/видео для post id={post_id}.\n"
            "Текущий текст сохранится, если не пришлёте новую подпись.\n"
            "Текущее: {preview}"
        ),
        "edit_time_prompt": "Выберите новую дату и время для post id={post_id}.",
        "edit_post_missing": "Пост не найден или уже недоступен.",
        "edit_post_ambiguous": "Этот short_id совпал с несколькими pending-постами. Укажите более длинный id.",
        "edit_post_recurring_blocked": "Этот пост относится к recurring-серии и пока не редактируется через /edit.",
        "edit_text_updated_ok": "Текст обновлён. id={post_id}",
        "edit_media_updated_ok": "Медиа обновлено. id={post_id}\n- Тип: {kind}",
        "edit_time_updated_ok": "Время обновлено. id={post_id}\n- Когда: {local_time} ({tz_name})",
        "delete_empty": "Нет доступных для удаления pending-постов.",
        "delete_list_header": "Выберите пост для удаления:\n\n{lines}",
        "delete_list_item": (
            "id={post_id}\n"
            "- Куда: {where}\n"
            "- Когда: {local_time}\n"
            "- Тип: {kind}\n"
            "- Превью: {preview}"
        ),
        "delete_confirm": (
            "Удалить post id={post_id}?\n"
            "- Куда: {where}\n"
            "- Когда: {local_time} ({tz_name})\n"
            "- Тип: {kind}\n"
            "- Превью: {preview}"
        ),
        "delete_post_missing": "Пост не найден или уже недоступен.",
        "delete_post_ambiguous": "Этот short_id совпал с несколькими pending-постами. Укажите более длинный id.",
        "delete_post_recurring_blocked": "Этот пост относится к recurring-серии и пока не удаляется через /delete.",
        "delete_post_ok": "Пост удалён. id={post_id}",
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
        "admin_intro": "📊 Админ-панель — откройте дашборд со статистикой бота.",
        "admin_open_btn": "Открыть панель",
        "admin_not_configured": "Админ-панель не настроена. Задайте WEBAPP_URL, чтобы включить мини-приложение.",
        "app_intro": "📱 Ваши посты — откройте приложение, чтобы посмотреть, перенести или отменить.",
        "app_open_btn": "Открыть мои посты",
        "app_not_configured": "Приложение пока не настроено.",
        "menu_button_app": "Меню",
        "admin_broadcast_prompt": "Отправьте текст для рассылки ВСЕМ пользователям. /cancel — отмена.",
        "admin_broadcast_empty": "Пустое сообщение. Пришлите текст или /cancel.",
        "admin_broadcast_confirm": "Разослать это {count} пользователю(ям)?\n\n———\n{preview}",
        "admin_broadcast_confirm_btn": "Отправить всем",
        "admin_broadcast_cancel_btn": "Отмена",
        "admin_broadcast_sending": "Отправляю…",
        "admin_broadcast_report": "Готово. Доставлено: {delivered}, заблокировали: {blocked}, ошибок: {failed} (из {total}).",
        "admin_broadcast_cancelled": "Рассылка отменена.",
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
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
        "btn_text": "Текст",
        "btn_media": "Медиа (фото/видео)",
        "btn_cancel": "Отмена",
        "btn_done": "Готово",
        "btn_clear": "Очистить",
        "btn_back": "Назад",
        "btn_caption_above": "Подпись сверху",
        "btn_caption_below": "Подпись снизу",
        "btn_confirm": "Подтвердить",
        "btn_queue_cancel": "Отменить {label}",
        "btn_edit_post": "Изменить {label}",
        "btn_edit_text": "Текст",
        "btn_edit_time": "Время",
        "btn_edit_media": "Медиа",
        "btn_delete_post": "Удалить {label}",
        "btn_repeat_stop": "Остановить {label}",
        "btn_draft_open": "Открыть {label}",
        "btn_draft_edit": "Изменить",
        "btn_draft_delete": "Удалить",
        "btn_draft_publish": "Запланировать",
        "btn_view_post": "👁 {label}",
        "view_not_found": "Пост не найден или недоступен.",
        "view_post_info": "Предпросмотр — id={post_id}\n• Куда: {where}\n• Когда: {local_time} ({tz_name})\n• Тип: {kind}",
    },
    "de": {
        "menu_schedule": "Planen",
        "menu_queue": "Warteschlange",
        "menu_destinations": "Meine Kanäle/Chats",
        "menu_timezone": "Zeitzone",
        "menu_language": "Sprache",
        "menu_button_app": "Menü",
        "timezone_location_button": "Standort senden",
        "language_prompt": "Oberflächensprache wählen:",
        "language_saved": "Sprache gespeichert: {language_name}",
        "language_invalid": "Unbekannte Sprache. Bitte über die Buttons wählen.",
        "language_option_en": "English",
        "language_option_ru": "Русский",
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
    },
    "ar": {
        "menu_schedule": "جدولة",
        "menu_queue": "الطابور",
        "menu_destinations": "قنواتي/دردشاتي",
        "menu_timezone": "المنطقة الزمنية",
        "menu_language": "اللغة",
        "menu_button_app": "القائمة",
        "timezone_location_button": "إرسال الموقع",
        "language_prompt": "اختر لغة الواجهة:",
        "language_saved": "تم حفظ اللغة: {language_name}",
        "language_invalid": "لغة غير معروفة. اختر من الأزرار.",
        "language_option_en": "English",
        "language_option_ru": "Русский",
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
    },
    "hi": {
        "menu_schedule": "शेड्यूल",
        "menu_queue": "कतार",
        "menu_destinations": "मेरे चैनल/चैट",
        "menu_timezone": "समय क्षेत्र",
        "menu_language": "भाषा",
        "menu_button_app": "मेनू",
        "timezone_location_button": "लोकेशन भेजें",
        "language_prompt": "इंटरफ़ेस भाषा चुनें:",
        "language_saved": "भाषा सहेजी गई: {language_name}",
        "language_invalid": "अज्ञात भाषा। बटन से चुनें।",
        "language_option_en": "English",
        "language_option_ru": "Русский",
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
    },
    "zh": {
        "menu_schedule": "计划",
        "menu_queue": "队列",
        "menu_destinations": "我的频道/群聊",
        "menu_timezone": "时区",
        "menu_language": "语言",
        "menu_button_app": "菜单",
        "timezone_location_button": "发送位置",
        "language_prompt": "请选择界面语言:",
        "language_saved": "语言已保存: {language_name}",
        "language_invalid": "未知语言，请使用按钮选择。",
        "language_option_en": "English",
        "language_option_ru": "Русский",
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
    },
    "ja": {
        "menu_schedule": "予約",
        "menu_queue": "キュー",
        "menu_destinations": "自分のチャンネル/チャット",
        "menu_timezone": "タイムゾーン",
        "menu_language": "言語",
        "menu_button_app": "メニュー",
        "timezone_location_button": "位置情報を送信",
        "language_prompt": "表示言語を選択してください:",
        "language_saved": "言語を保存しました: {language_name}",
        "language_invalid": "不明な言語です。ボタンから選択してください。",
        "language_option_en": "English",
        "language_option_ru": "Русский",
        "language_option_de": "Deutsch",
        "language_option_ar": "العربية",
        "language_option_hi": "हिन्दी",
        "language_option_zh": "中文",
        "language_option_ja": "日本語",
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
    "de": {
        "moscow": "Moskau (UTC+3)",
        "berlin": "Berlin (UTC+1)",
        "london": "London (UTC+0)",
        "new_york": "New York (UTC-5)",
        "los_angeles": "Los Angeles (UTC-8)",
        "dubai": "Dubai (UTC+4)",
        "almaty": "Almaty (UTC+5)",
        "delhi": "Delhi (UTC+5:30)",
        "singapore": "Singapur (UTC+8)",
        "tokyo": "Tokio (UTC+9)",
    },
    "ar": {
        "moscow": "موسكو (UTC+3)",
        "berlin": "برلين (UTC+1)",
        "london": "لندن (UTC+0)",
        "new_york": "نيويورك (UTC-5)",
        "los_angeles": "لوس أنجلوس (UTC-8)",
        "dubai": "دبي (UTC+4)",
        "almaty": "ألماتي (UTC+5)",
        "delhi": "دلهي (UTC+5:30)",
        "singapore": "سنغافورة (UTC+8)",
        "tokyo": "طوكيو (UTC+9)",
    },
    "hi": {
        "moscow": "मॉस्को (UTC+3)",
        "berlin": "बर्लिन (UTC+1)",
        "london": "लंदन (UTC+0)",
        "new_york": "न्यूयॉर्क (UTC-5)",
        "los_angeles": "लॉस एंजेलिस (UTC-8)",
        "dubai": "दुबई (UTC+4)",
        "almaty": "अल्माटी (UTC+5)",
        "delhi": "दिल्ली (UTC+5:30)",
        "singapore": "सिंगापुर (UTC+8)",
        "tokyo": "टोक्यो (UTC+9)",
    },
    "zh": {
        "moscow": "莫斯科 (UTC+3)",
        "berlin": "柏林 (UTC+1)",
        "london": "伦敦 (UTC+0)",
        "new_york": "纽约 (UTC-5)",
        "los_angeles": "洛杉矶 (UTC-8)",
        "dubai": "迪拜 (UTC+4)",
        "almaty": "阿拉木图 (UTC+5)",
        "delhi": "德里 (UTC+5:30)",
        "singapore": "新加坡 (UTC+8)",
        "tokyo": "东京 (UTC+9)",
    },
    "ja": {
        "moscow": "モスクワ (UTC+3)",
        "berlin": "ベルリン (UTC+1)",
        "london": "ロンドン (UTC+0)",
        "new_york": "ニューヨーク (UTC-5)",
        "los_angeles": "ロサンゼルス (UTC-8)",
        "dubai": "ドバイ (UTC+4)",
        "almaty": "アルマトイ (UTC+5)",
        "delhi": "デリー (UTC+5:30)",
        "singapore": "シンガポール (UTC+8)",
        "tokyo": "東京 (UTC+9)",
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
        value = _TRANSLATIONS.get(lang, {}).get(key)
        if value and value not in out:
            out.append(value)
    return tuple(out)


def language_choice_rows() -> list[list[str]]:
    labels = [tr("en", f"language_option_{code}") for code in SUPPORTED_LANGUAGES]
    return [labels[i : i + 2] for i in range(0, len(labels), 2)]


def resolve_language_choice(text: str) -> str | None:
    raw = text.strip()
    if raw.lower() in SUPPORTED_LANGUAGES:
        return raw.lower()

    for code in SUPPORTED_LANGUAGES:
        key = f"language_option_{code}"
        labels = {tr("en", key)}
        for lang in SUPPORTED_LANGUAGES:
            value = _TRANSLATIONS.get(lang, {}).get(key)
            if value:
                labels.add(value)
        if raw in labels:
            return code
    return None


def language_display_name(language: str) -> str:
    lang = normalize_language(language)
    return tr("en", f"language_option_{lang}")


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
