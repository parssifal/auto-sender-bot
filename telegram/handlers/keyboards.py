from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from core.rbac import DraftPermissions
from core.services._shared import _destination_label, _normalize_selected_chat_ids
from core.state import Destination, DraftRow, RecurringPattern, RecurringPatternSummary, Team
from core.time_picker import TimePicker
from telegram.i18n import (
    language_choice_rows,
    timezone_choice_rows,
    tr,
)
from telegram.handlers.states import (
    BroadcastStates, DraftStates, EditStates, RepeatStates, ScheduleStates,
)

_TIME_PICKER = TimePicker()
_SCHEDULE_TIME_MINUTES = (0, 30)
_REPEAT_WEEKDAYS_MASK = 0b0011111
_DRAFT_SCOPES = ("all", "mine", "team")


def _is_time_selection_state(state_name: str | None) -> bool:
    return state_name in {
        ScheduleStates.selecting_time.state,
        RepeatStates.selecting_time.state,
        DraftStates.selecting_time.state,
        BroadcastStates.selecting_time.state,
        EditStates.selecting_time.state,
    }


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


def _destinations_kb(
    destinations: list[Destination],
    page: int,
    has_more: bool,
    *,
    select_prefix: str = "sdsel",
    page_prefix: str = "sdpage",
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for d in destinations:
        title = d.title
        if d.username:
            title = f"{title} (@{d.username})"
        buttons.append([InlineKeyboardButton(text=title[:60], callback_data=f"{select_prefix}:{d.chat_id}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{page_prefix}:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{page_prefix}:{page+1}"))
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _toggle_selected_chat_ids(selected_chat_ids: list[int], chat_id: int, enabled: bool) -> list[int]:
    selected = set(selected_chat_ids)
    if enabled:
        selected.add(chat_id)
    else:
        selected.discard(chat_id)
    return sorted(selected)


def _broadcast_destinations_kb(
    destinations: list[Destination],
    *,
    page: int,
    has_more: bool,
    selected_chat_ids: list[int],
    lang: str,
) -> InlineKeyboardMarkup:
    selected = set(selected_chat_ids)
    buttons: list[list[InlineKeyboardButton]] = []
    for destination in destinations:
        title = _destination_label(destination.title, destination.username)
        is_selected = destination.chat_id in selected
        checkbox = "☑️" if is_selected else "⬜️"
        action = "off" if is_selected else "on"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{checkbox} {title[:56]}",
                    callback_data=f"bc:{destination.chat_id}:{action}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"bcpage:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"bcpage:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append(
        [
            InlineKeyboardButton(
                text=f"{tr(lang, 'btn_done')} ({len(selected_chat_ids)})",
                callback_data="bcdone",
            )
        ]
    )
    buttons.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _repeat_interval_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=tr(lang, "repeat_interval_daily"), callback_data="rint:daily"),
                InlineKeyboardButton(text=tr(lang, "repeat_interval_weekly"), callback_data="rint:weekly"),
            ],
            [
                InlineKeyboardButton(text=tr(lang, "repeat_interval_weekdays"), callback_data="rint:weekdays"),
                InlineKeyboardButton(text=tr(lang, "repeat_interval_custom"), callback_data="rint:custom"),
            ],
            [InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")],
        ]
    )


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


def _queue_edit_kb(posts: list[dict[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_edit_post", label=item["label"]),
                    callback_data=f"qedit:{item['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _queue_delete_kb(posts: list[dict[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_delete_post", label=item["label"]),
                    callback_data=f"qdelask:{item['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _queue_paged_kb(posts: list[dict[str, str]], page: int, has_more: bool, lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_view_post", label=item["label"]),
                    callback_data=f"qview:{item['id']}",
                ),
                InlineKeyboardButton(
                    text=tr(lang, "btn_queue_cancel", label=item["label"]),
                    callback_data=f"qcancel:{item['id']}",
                ),
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"qpage:{page - 1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"qpage:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_paged_kb(posts: list[dict[str, str]], page: int, has_more: bool, lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_edit_post", label=item["label"]),
                    callback_data=f"qedit:{item['id']}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"epage:{page - 1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"epage:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delete_paged_kb(posts: list[dict[str, str]], page: int, has_more: bool, lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in posts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_delete_post", label=item["label"]),
                    callback_data=f"qdelask:{item['id']}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"delpage:{page - 1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"delpage:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_field_kb(*, post_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=tr(lang, "btn_edit_text"), callback_data=f"eact:text:{post_id}"),
                InlineKeyboardButton(text=tr(lang, "btn_edit_time"), callback_data=f"eact:time:{post_id}"),
            ],
            [InlineKeyboardButton(text=tr(lang, "btn_edit_media"), callback_data=f"eact:media:{post_id}")],
            [InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")],
        ]
    )


def _delete_confirm_kb(*, post_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "btn_draft_delete"), callback_data=f"qdelyes:{post_id}")],
            [InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")],
        ]
    )


def _repeats_manage_kb(items: list[RecurringPatternSummary], page: int, has_more: bool, lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_repeat_stop", label=_short_id(item.pattern.id)),
                    callback_data=f"rstop:{page}:{item.pattern.id}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"rlpage:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"rlpage:{page+1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _normalize_draft_scope(scope: str) -> str:
    if scope in _DRAFT_SCOPES:
        return scope
    return "all"


def _draft_scope_label(lang: str, scope: str) -> str:
    return tr(lang, f"draft_filter_{_normalize_draft_scope(scope)}")


def _draft_location_label(lang: str, team_name: str | None) -> str:
    if team_name is None:
        return tr(lang, "draft_location_personal")
    return tr(lang, "draft_location_team", team_name=team_name)


def _draft_preview_text(raw: str | None, *, fallback: str, limit: int) -> str:
    collapsed = " ".join(str(raw or "").split())
    if not collapsed:
        return fallback
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 3]}..."


def _draft_action_labels(lang: str, permissions: DraftPermissions) -> str:
    labels: list[str] = []
    if permissions.can_edit:
        labels.append(tr(lang, "btn_draft_edit"))
    if permissions.can_delete:
        labels.append(tr(lang, "btn_draft_delete"))
    if permissions.can_publish:
        labels.append(tr(lang, "btn_draft_publish"))
    if not labels:
        return tr(lang, "draft_actions_view_only")
    return ", ".join(labels)


def _draft_post_prompt_text(lang: str, *, draft_id: str | None, where: str) -> str:
    return tr(lang, "draft_post_enter_datetime", draft_id=_short_id(draft_id or ""), where=where)


def _team_role_label(lang: str, role: str) -> str:
    return tr(lang, f"team_role_{role}")


def _drafts_manage_kb(drafts: list[DraftRow], *, scope: str, page: int, has_more: bool, lang: str) -> InlineKeyboardMarkup:
    current_scope = _normalize_draft_scope(scope)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"[{_draft_scope_label(lang, item_scope)}]" if item_scope == current_scope else _draft_scope_label(lang, item_scope),
                callback_data=TimePicker.NOOP_CALLBACK if item_scope == current_scope else f"dscope:{item_scope}",
            )
            for item_scope in _DRAFT_SCOPES
        ]
    ]
    for draft in drafts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "btn_draft_open", label=_short_id(draft.id)),
                    callback_data=f"dopen:{current_scope}:{page}:{draft.id}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"dpage:{current_scope}:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"dpage:{current_scope}:{page+1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _draft_detail_kb(*, draft_id: str, permissions: DraftPermissions, scope: str, page: int, lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    primary_actions: list[InlineKeyboardButton] = []
    if permissions.can_edit:
        primary_actions.append(
            InlineKeyboardButton(text=tr(lang, "btn_draft_edit"), callback_data=f"dact:edit:{draft_id}")
        )
    if permissions.can_delete:
        primary_actions.append(
            InlineKeyboardButton(text=tr(lang, "btn_draft_delete"), callback_data=f"ddelask:{scope}:{page}:{draft_id}")
        )
    if primary_actions:
        rows.append(primary_actions)
    if permissions.can_publish:
        rows.append([InlineKeyboardButton(text=tr(lang, "btn_draft_publish"), callback_data=f"dact:publish:{draft_id}")])
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_back"), callback_data=f"dback:{_normalize_draft_scope(scope)}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _draft_delete_confirm_kb(*, draft_id: str, scope: str, page: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "btn_draft_delete"), callback_data=f"ddelyes:{scope}:{page}:{draft_id}")],
            [InlineKeyboardButton(text=tr(lang, "btn_back"), callback_data=f"dopen:{scope}:{page}:{draft_id}")],
        ]
    )


def _draft_delete_command_kb(*, draft_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "btn_draft_delete"), callback_data=f"ddelcmd:{draft_id}")],
            [InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")],
        ]
    )


def _draft_create_scope_kb(teams: list[Team], lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=tr(lang, "draft_location_personal"), callback_data="dcscope:personal")]
    ]
    for team in teams:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "draft_location_team", team_name=team.name)[:60],
                    callback_data=f"dcscope:team:{team.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _schedule_quick_labels(lang: str) -> dict[str, str]:
    return {
        "1h": tr(lang, "schedule_quick_1h"),
        "today_20": tr(lang, "schedule_quick_today_20"),
        "tomorrow_9": tr(lang, "schedule_quick_tomorrow_9"),
        "next_monday": tr(lang, "schedule_quick_next_monday"),
    }


def _schedule_weekday_labels(lang: str) -> tuple[str, ...]:
    return (
        tr(lang, "schedule_weekday_mon"),
        tr(lang, "schedule_weekday_tue"),
        tr(lang, "schedule_weekday_wed"),
        tr(lang, "schedule_weekday_thu"),
        tr(lang, "schedule_weekday_fri"),
        tr(lang, "schedule_weekday_sat"),
        tr(lang, "schedule_weekday_sun"),
    )


def _schedule_calendar_kb(lang: str, *, year: int, month: int, selected: date | None = None) -> InlineKeyboardMarkup:
    picker = TimePicker(weekday_labels=_schedule_weekday_labels(lang))
    calendar_markup = picker.calendar_month(
        year,
        month,
        selected=selected,
        month_label=f"{month:02d}.{year:04d}",
    )
    quick_markup = _TIME_PICKER.quick_buttons(_schedule_quick_labels(lang))
    rows = [list(row) for row in calendar_markup.inline_keyboard]
    rows.extend([list(row) for row in quick_markup.inline_keyboard])
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _schedule_time_kb(lang: str) -> InlineKeyboardMarkup:
    time_markup = _TIME_PICKER.time_selection(minute_values=_SCHEDULE_TIME_MINUTES, per_row=4)
    rows = [list(row) for row in time_markup.inline_keyboard]
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_back"), callback_data="tp:back:calendar")])
    rows.append([InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _repeat_interval_label(lang: str, interval_type: str) -> str:
    labels = {
        "daily": tr(lang, "repeat_interval_daily"),
        "weekly": tr(lang, "repeat_interval_weekly"),
        "weekdays": tr(lang, "repeat_interval_weekdays"),
    }
    return labels.get(interval_type, tr(lang, "repeat_interval_invalid"))


def _repeat_weekdays_mask(interval_type: str) -> int | None:
    if interval_type == "weekdays":
        return _REPEAT_WEEKDAYS_MASK
    return None


def _selected_date_from_state(data: dict[str, object]) -> date | None:
    raw = data.get("selected_date")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _calendar_month_from_state(data: dict[str, object], tz_name: str, *, now_utc: datetime | None = None) -> tuple[int, int]:
    year = data.get("calendar_year")
    month = data.get("calendar_month")
    if isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12:
        return year, month

    current_utc = datetime.now(timezone.utc) if now_utc is None else now_utc.astimezone(timezone.utc)
    local_now = current_utc.astimezone(ZoneInfo(tz_name))
    return local_now.year, local_now.month


def _schedule_datetime_markup(
    lang: str,
    *,
    tz_name: str,
    data: dict[str, object],
    state_name: str | None,
) -> InlineKeyboardMarkup:
    if _is_time_selection_state(state_name) and _selected_date_from_state(data) is not None:
        return _schedule_time_kb(lang)

    selected = _selected_date_from_state(data)
    year, month = _calendar_month_from_state(data, tz_name)
    return _schedule_calendar_kb(lang, year=year, month=month, selected=selected)


def _format_selected_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _parse_calendar_date_token(token: str) -> date:
    return datetime.strptime(token, "%Y%m%d").date()


def _parse_calendar_month_token(token: str) -> tuple[int, int]:
    if len(token) != 6:
        raise ValueError("month token must be YYYYMM")
    year = int(token[:4])
    month = int(token[4:6])
    if not 1 <= month <= 12:
        raise ValueError("month must be in range 1..12")
    return year, month


def _parse_time_token(token: str) -> tuple[int, int]:
    if len(token) != 4:
        raise ValueError("time token must be HHMM")
    hour = int(token[:2])
    minute = int(token[2:4])
    if not 0 <= hour <= 23:
        raise ValueError("hour must be in range 0..23")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be in range 0..59")
    return hour, minute


def _short_id(post_id: str) -> str:
    return post_id[:8]


def _format_local(epoch_utc: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(epoch_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%d.%m.%Y %H:%M")


def _repeat_count_label(pattern: RecurringPattern) -> str:
    if pattern.max_occurrences is None:
        return str(pattern.current_count)
    return f"{pattern.current_count}/{pattern.max_occurrences}"
