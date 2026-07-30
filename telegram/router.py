from __future__ import annotations

import logging
from datetime import date, datetime, timezone
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

from core.rbac import DraftPermissions
from core.state import Destination, DraftRow, RecurringPattern, RecurringPatternSummary, ScheduledPostRow, StateStore, Team
from core.time_picker import TimePicker, resolve_quick_option, resolve_selected_time
from core.timezone_resolver import timezone_from_coordinates
from core.utils import ParsedScheduleTime, parse_local_datetime, validate_schedule_time
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
from telegram.handlers.states import (
    TimezoneStates, LanguageStates, DestinationsStates, ScheduleStates,
    RepeatStates, DraftStates, BroadcastStates, EditStates,
)

logger = logging.getLogger(__name__)


_MENU_SCHEDULE_TEXTS = key_values("menu_schedule")
_MENU_QUEUE_TEXTS = key_values("menu_queue")
_MENU_DESTINATIONS_TEXTS = key_values("menu_destinations")
_MENU_TIMEZONE_TEXTS = key_values("menu_timezone")
_MENU_LANGUAGE_TEXTS = key_values("menu_language")
_TZ_LOCATION_BUTTON_TEXTS = key_values("timezone_location_button")
_TIME_PICKER = TimePicker()
_SCHEDULE_TIME_MINUTES = (0, 30)
_REPEAT_WEEKDAYS_MASK = 0b0011111
_DRAFT_SCOPES = ("all", "mine", "team")


def _is_datetime_entry_state(state_name: str | None) -> bool:
    return state_name in {
        ScheduleStates.entering_datetime.state,
        RepeatStates.entering_datetime.state,
        DraftStates.entering_datetime.state,
        BroadcastStates.entering_datetime.state,
        EditStates.entering_datetime.state,
    }


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


def _normalize_selected_chat_ids(raw_value: object) -> list[int]:
    if not isinstance(raw_value, list):
        return []

    selected: set[int] = set()
    for item in raw_value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            selected.add(item)
            continue
        if isinstance(item, str):
            try:
                selected.add(int(item))
            except ValueError:
                continue
    return sorted(selected)


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


def _resolve_draft_id(drafts: list[DraftRow], draft_ref: str) -> str | None:
    ref = draft_ref.strip().lower()
    if not ref:
        return None

    for draft in drafts:
        if draft.id == ref:
            return draft.id

    matches = [draft.id for draft in drafts if draft.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


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


def _resolve_team_id(teams: list[Team], team_ref: str) -> str | None:
    ref = team_ref.strip().lower()
    if not ref:
        return None

    for team in teams:
        if team.id == ref:
            return team.id

    matches = [team.id for team in teams if team.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


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


async def _clear_inline_markup(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        return


def _short_id(post_id: str) -> str:
    return post_id[:8]


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


def _format_local(epoch_utc: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(epoch_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%d.%m.%Y %H:%M")


def _destination_label(title: str, username: str | None) -> str:
    if username:
        return f"{title} (@{username})"
    return title


def _repeat_count_label(pattern: RecurringPattern) -> str:
    if pattern.max_occurrences is None:
        return str(pattern.current_count)
    return f"{pattern.current_count}/{pattern.max_occurrences}"


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

    async def _render_destinations(
        message: Message,
        page: int,
        *,
        user_id: int,
        select_prefix: str = "sdsel",
        page_prefix: str = "sdpage",
    ) -> None:
        lang = await _user_lang(user_id)
        page_size = 5
        offset = page * page_size
        items = await store.list_user_destinations(user_id=user_id, offset=offset, limit=page_size + 1)
        has_more = len(items) > page_size
        items = items[:page_size]
        if not items:
            await message.answer(
                tr(lang, "no_destinations"),
                reply_markup=await _main_menu_for(user_id),
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

    async def _list_all_user_destinations(user_id: int) -> list[Destination]:
        total = await store.count_user_destinations(user_id)
        if total <= 0:
            return []
        return await store.list_user_destinations(user_id=user_id, offset=0, limit=total)

    async def _render_broadcast_destinations(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        page: int,
        edit: bool,
    ) -> None:
        lang = await _user_lang(user_id)
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
                reply_markup=await _main_menu_for(user_id),
            )
            return

        data = await state.get_data()
        selected_chat_ids = _normalize_selected_chat_ids(data.get("selected_chat_ids"))
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

    async def _resolve_broadcast_destinations(user_id: int, selected_chat_ids: list[int]) -> list[tuple[int, str]]:
        destination_map = {destination.chat_id: destination for destination in await _list_all_user_destinations(user_id)}
        resolved: list[tuple[int, str]] = []
        for chat_id in _normalize_selected_chat_ids(selected_chat_ids):
            destination = destination_map.get(chat_id)
            if destination is None:
                continue
            resolved.append((chat_id, _destination_label(destination.title, destination.username)))
        return resolved

    async def _resolve_broadcast_destination_lines(user_id: int, selected_chat_ids: list[int]) -> tuple[list[int], str]:
        resolved_destinations = await _resolve_broadcast_destinations(user_id, selected_chat_ids)
        valid_chat_ids = [chat_id for chat_id, _ in resolved_destinations]
        labels = [f"- {label}" for _, label in resolved_destinations]
        return valid_chat_ids, "\n".join(labels)

    async def _move_repeat_to_destination_selection(
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
        await _render_destinations(message, page=0, user_id=user_id, select_prefix="rdsel", page_prefix="rdpage")

    async def _move_to_draft_collection(message: Message, state: FSMContext, *, chat_id: int, lang: str) -> None:
        await state.update_data(
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
        await state.set_state(DraftStates.collecting_post)
        await message.answer(tr(lang, "schedule_post_prompt"), reply_markup=_media_collect_kb(lang))

    async def _render_repeats(message: Message, *, user_id: int, page: int, edit: bool) -> None:
        lang = await _user_lang(user_id)
        page_size = 5
        while True:
            offset = page * page_size
            items = await store.list_user_recurring_summaries(user_id=user_id, offset=offset, limit=page_size + 1)
            if items or page == 0:
                break
            page -= 1

        has_more = len(items) > page_size
        items = items[:page_size]
        if not items:
            text = tr(lang, "repeat_list_empty")
            if edit:
                await message.edit_text(text, reply_markup=None)
            else:
                await message.answer(text, reply_markup=await _main_menu_for(user_id))
            return

        display_tz = await store.get_user_timezone(user_id)
        lines: list[str] = []
        for item in items:
            next_tz = display_tz or item.pattern.timezone
            next_run = tr(lang, "repeat_list_next_missing")
            if item.next_scheduled_at_utc is not None:
                next_run = f"{_format_local(item.next_scheduled_at_utc, next_tz)} ({next_tz})"
            lines.append(
                tr(
                    lang,
                    "repeat_list_item",
                    pattern_id=_short_id(item.pattern.id),
                    where=_destination_label(item.destination_title, item.destination_username),
                    interval=_repeat_interval_label(lang, item.pattern.interval_type),
                    next_run=next_run,
                    count=_repeat_count_label(item.pattern),
                )
            )

        text = tr(lang, "repeat_list_header", lines="\n\n".join(lines))
        reply_markup = _repeats_manage_kb(items, page=page, has_more=has_more, lang=lang)
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)

    async def _build_draft_summary(draft: DraftRow, *, lang: str) -> dict[str, str]:
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

    async def _build_scheduled_post_summary(post: ScheduledPostRow, *, lang: str) -> dict[str, str]:
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

    async def _load_pending_post_for_edit(user_id: int, post_id: str) -> tuple[ScheduledPostRow | None, str | None]:
        post = await store.get_scheduled_post(post_id)
        if post is None or post.user_id != user_id:
            return None, "missing"
        if post.status != "pending":
            return None, "unavailable"
        if await store.get_recurring_instance_by_post_id(post_id) is not None:
            return None, "recurring"
        return post, None

    async def _render_edit_posts(message: Message, *, user_id: int, page: int = 0, edit: bool = False) -> None:
        lang = await _user_lang(user_id)
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
                await message.answer(text, reply_markup=await _main_menu_for(user_id))
            return

        lines: list[str] = []
        edit_buttons: list[dict[str, str]] = []
        for post in posts:
            summary = await _build_scheduled_post_summary(post, lang=lang)
            lines.append(
                tr(
                    lang,
                    "edit_list_item",
                    post_id=_short_id(post.id),
                    where=summary["where"],
                    local_time=_format_local(post.scheduled_at_utc, tz_name),
                    kind=summary["kind"],
                    preview=summary["preview"],
                )
            )
            edit_buttons.append({"id": post.id, "label": _short_id(post.id)})

        text = tr(lang, "edit_list_header", lines="\n\n".join(lines))
        reply_markup = _edit_paged_kb(edit_buttons, page=page, has_more=has_more, lang=lang)
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)

    async def _render_delete_posts(message: Message, *, user_id: int, page: int = 0, edit: bool = False) -> None:
        lang = await _user_lang(user_id)
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
                await message.answer(text, reply_markup=await _main_menu_for(user_id))
            return

        lines: list[str] = []
        delete_buttons: list[dict[str, str]] = []
        for post in posts:
            summary = await _build_scheduled_post_summary(post, lang=lang)
            lines.append(
                tr(
                    lang,
                    "delete_list_item",
                    post_id=_short_id(post.id),
                    where=summary["where"],
                    local_time=_format_local(post.scheduled_at_utc, tz_name),
                    kind=summary["kind"],
                    preview=summary["preview"],
                )
            )
            delete_buttons.append({"id": post.id, "label": _short_id(post.id)})

        text = tr(lang, "delete_list_header", lines="\n\n".join(lines))
        reply_markup = _delete_paged_kb(delete_buttons, page=page, has_more=has_more, lang=lang)
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)

    async def _render_queue_page(message: Message, page: int, user_id: int, *, edit: bool = False) -> None:
        lang = await _user_lang(user_id)
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
            if edit:
                await message.edit_text(text, reply_markup=None)
            else:
                await message.answer(text, reply_markup=await _main_menu_for(user_id))
            return

        lines: list[str] = []
        buttons: list[dict[str, str]] = []
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
            buttons.append({"id": p.id, "label": label})

        text = tr(lang, "queue_header", lines="\n".join(lines))
        reply_markup = _queue_paged_kb(buttons, page=page, has_more=has_more, lang=lang)
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)

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
        message: Message, *, user_id: int, post_id: str, state: FSMContext | None = None
    ) -> None:
        lang = await _user_lang(user_id)
        post = await store.get_scheduled_post(post_id)
        if post is None or post.user_id != user_id:
            await message.answer(tr(lang, "view_not_found"), reply_markup=await _main_menu_for(user_id))
            return

        # Replace any previous live preview instead of stacking a new one.
        await _clear_live_preview(message.bot, state)

        sent_ids: list[int] = []

        tz_name = await store.get_user_timezone(user_id) or "UTC"
        summary = await _build_scheduled_post_summary(post, lang=lang)
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

    async def _start_scheduled_post_edit(message: Message, state: FSMContext, *, user_id: int, post: ScheduledPostRow) -> None:
        lang = await _user_lang(user_id)
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        summary = await _build_scheduled_post_summary(post, lang=lang)
        await state.clear()
        await state.update_data(
            edit_post_id=post.id,
            chat_id=post.chat_id,
        )
        await state.set_state(EditStates.choosing_field)
        await message.answer(
            tr(
                lang,
                "edit_choose_field",
                post_id=_short_id(post.id),
                where=summary["where"],
                local_time=_format_local(post.scheduled_at_utc, tz_name),
                tz_name=tz_name,
                kind=summary["kind"],
                preview=summary["preview"],
            ),
            reply_markup=_edit_field_kb(post_id=post.id, lang=lang),
        )

    async def _start_scheduled_post_text_edit(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        post: ScheduledPostRow,
    ) -> None:
        lang = await _user_lang(user_id)
        summary = await _build_scheduled_post_summary(post, lang=lang)
        await state.clear()
        await state.update_data(
            edit_post_id=post.id,
            chat_id=post.chat_id,
        )
        await state.set_state(EditStates.entering_text)
        await message.answer(
            tr(
                lang,
                "edit_text_prompt",
                post_id=_short_id(post.id),
                kind=summary["kind"],
                preview=summary["preview"],
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "btn_cancel"), callback_data="scancel")]]
            ),
        )

    async def _start_scheduled_post_time_edit(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        post: ScheduledPostRow,
    ) -> None:
        lang = await _user_lang(user_id)
        tz_name = await store.get_user_timezone(user_id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(user_id))
            return

        summary = await _build_scheduled_post_summary(post, lang=lang)
        local_dt = datetime.fromtimestamp(post.scheduled_at_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
        await state.clear()
        await state.update_data(
            edit_post_id=post.id,
            chat_id=post.chat_id,
            selected_date=local_dt.date().isoformat(),
            calendar_year=local_dt.year,
            calendar_month=local_dt.month,
        )
        await state.set_state(EditStates.entering_datetime)
        await _prompt_for_datetime(
            message,
            lang=lang,
            tz_name=tz_name,
            text=tr(
                lang,
                "edit_time_prompt",
                post_id=_short_id(post.id),
                where=summary["where"],
                local_time=_format_local(post.scheduled_at_utc, tz_name),
                tz_name=tz_name,
            ),
            data=await state.get_data(),
            state_name=EditStates.entering_datetime.state,
        )

    async def _start_scheduled_post_media_edit(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        post: ScheduledPostRow,
    ) -> None:
        lang = await _user_lang(user_id)
        summary = await _build_scheduled_post_summary(post, lang=lang)
        existing_text = post.caption if post.kind == "media" else post.text
        existing_entities = post.caption_entities_json if post.kind == "media" else post.entities_json
        existing_caption_above = None if post.caption_above is None else bool(post.caption_above)
        await state.clear()
        await state.update_data(
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
        await state.set_state(EditStates.collecting_media)
        await message.answer(
            tr(
                lang,
                "edit_media_prompt",
                post_id=_short_id(post.id),
                preview=summary["preview"],
            ),
            reply_markup=_media_collect_kb(lang),
        )

    async def _send_edit_unavailable(message: Message, *, user_id: int, reason: str) -> None:
        lang = await _user_lang(user_id)
        key = "edit_post_recurring_blocked" if reason == "recurring" else "edit_post_missing"
        await message.answer(tr(lang, key), reply_markup=await _main_menu_for(user_id))

    async def _send_delete_unavailable(message: Message, *, user_id: int, reason: str) -> None:
        lang = await _user_lang(user_id)
        key = "delete_post_recurring_blocked" if reason == "recurring" else "delete_post_missing"
        await message.answer(tr(lang, key), reply_markup=await _main_menu_for(user_id))

    async def _render_delete_confirm(message: Message, *, user_id: int, post: ScheduledPostRow) -> None:
        lang = await _user_lang(user_id)
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        summary = await _build_scheduled_post_summary(post, lang=lang)
        await message.answer(
            tr(
                lang,
                "delete_confirm",
                post_id=_short_id(post.id),
                where=summary["where"],
                local_time=_format_local(post.scheduled_at_utc, tz_name),
                tz_name=tz_name,
                kind=summary["kind"],
                preview=summary["preview"],
            ),
            reply_markup=_delete_confirm_kb(post_id=post.id, lang=lang),
        )

    async def _confirm_delete_post(message: Message, *, user_id: int, post_id: str) -> bool:
        deleted = await store.hard_delete_post(user_id=user_id, post_id=post_id)
        if not deleted:
            _, reason = await _load_pending_post_for_edit(user_id, post_id)
            await _send_delete_unavailable(message, user_id=user_id, reason=str(reason or "missing"))
            return False

        lang = await _user_lang(user_id)
        await message.answer(
            tr(lang, "delete_post_ok", post_id=_short_id(post_id)),
            reply_markup=await _main_menu_for(user_id),
        )
        return True

    async def _save_scheduled_post_time(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        scheduled_at_utc: int,
    ) -> bool:
        data = await state.get_data()
        post_id = data.get("edit_post_id")
        if not isinstance(post_id, str):
            return False

        updated = await store.update_scheduled_post(
            post_id,
            user_id,
            {"scheduled_at_utc": scheduled_at_utc},
        )
        if not updated:
            await state.clear()
            _, reason = await _load_pending_post_for_edit(user_id, post_id)
            await _send_edit_unavailable(message, user_id=user_id, reason=str(reason or "missing"))
            return False

        lang = await _user_lang(user_id)
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
            reply_markup=await _main_menu_for(user_id),
        )
        return True

    async def _save_scheduled_post_text(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        text: str,
        entities_json: str | None,
    ) -> bool:
        data = await state.get_data()
        post_id = data.get("edit_post_id")
        if not isinstance(post_id, str):
            return False

        post, reason = await _load_pending_post_for_edit(user_id, post_id)
        if post is None:
            await state.clear()
            await _send_edit_unavailable(message, user_id=user_id, reason=str(reason or "missing"))
            return False

        lang = await _user_lang(user_id)
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
            await _send_edit_unavailable(message, user_id=user_id, reason="missing")
            return False

        await state.clear()
        await message.answer(
            tr(lang, "edit_text_updated_ok", post_id=_short_id(post_id)),
            reply_markup=await _main_menu_for(user_id),
        )
        return True

    async def _save_scheduled_post_media(message: Message, state: FSMContext, *, user_id: int) -> bool:
        data = await state.get_data()
        post_id = data.get("edit_post_id")
        if not isinstance(post_id, str):
            return False

        media_items = list(data.get("media_items", []))
        if not media_items:
            return False

        draft_text = data.get("draft_text")
        draft_text_valid = bool(str(draft_text).strip()) if draft_text is not None else False
        updated = await store.update_scheduled_post(
            post_id,
            user_id,
            {
                "kind": "media",
                "caption": draft_text if draft_text_valid else None,
                "caption_entities_json": data.get("draft_entities_json") if draft_text_valid else None,
                "caption_above": bool(data.get("caption_above", False)) if draft_text_valid else None,
                "media_items": media_items,
            },
        )
        if not updated:
            await state.clear()
            await _send_edit_unavailable(message, user_id=user_id, reason="missing")
            return False

        lang = await _user_lang(user_id)
        await state.clear()
        await message.answer(
            tr(
                lang,
                "edit_media_updated_ok",
                post_id=_short_id(post_id),
                kind=tr(lang, "kind_media", count=len(media_items)),
            ),
            reply_markup=await _main_menu_for(user_id),
        )
        return True

    async def _render_drafts(message: Message, *, user_id: int, scope: str, page: int, edit: bool) -> None:
        lang = await _user_lang(user_id)
        current_scope = _normalize_draft_scope(scope)
        page_size = 5
        while True:
            offset = page * page_size
            items = await store.list_drafts(user_id=user_id, scope=current_scope, offset=offset, limit=page_size + 1)
            if items or page == 0:
                break
            page -= 1

        has_more = len(items) > page_size
        items = items[:page_size]
        reply_markup = _drafts_manage_kb(items, scope=current_scope, page=page, has_more=has_more, lang=lang)

        if not items:
            text = tr(lang, "draft_list_empty", scope=_draft_scope_label(lang, current_scope))
            if edit:
                await message.edit_text(text, reply_markup=reply_markup)
            else:
                await message.answer(text, reply_markup=reply_markup)
            return

        lines: list[str] = []
        for draft in items:
            summary = await _build_draft_summary(draft, lang=lang)
            lines.append(
                tr(
                    lang,
                    "draft_list_item",
                    draft_id=_short_id(draft.id),
                    location=summary["location"],
                    where=summary["where"],
                    kind=summary["kind"],
                    preview=summary["preview"],
                )
            )

        text = tr(lang, "draft_list_header", scope=_draft_scope_label(lang, current_scope), lines="\n\n".join(lines))
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)

    async def _render_draft_detail(
        message: Message,
        *,
        user_id: int,
        scope: str,
        page: int,
        draft: DraftRow,
        permissions: DraftPermissions,
        edit: bool,
    ) -> None:
        lang = await _user_lang(user_id)
        tz_name = await store.get_user_timezone(user_id) or "UTC"
        summary = await _build_draft_summary(draft, lang=lang)
        text = tr(
            lang,
            "draft_detail_header",
            draft_id=_short_id(draft.id),
            location=summary["location"],
            where=summary["where"],
            kind=summary["kind"],
            updated_at=_format_local(draft.updated_at, tz_name),
            preview=summary["preview"],
            actions=_draft_action_labels(lang, permissions),
        )
        reply_markup = _draft_detail_kb(
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
        message: Message,
        *,
        user_id: int,
        draft: DraftRow,
        scope: str | None,
        page: int | None,
        edit: bool,
    ) -> None:
        lang = await _user_lang(user_id)
        summary = await _build_draft_summary(draft, lang=lang)
        text = tr(
            lang,
            "draft_delete_confirm",
            draft_id=_short_id(draft.id),
            location=summary["location"],
            where=summary["where"],
            kind=summary["kind"],
        )
        if scope is None or page is None:
            reply_markup = _draft_delete_command_kb(draft_id=draft.id, lang=lang)
        else:
            reply_markup = _draft_delete_confirm_kb(
                draft_id=draft.id,
                scope=_normalize_draft_scope(scope),
                page=page,
                lang=lang,
            )
        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)

    async def _save_draft_from_state(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        team_id: str | None,
    ) -> bool:
        data = await state.get_data()
        chat_id = data.get("chat_id")
        kind = data.get("kind")
        lang = await _user_lang(user_id)
        if not isinstance(chat_id, int) or kind not in {"text", "media"}:
            return False

        try:
            if kind == "text":
                draft_id = await store.create_draft(
                    author_user_id=user_id,
                    team_id=team_id,
                    chat_id=chat_id,
                    kind="text",
                    text=data.get("text"),
                    entities_json=data.get("entities_json"),
                )
                kind_label = tr(lang, "kind_text")
            else:
                media_items: list[dict[str, str]] = list(data.get("media_items", []))
                draft_id = await store.create_draft(
                    author_user_id=user_id,
                    team_id=team_id,
                    chat_id=chat_id,
                    kind="media",
                    caption=data.get("caption"),
                    caption_entities_json=data.get("caption_entities_json"),
                    caption_above=bool(data.get("caption_above", False)),
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
            reply_markup=await _main_menu_for(user_id),
        )
        return True

    async def _prompt_draft_scope(message: Message, state: FSMContext, *, user_id: int) -> None:
        lang = await _user_lang(user_id)
        writable_teams = await store.list_writable_teams(user_id)
        if not writable_teams:
            await _save_draft_from_state(message, state, user_id=user_id, team_id=None)
            return

        await state.set_state(DraftStates.choosing_scope)
        await message.answer(
            tr(lang, "draft_create_scope_prompt"),
            reply_markup=_draft_create_scope_kb(writable_teams, lang),
        )

    async def _start_draft_edit(message: Message, state: FSMContext, *, user_id: int, draft: DraftRow) -> None:
        lang = await _user_lang(user_id)
        summary = await _build_draft_summary(draft, lang=lang)
        await state.clear()
        await state.update_data(
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
        await state.set_state(DraftStates.editing_post)
        await message.answer(
            tr(
                lang,
                "draft_edit_prompt",
                draft_id=_short_id(draft.id),
                location=summary["location"],
                where=summary["where"],
                kind=summary["kind"],
            ),
            reply_markup=_media_collect_kb(lang),
        )

    async def _update_draft_from_state(message: Message, state: FSMContext, *, user_id: int) -> bool:
        data = await state.get_data()
        draft_id = data.get("edit_draft_id")
        chat_id = data.get("chat_id")
        team_id = data.get("team_id")
        kind = data.get("kind")
        lang = await _user_lang(user_id)
        if not isinstance(draft_id, str) or not isinstance(chat_id, int) or kind not in {"text", "media"}:
            return False

        try:
            if kind == "text":
                updated = await store.update_draft(
                    draft_id,
                    user_id,
                    chat_id=chat_id,
                    kind="text",
                    text=data.get("text"),
                    entities_json=data.get("entities_json"),
                )
                kind_label = tr(lang, "kind_text")
            else:
                media_items: list[dict[str, str]] = list(data.get("media_items", []))
                updated = await store.update_draft(
                    draft_id,
                    user_id,
                    chat_id=chat_id,
                    kind="media",
                    caption=data.get("caption"),
                    caption_entities_json=data.get("caption_entities_json"),
                    caption_above=bool(data.get("caption_above", False)),
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
            reply_markup=await _main_menu_for(user_id),
        )
        return True

    async def _start_draft_publish(message: Message, state: FSMContext, *, user_id: int, draft: DraftRow) -> None:
        lang = await _user_lang(user_id)
        tz_name = await store.get_user_timezone(user_id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(user_id))
            return

        where = await store.get_destination_title(draft.chat_id) or str(draft.chat_id)
        await state.clear()
        await state.update_data(
            draft_publish_id=draft.id,
            chat_id=draft.chat_id,
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
        )
        await state.set_state(DraftStates.entering_datetime)
        await _prompt_for_datetime(
            message,
            lang=lang,
            tz_name=tz_name,
            text=_draft_post_prompt_text(lang, draft_id=draft.id, where=where),
            data=await state.get_data(),
            state_name=DraftStates.entering_datetime.state,
        )

    async def _move_draft_publish_to_confirmation(
        message: Message,
        state: FSMContext,
        *,
        user_id: int,
        scheduled_at_utc: int,
        scheduled_local: str,
    ) -> None:
        lang = await _user_lang(user_id)
        draft_id = (await state.get_data()).get("draft_publish_id")
        if not isinstance(draft_id, str):
            await state.clear()
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(user_id))
            return

        permissions = await store.get_draft_permissions(draft_id, user_id)
        draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_publish else None
        if draft is None or permissions is None or not permissions.can_publish:
            await state.clear()
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(user_id))
            return

        await state.update_data(
            scheduled_at_utc=scheduled_at_utc,
            scheduled_local=scheduled_local,
            chat_id=draft.chat_id,
        )
        await state.set_state(DraftStates.confirming)

        tz_name = await store.get_user_timezone(user_id) or "UTC"
        local_time = _format_local(scheduled_at_utc, tz_name)
        summary = await _build_draft_summary(draft, lang=lang)
        text = tr(lang, "confirm_template", where=summary["where"], local_time=local_time, tz_name=tz_name, kind=summary["kind"])
        await message.answer(text, reply_markup=_confirm_kb(lang))

    async def _handle_team_invite_start(message: Message, state: FSMContext, *, user_id: int, token: str) -> None:
        lang = await _user_lang(user_id)
        await state.clear()
        result = await store.accept_team_invite(token, user_id)
        team = result.team
        role = result.role

        if result.status == "accepted" and team is not None and role is not None:
            await message.answer(
                tr(
                    lang,
                    "team_invite_accept_ok",
                    team_id=_short_id(team.id),
                    team_name=team.name,
                    role=_team_role_label(lang, role),
                ),
                reply_markup=await _main_menu_for(user_id),
            )
            return

        if result.status == "already_member" and team is not None and role is not None:
            await message.answer(
                tr(
                    lang,
                    "team_invite_already_member",
                    team_id=_short_id(team.id),
                    team_name=team.name,
                    role=_team_role_label(lang, role),
                ),
                reply_markup=await _main_menu_for(user_id),
            )
            return

        key = {
            "expired": "team_invite_expired",
            "used": "team_invite_used",
        }.get(result.status, "team_invite_missing")
        await message.answer(tr(lang, key), reply_markup=await _main_menu_for(user_id))

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await store.ensure_user(
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        parts = (message.text or "").split(maxsplit=1)
        start_arg = parts[1].strip() if len(parts) == 2 else ""
        if start_arg.startswith("ti_") and len(start_arg) > 3:
            await _handle_team_invite_start(message, state, user_id=message.from_user.id, token=start_arg[3:])
            return

        lang = await _user_lang(message.from_user.id)
        await state.clear()
        await message.answer(
            tr(lang, "start_message"),
            reply_markup=_main_menu_kb(lang),
        )

    @router.message(Command("team_create"))
    async def cmd_team_create(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "team_create_usage"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        team_name = parts[1].strip()
        team_id = await store.create_team(message.from_user.id, team_name)
        await message.answer(
            tr(
                lang,
                "team_create_ok",
                team_id=_short_id(team_id),
                team_name=team_name,
                role=_team_role_label(lang, "owner"),
            ),
            reply_markup=await _main_menu_for(message.from_user.id),
        )

    @router.message(Command("team_invite"))
    async def cmd_team_invite(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(tr(lang, "team_invite_usage"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        team_ref = parts[1].strip().lower()
        role = parts[2].strip().lower() if len(parts) == 3 and parts[2].strip() else "viewer"
        if role not in {"viewer", "editor"}:
            await message.answer(tr(lang, "team_invite_role_invalid"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        owned_teams = await store.list_owned_teams(message.from_user.id, limit=200)
        team_id = _resolve_team_id(owned_teams, team_ref)
        if team_id is None:
            await message.answer(tr(lang, "team_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        try:
            invite = await store.create_team_invite(team_id, message.from_user.id, role)
        except ValueError:
            await message.answer(tr(lang, "team_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        team = next((item for item in owned_teams if item.id == team_id), None)
        if team is None:
            await message.answer(tr(lang, "team_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        bot_user = await message.bot.me()
        start_payload = f"ti_{invite.token}"
        bot_username = getattr(bot_user, "username", None)
        invite_link = f"https://t.me/{bot_username}?start={start_payload}" if bot_username else f"/start {start_payload}"
        tz_name = await store.get_user_timezone(message.from_user.id) or "UTC"
        await message.answer(
            tr(
                lang,
                "team_invite_created",
                team_id=_short_id(team.id),
                team_name=team.name,
                role=_team_role_label(lang, role),
                expires_at=_format_local(invite.expires_at, tz_name),
                tz_name=tz_name,
                link=invite_link,
            ),
            reply_markup=await _main_menu_for(message.from_user.id),
        )

    @router.message(Command("team_members"))
    async def cmd_team_members(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        await state.clear()
        teams = await store.list_user_teams(message.from_user.id, limit=200)
        if not teams:
            await message.answer(tr(lang, "team_members_none"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            team_id = _resolve_team_id(teams, parts[1].strip().lower())
            if team_id is None:
                await message.answer(tr(lang, "team_missing"), reply_markup=await _main_menu_for(message.from_user.id))
                return
        elif len(teams) == 1:
            team_id = teams[0].id
        else:
            lines: list[str] = []
            for team in teams:
                role = await store.get_team_member_role(team.id, message.from_user.id)
                if role is None:
                    continue
                lines.append(
                    tr(
                        lang,
                        "team_members_choose_item",
                        team_id=_short_id(team.id),
                        team_name=team.name,
                        role=_team_role_label(lang, role),
                    )
                )
            await message.answer(
                tr(lang, "team_members_choose", lines="\n".join(lines)),
                reply_markup=await _main_menu_for(message.from_user.id),
            )
            return

        team = next((item for item in teams if item.id == team_id), None)
        role = await store.get_team_member_role(team_id, message.from_user.id)
        if team is None or role is None:
            await message.answer(tr(lang, "team_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        members = await store.list_team_members(team_id)
        lines = "\n".join(
            tr(
                lang,
                "team_members_item",
                role=_team_role_label(lang, member.role),
                user_id=member.user_id,
            )
            for member in members
        )
        await message.answer(
            tr(
                lang,
                "team_members_header",
                team_id=_short_id(team.id),
                team_name=team.name,
                role=_team_role_label(lang, role),
                lines=lines,
            ),
            reply_markup=await _main_menu_for(message.from_user.id),
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
        await _render_destinations(message, page=0, user_id=message.from_user.id)

    @router.message(Command("broadcast"))
    async def cmd_broadcast(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await state.clear()
        await state.set_state(BroadcastStates.choosing_destinations)
        await state.update_data(selected_chat_ids=[], dest_page=0)
        await _render_broadcast_destinations(message, state, user_id=message.from_user.id, page=0, edit=False)

    @router.message(Command("repeat"))
    async def cmd_repeat(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await state.clear()
        await state.set_state(RepeatStates.choosing_interval)
        await message.answer(tr(lang, "repeat_choose_interval"), reply_markup=_repeat_interval_kb(lang))

    @router.message(Command("repeats"))
    async def cmd_repeats(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        await _render_repeats(message, user_id=message.from_user.id, page=0, edit=False)

    @router.message(Command("drafts"))
    async def cmd_drafts(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        await _render_drafts(message, user_id=message.from_user.id, scope="all", page=0, edit=False)

    @router.message(Command("draft_create"))
    async def cmd_draft_create(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        await state.set_state(DraftStates.choosing_destination)
        await state.update_data(dest_page=0)
        await _render_destinations(message, page=0, user_id=message.from_user.id, select_prefix="ddsel", page_prefix="ddpage")

    @router.message(Command("draft_edit"))
    async def cmd_draft_edit(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_drafts(message, user_id=message.from_user.id, scope="all", page=0, edit=False)
            return

        draft_ref = parts[1].strip().lower()
        drafts = await store.list_drafts(message.from_user.id, scope="all", limit=200)
        draft_id = _resolve_draft_id(drafts, draft_ref)
        if draft_id is None:
            lang = await _user_lang(message.from_user.id)
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        draft = await store.get_draft(draft_id)
        permissions = await store.get_draft_permissions(draft_id, message.from_user.id)
        if draft is None or permissions is None or not permissions.can_edit:
            lang = await _user_lang(message.from_user.id)
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await _start_draft_edit(message, state, user_id=message.from_user.id, draft=draft)

    @router.message(Command("draft_delete"))
    async def cmd_draft_delete(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        lang = await _user_lang(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "draft_delete_usage"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        draft_ref = parts[1].strip().lower()
        drafts = await store.list_drafts(message.from_user.id, scope="all", limit=200)
        draft_id = _resolve_draft_id(drafts, draft_ref)
        if draft_id is None:
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        draft = await store.get_draft(draft_id)
        permissions = await store.get_draft_permissions(draft_id, message.from_user.id)
        if draft is None or permissions is None or not permissions.can_delete:
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await _render_draft_delete_confirm(
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
            await _render_drafts(message, user_id=message.from_user.id, scope="all", page=0, edit=False)
            return

        draft_ref = parts[1].strip().lower()
        drafts = await store.list_drafts(message.from_user.id, scope="all", limit=200)
        draft_id = _resolve_draft_id(drafts, draft_ref)
        if draft_id is None:
            lang = await _user_lang(message.from_user.id)
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        draft = await store.get_draft(draft_id)
        permissions = await store.get_draft_permissions(draft_id, message.from_user.id)
        if draft is None or permissions is None or not permissions.can_publish:
            lang = await _user_lang(message.from_user.id)
            await message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await _start_draft_publish(message, state, user_id=message.from_user.id, draft=draft)

    @router.message(Command("repeat_cancel"))
    async def cmd_repeat_cancel(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "repeat_cancel_usage"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        pattern_ref = parts[1].strip().lower()
        patterns = await store.list_user_recurring(message.from_user.id, include_inactive=True)
        pattern_id = _resolve_recurring_pattern_id(patterns, pattern_ref)
        if pattern_id is None:
            await message.answer(tr(lang, "repeat_cancel_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        ok = await store.cancel_recurring_pattern(user_id=message.from_user.id, pattern_id=pattern_id)
        if not ok:
            await message.answer(tr(lang, "repeat_cancel_missing"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await message.answer(
            tr(lang, "repeat_cancel_ok", pattern_id=_short_id(pattern_id)),
            reply_markup=await _main_menu_for(message.from_user.id),
        )

    @router.message(Command("edit"))
    async def cmd_edit(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_edit_posts(message, user_id=message.from_user.id)
            return

        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=200)
        post_id, ambiguous = _resolve_scheduled_post_id(posts, parts[1].strip().lower())
        lang = await _user_lang(message.from_user.id)
        if post_id is None:
            key = "edit_post_ambiguous" if ambiguous else "edit_post_missing"
            await message.answer(tr(lang, key), reply_markup=await _main_menu_for(message.from_user.id))
            return

        post, reason = await _load_pending_post_for_edit(message.from_user.id, post_id)
        if post is None:
            await _send_edit_unavailable(message, user_id=message.from_user.id, reason=str(reason or "missing"))
            return

        await _start_scheduled_post_edit(message, state, user_id=message.from_user.id, post=post)

    @router.message(Command("delete"))
    async def cmd_delete(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await _render_delete_posts(message, user_id=message.from_user.id)
            return

        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=200)
        post_id, ambiguous = _resolve_scheduled_post_id(posts, parts[1].strip().lower())
        lang = await _user_lang(message.from_user.id)
        if post_id is None:
            key = "delete_post_ambiguous" if ambiguous else "delete_post_missing"
            await message.answer(tr(lang, key), reply_markup=await _main_menu_for(message.from_user.id))
            return

        post, reason = await _load_pending_post_for_edit(message.from_user.id, post_id)
        if post is None:
            await _send_delete_unavailable(message, user_id=message.from_user.id, reason=str(reason or "missing"))
            return

        await _render_delete_confirm(message, user_id=message.from_user.id, post=post)

    @router.message(Command("view"))
    async def cmd_view(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        lang = await _user_lang(message.from_user.id)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "view_not_found"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        posts = await store.list_pending_posts(user_id=message.from_user.id, limit=200)
        post_id, _ = _resolve_scheduled_post_id(posts, parts[1].strip().lower())
        if post_id is None:
            await message.answer(tr(lang, "view_not_found"), reply_markup=await _main_menu_for(message.from_user.id))
            return

        await _send_post_preview(message, user_id=message.from_user.id, post_id=post_id, state=state)

    @router.callback_query(F.data.startswith("rlpage:"))
    async def cb_repeats_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_repeats(query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("epage:"))
    async def cb_edit_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_edit_posts(query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("delpage:"))
    async def cb_delete_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_delete_posts(query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("qedit:"))
    async def cb_queue_edit(query: CallbackQuery, state: FSMContext) -> None:
        post_id = query.data.split(":", 1)[1]
        post, reason = await _load_pending_post_for_edit(query.from_user.id, post_id)
        lang = await _user_lang(query.from_user.id)
        if post is None:
            key = "edit_post_recurring_blocked" if reason == "recurring" else "edit_post_missing"
            await query.answer(tr(lang, key), show_alert=True)
            return

        await query.answer()
        await _start_scheduled_post_edit(query.message, state, user_id=query.from_user.id, post=post)

    @router.callback_query(F.data.startswith("qdelask:"))
    async def cb_queue_delete_prompt(query: CallbackQuery) -> None:
        post_id = query.data.split(":", 1)[1]
        post, reason = await _load_pending_post_for_edit(query.from_user.id, post_id)
        lang = await _user_lang(query.from_user.id)
        if post is None:
            key = "delete_post_recurring_blocked" if reason == "recurring" else "delete_post_missing"
            await query.answer(tr(lang, key), show_alert=True)
            return

        await query.answer()
        await _render_delete_confirm(query.message, user_id=query.from_user.id, post=post)

    @router.callback_query(F.data.startswith("qdelyes:"))
    async def cb_queue_delete_confirm(query: CallbackQuery) -> None:
        post_id = query.data.split(":", 1)[1]
        await query.answer()
        await _clear_inline_markup(query.message)
        await _confirm_delete_post(query.message, user_id=query.from_user.id, post_id=post_id)

    @router.callback_query(F.data.startswith("eact:"))
    async def cb_edit_action(query: CallbackQuery, state: FSMContext) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        action = parts[1]
        post_id = parts[2]
        post, reason = await _load_pending_post_for_edit(query.from_user.id, post_id)
        lang = await _user_lang(query.from_user.id)
        if post is None:
            key = "edit_post_recurring_blocked" if reason == "recurring" else "edit_post_missing"
            await query.answer(tr(lang, key), show_alert=True)
            return

        await query.answer()
        if action == "text":
            await _start_scheduled_post_text_edit(query.message, state, user_id=query.from_user.id, post=post)
            return
        if action == "time":
            await _start_scheduled_post_time_edit(query.message, state, user_id=query.from_user.id, post=post)
            return
        if action == "media":
            await _start_scheduled_post_media_edit(query.message, state, user_id=query.from_user.id, post=post)
            return

        await query.message.answer(tr(lang, "edit_post_missing"), reply_markup=await _main_menu_for(query.from_user.id))

    @router.callback_query(F.data.startswith("rstop:"))
    async def cb_repeats_stop(query: CallbackQuery) -> None:
        lang = await _user_lang(query.from_user.id)
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        page = int(parts[1])
        pattern_id = parts[2]
        ok = await store.cancel_recurring_pattern(user_id=query.from_user.id, pattern_id=pattern_id)
        await query.answer(
            tr(lang, "repeat_cancel_ok", pattern_id=_short_id(pattern_id)) if ok else tr(lang, "repeat_cancel_missing")
        )
        await _render_repeats(query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("dscope:"))
    async def cb_drafts_scope(query: CallbackQuery) -> None:
        scope = _normalize_draft_scope(query.data.split(":", 1)[1])
        await query.answer()
        await _render_drafts(query.message, user_id=query.from_user.id, scope=scope, page=0, edit=True)

    @router.callback_query(F.data.startswith("dpage:"))
    async def cb_drafts_page(query: CallbackQuery) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        scope = _normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        await query.answer()
        await _render_drafts(query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)

    @router.callback_query(F.data.startswith("dback:"))
    async def cb_draft_back(query: CallbackQuery) -> None:
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        scope = _normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        await query.answer()
        await _render_drafts(query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)

    @router.callback_query(F.data.startswith("dopen:"))
    async def cb_draft_open(query: CallbackQuery) -> None:
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            await query.answer()
            return

        scope = _normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        draft_id = parts[3]
        lang = await _user_lang(query.from_user.id)
        permissions = await store.get_draft_permissions(draft_id, query.from_user.id)
        draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_view else None
        if draft is None or permissions is None or not permissions.can_view:
            await query.answer(tr(lang, "draft_missing"), show_alert=True)
            if (query.message.text or "").startswith("draft="):
                await _render_drafts(query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)
            return

        await query.answer()
        await _render_draft_detail(
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

        scope = _normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        draft_id = parts[3]
        lang = await _user_lang(query.from_user.id)
        permissions = await store.get_draft_permissions(draft_id, query.from_user.id)
        draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_delete else None
        if draft is None or permissions is None or not permissions.can_delete:
            await query.answer(tr(lang, "draft_missing"), show_alert=True)
            await _render_drafts(query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)
            return

        await query.answer()
        await _render_draft_delete_confirm(
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

        scope = _normalize_draft_scope(parts[1])
        try:
            page = int(parts[2])
        except ValueError:
            await query.answer()
            return

        draft_id = parts[3]
        lang = await _user_lang(query.from_user.id)
        ok = await store.delete_draft(draft_id, query.from_user.id)
        await query.answer(
            tr(lang, "draft_delete_ok", draft_id=_short_id(draft_id)) if ok else tr(lang, "draft_missing"),
            show_alert=not ok,
        )
        await _render_drafts(query.message, user_id=query.from_user.id, scope=scope, page=page, edit=True)

    @router.callback_query(F.data.startswith("ddelcmd:"))
    async def cb_draft_delete_command_confirm(query: CallbackQuery) -> None:
        draft_id = query.data.split(":", 1)[1]
        lang = await _user_lang(query.from_user.id)
        ok = await store.delete_draft(draft_id, query.from_user.id)
        await query.answer(
            tr(lang, "draft_delete_ok", draft_id=_short_id(draft_id)) if ok else tr(lang, "draft_missing"),
            show_alert=not ok,
        )
        await query.message.edit_text(
            tr(lang, "draft_delete_ok", draft_id=_short_id(draft_id)) if ok else tr(lang, "draft_missing"),
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
        permissions = await store.get_draft_permissions(draft_id, query.from_user.id)
        lang = await _user_lang(query.from_user.id)
        if action == "edit":
            draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_edit else None
            if draft is None or permissions is None or not permissions.can_edit:
                await query.answer(tr(lang, "draft_missing"), show_alert=True)
                return

            await query.answer()
            await _start_draft_edit(query.message, state, user_id=query.from_user.id, draft=draft)
            return

        if action == "publish":
            draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_publish else None
            if draft is None or permissions is None or not permissions.can_publish:
                await query.answer(tr(lang, "draft_missing"), show_alert=True)
                return

            await query.answer()
            await _start_draft_publish(query.message, state, user_id=query.from_user.id, draft=draft)
            return

        allowed = False
        if permissions is not None:
            allowed = action == "delete" and permissions.can_delete
        await query.answer(
            tr(lang, "draft_action_unavailable") if allowed else tr(lang, "draft_missing"),
            show_alert=True,
        )

    @router.callback_query(F.data.startswith("ddpage:"))
    async def cb_draft_create_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != DraftStates.choosing_destination.state:
            await query.answer()
            return

        page = int(query.data.split(":")[1])
        await state.update_data(dest_page=page)
        await query.answer()
        await _render_destinations(
            query.message,
            page=page,
            user_id=query.from_user.id,
            select_prefix="ddsel",
            page_prefix="ddpage",
        )

    @router.callback_query(F.data.startswith("ddsel:"))
    async def cb_draft_create_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != DraftStates.choosing_destination.state:
            await query.answer()
            return

        chat_id = int(query.data.split(":")[1])
        lang = await _user_lang(query.from_user.id)
        await state.update_data(chat_id=chat_id)
        await query.answer()
        await _move_to_draft_collection(query.message, state, chat_id=chat_id, lang=lang)

    @router.callback_query(F.data.startswith("dcscope:"))
    async def cb_draft_create_scope(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != DraftStates.choosing_scope.state:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
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
        if not await _save_draft_from_state(query.message, state, user_id=query.from_user.id, team_id=team_id):
            await query.message.answer(
                tr(lang, "draft_create_scope_prompt"),
                reply_markup=_draft_create_scope_kb(await store.list_writable_teams(query.from_user.id), lang),
            )
            return

    @router.callback_query(F.data.startswith("bcpage:"))
    async def cb_broadcast_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != BroadcastStates.choosing_destinations.state:
            await query.answer()
            return

        try:
            page = int(query.data.split(":", 1)[1])
        except ValueError:
            await query.answer()
            return

        await query.answer()
        await _render_broadcast_destinations(
            query.message,
            state,
            user_id=query.from_user.id,
            page=page,
            edit=True,
        )

    @router.callback_query(F.data.startswith("bc:"))
    async def cb_broadcast_dest_toggle(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != BroadcastStates.choosing_destinations.state:
            await query.answer()
            return

        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        try:
            chat_id = int(parts[1])
        except ValueError:
            await query.answer()
            return

        enabled_token = parts[2]
        if enabled_token not in {"on", "off"}:
            await query.answer()
            return

        all_destinations = await _list_all_user_destinations(query.from_user.id)
        if chat_id not in {destination.chat_id for destination in all_destinations}:
            await query.answer(tr(await _user_lang(query.from_user.id), "broadcast_destination_missing"), show_alert=True)
            return

        data = await state.get_data()
        selected_chat_ids = _normalize_selected_chat_ids(data.get("selected_chat_ids"))
        next_selected_chat_ids = _toggle_selected_chat_ids(selected_chat_ids, chat_id, enabled_token == "on")
        page = int(data.get("dest_page", 0) or 0)
        await state.update_data(selected_chat_ids=next_selected_chat_ids)
        await query.answer()
        await _render_broadcast_destinations(
            query.message,
            state,
            user_id=query.from_user.id,
            page=page,
            edit=True,
        )

    @router.callback_query(F.data == "bcdone")
    async def cb_broadcast_dest_done(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != BroadcastStates.choosing_destinations.state:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        data = await state.get_data()
        selected_chat_ids = _normalize_selected_chat_ids(data.get("selected_chat_ids"))
        valid_chat_ids = {destination.chat_id for destination in await _list_all_user_destinations(query.from_user.id)}
        selected_chat_ids = [chat_id for chat_id in selected_chat_ids if chat_id in valid_chat_ids]
        await state.update_data(selected_chat_ids=selected_chat_ids)
        if not selected_chat_ids:
            await query.answer(tr(lang, "broadcast_choose_one"), show_alert=True)
            return

        await state.update_data(
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
            scheduled_at_utc=None,
            scheduled_local=None,
        )
        await state.set_state(BroadcastStates.entering_datetime)
        await query.answer()
        await _clear_inline_markup(query.message)
        await _prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "enter_datetime"),
            data=await state.get_data(),
            state_name=BroadcastStates.entering_datetime.state,
        )

    @router.callback_query(F.data.startswith("sdpage:"))
    async def cb_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        page = int(query.data.split(":")[1])
        await state.update_data(dest_page=page)
        await query.answer()
        await _render_destinations(query.message, page=page, user_id=query.from_user.id)

    @router.callback_query(F.data.startswith("rdpage:"))
    async def cb_repeat_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != RepeatStates.choosing_destination.state:
            await query.answer()
            return

        page = int(query.data.split(":")[1])
        await state.update_data(dest_page=page)
        await query.answer()
        await _render_destinations(
            query.message,
            page=page,
            user_id=query.from_user.id,
            select_prefix="rdsel",
            page_prefix="rdpage",
        )

    @router.callback_query(F.data.startswith("rint:"))
    async def cb_repeat_interval(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != RepeatStates.choosing_interval.state:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        interval_type = query.data.split(":")[1]
        if interval_type == "custom":
            await query.answer(tr(lang, "repeat_custom_unavailable"), show_alert=True)
            return
        if interval_type not in {"daily", "weekly", "weekdays"}:
            await query.answer(tr(lang, "repeat_interval_invalid"), show_alert=True)
            return

        await state.update_data(
            interval_type=interval_type,
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
        )
        await state.set_state(RepeatStates.entering_datetime)
        await query.answer()
        await _prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "repeat_enter_datetime"),
            data=await state.get_data(),
            state_name=RepeatStates.entering_datetime.state,
        )

    @router.callback_query(F.data.startswith("sdsel:"))
    async def cb_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        chat_id = int(query.data.split(":")[1])
        await state.update_data(
            chat_id=chat_id,
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
        )
        await state.set_state(ScheduleStates.entering_datetime)
        await query.answer()
        await _prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "enter_datetime"),
            data=await state.get_data(),
            state_name=ScheduleStates.entering_datetime.state,
        )

    @router.callback_query(F.data.startswith("rdsel:"))
    async def cb_repeat_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != RepeatStates.choosing_destination.state:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        data = await state.get_data()
        scheduled_at_utc = data.get("scheduled_at_utc")
        scheduled_local = data.get("scheduled_local")
        if not isinstance(scheduled_at_utc, int) or not isinstance(scheduled_local, str):
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            await state.clear()
            return

        chat_id = int(query.data.split(":")[1])
        await state.update_data(chat_id=chat_id)
        await query.answer()
        await _move_to_post_collection(
            query.message,
            state,
            scheduled_at_utc=scheduled_at_utc,
            scheduled_local=scheduled_local,
            collecting_state=RepeatStates.collecting_post,
            lang=lang,
        )

    @router.callback_query(F.data == TimePicker.NOOP_CALLBACK)
    async def cb_time_picker_noop(query: CallbackQuery) -> None:
        await query.answer()

    @router.callback_query(F.data.startswith("tp:nav:"))
    async def cb_schedule_calendar_nav(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_datetime_entry_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        token = query.data.split(":", 2)[2]
        try:
            year, month = _parse_calendar_month_token(token)
        except ValueError:
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            return

        await state.update_data(calendar_year=year, calendar_month=month)
        data = await state.get_data()
        if current_state == DraftStates.entering_datetime.state:
            where = await store.get_destination_title(int(data.get("chat_id") or 0)) or str(data.get("chat_id") or "")
            text = _draft_post_prompt_text(lang, draft_id=data.get("draft_publish_id"), where=where)
        elif current_state == RepeatStates.entering_datetime.state:
            text = tr(lang, "repeat_enter_datetime")
        elif current_state == EditStates.entering_datetime.state:
            text = tr(lang, "edit_time_prompt", post_id=_short_id(str(data.get("edit_post_id") or "")))
        else:
            text = tr(lang, "enter_datetime")
        await query.answer()
        await _edit_datetime_prompt(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=text,
            data=data,
            state_name=current_state,
        )

    @router.callback_query(F.data.startswith("tp:date:"))
    async def cb_schedule_calendar_date(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_datetime_entry_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        token = query.data.split(":", 2)[2]
        try:
            selected_date = _parse_calendar_date_token(token)
        except ValueError:
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            return

        await state.update_data(
            selected_date=selected_date.isoformat(),
            calendar_year=selected_date.year,
            calendar_month=selected_date.month,
        )
        if current_state == RepeatStates.entering_datetime.state:
            next_state = RepeatStates.selecting_time
        elif current_state == DraftStates.entering_datetime.state:
            next_state = DraftStates.selecting_time
        elif current_state == BroadcastStates.entering_datetime.state:
            next_state = BroadcastStates.selecting_time
        elif current_state == EditStates.entering_datetime.state:
            next_state = EditStates.selecting_time
        else:
            next_state = ScheduleStates.selecting_time
        await state.set_state(next_state)
        await query.answer()
        await _edit_datetime_prompt(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=_schedule_time_prompt(lang, selected_date=selected_date),
            data=await state.get_data(),
            state_name=next_state.state,
        )

    @router.callback_query(F.data.startswith("tp:quick:"))
    async def cb_schedule_quick(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_datetime_entry_state(current_state):
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

        validation_text = _schedule_validation_text(lang, parsed.utc_epoch)
        if validation_text is not None:
            await query.answer(validation_text, show_alert=True)
            return

        await query.answer()
        await _clear_inline_markup(query.message)
        if current_state == RepeatStates.entering_datetime.state:
            await _move_repeat_to_destination_selection(
                query.message,
                state,
                user_id=query.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
            )
            return
        if current_state == DraftStates.entering_datetime.state:
            await _move_draft_publish_to_confirmation(
                query.message,
                state,
                user_id=query.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
            )
            return
        if current_state == EditStates.entering_datetime.state:
            await _save_scheduled_post_time(
                query.message,
                state,
                user_id=query.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
            )
            return
        if current_state == BroadcastStates.entering_datetime.state:
            await _move_to_post_collection(
                query.message,
                state,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
                collecting_state=BroadcastStates.collecting_post,
                lang=lang,
            )
            return
        await _move_to_post_collection(
            query.message,
            state,
            scheduled_at_utc=parsed.utc_epoch,
            scheduled_local=str(parsed.local_dt),
            collecting_state=ScheduleStates.collecting_post,
            lang=lang,
        )

    @router.callback_query(F.data == "tp:back:calendar")
    async def cb_schedule_back_to_calendar(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_time_selection_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        data = await state.get_data()
        selected_date = _selected_date_from_state(data)
        if selected_date is not None:
            await state.update_data(calendar_year=selected_date.year, calendar_month=selected_date.month)
        if current_state == RepeatStates.selecting_time.state:
            previous_state = RepeatStates.entering_datetime
        elif current_state == DraftStates.selecting_time.state:
            previous_state = DraftStates.entering_datetime
        elif current_state == BroadcastStates.selecting_time.state:
            previous_state = BroadcastStates.entering_datetime
        elif current_state == EditStates.selecting_time.state:
            previous_state = EditStates.entering_datetime
        else:
            previous_state = ScheduleStates.entering_datetime
        await state.set_state(previous_state)
        data = await state.get_data()
        if previous_state == DraftStates.entering_datetime:
            where = await store.get_destination_title(int(data.get("chat_id") or 0)) or str(data.get("chat_id") or "")
            text = _draft_post_prompt_text(lang, draft_id=data.get("draft_publish_id"), where=where)
        elif previous_state == RepeatStates.entering_datetime:
            text = tr(lang, "repeat_enter_datetime")
        elif previous_state == EditStates.entering_datetime:
            text = tr(lang, "edit_time_prompt", post_id=_short_id(str(data.get("edit_post_id") or "")))
        else:
            text = tr(lang, "enter_datetime")
        await query.answer()
        await _edit_datetime_prompt(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=text,
            data=data,
            state_name=previous_state.state,
        )

    @router.callback_query(F.data.startswith("tp:time:"))
    async def cb_schedule_time(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if not _is_time_selection_state(current_state):
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(query.from_user.id))
            await state.clear()
            return

        data = await state.get_data()
        selected_date = _selected_date_from_state(data)
        if selected_date is None:
            if current_state == RepeatStates.selecting_time.state:
                previous_state = RepeatStates.entering_datetime
            elif current_state == DraftStates.selecting_time.state:
                previous_state = DraftStates.entering_datetime
            elif current_state == BroadcastStates.selecting_time.state:
                previous_state = BroadcastStates.entering_datetime
            elif current_state == EditStates.selecting_time.state:
                previous_state = EditStates.entering_datetime
            else:
                previous_state = ScheduleStates.entering_datetime
            await state.set_state(previous_state)
            data = await state.get_data()
            if previous_state == DraftStates.entering_datetime:
                where = await store.get_destination_title(int(data.get("chat_id") or 0)) or str(data.get("chat_id") or "")
                text = _draft_post_prompt_text(lang, draft_id=data.get("draft_publish_id"), where=where)
            elif previous_state == RepeatStates.entering_datetime:
                text = tr(lang, "repeat_enter_datetime")
            elif previous_state == EditStates.entering_datetime:
                text = tr(lang, "edit_time_prompt", post_id=_short_id(str(data.get("edit_post_id") or "")))
            else:
                text = tr(lang, "enter_datetime")
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            await _edit_datetime_prompt(
                query.message,
                lang=lang,
                tz_name=tz_name,
                text=text,
                data=data,
                state_name=previous_state.state,
            )
            return

        token = query.data.split(":", 2)[2]
        try:
            hour, minute = _parse_time_token(token)
            parsed = resolve_selected_time(selected_date, hour=hour, minute=minute, tz_name=tz_name)
        except ValueError:
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            return

        validation_text = _schedule_validation_text(lang, parsed.utc_epoch)
        if validation_text is not None:
            await query.answer(validation_text, show_alert=True)
            return

        await query.answer()
        await _clear_inline_markup(query.message)
        if current_state == RepeatStates.selecting_time.state:
            await _move_repeat_to_destination_selection(
                query.message,
                state,
                user_id=query.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
            )
            return
        if current_state == DraftStates.selecting_time.state:
            await _move_draft_publish_to_confirmation(
                query.message,
                state,
                user_id=query.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
            )
            return
        if current_state == EditStates.selecting_time.state:
            await _save_scheduled_post_time(
                query.message,
                state,
                user_id=query.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
            )
            return
        if current_state == BroadcastStates.selecting_time.state:
            await _move_to_post_collection(
                query.message,
                state,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
                collecting_state=BroadcastStates.collecting_post,
                lang=lang,
            )
            return
        await _move_to_post_collection(
            query.message,
            state,
            scheduled_at_utc=parsed.utc_epoch,
            scheduled_local=str(parsed.local_dt),
            collecting_state=ScheduleStates.collecting_post,
            lang=lang,
        )

    @router.message(EditStates.entering_text)
    async def edit_enter_text(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        if not message.text:
            await message.answer(tr(lang, "text_required"))
            return
        await _save_scheduled_post_text(
            message,
            state,
            user_id=message.from_user.id,
            text=message.text,
            entities_json=store.dump_entities(message.entities),
        )

    @router.message(BroadcastStates.selecting_time)
    @router.message(BroadcastStates.entering_datetime)
    @router.message(EditStates.selecting_time)
    @router.message(EditStates.entering_datetime)
    @router.message(DraftStates.selecting_time)
    @router.message(DraftStates.entering_datetime)
    @router.message(RepeatStates.selecting_time)
    @router.message(RepeatStates.entering_datetime)
    @router.message(ScheduleStates.selecting_time)
    @router.message(ScheduleStates.entering_datetime)
    async def schedule_enter_datetime(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await _user_lang(message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await _main_menu_for(message.from_user.id))
            await state.clear()
            return

        current_state = await state.get_state()
        data = await state.get_data()
        try:
            parsed: ParsedScheduleTime = parse_local_datetime(message.text, tz_name=tz_name)
        except Exception:
            await _prompt_for_datetime(
                message,
                lang=lang,
                tz_name=tz_name,
                text=tr(lang, "invalid_datetime_format"),
                data=data,
                state_name=current_state,
            )
            return

        validation_text = _schedule_validation_text(lang, parsed.utc_epoch)
        if validation_text is not None:
            await _prompt_for_datetime(
                message,
                lang=lang,
                tz_name=tz_name,
                text=validation_text,
                data=data,
                state_name=current_state,
            )
            return

        if current_state in {RepeatStates.entering_datetime.state, RepeatStates.selecting_time.state}:
            await _move_repeat_to_destination_selection(
                message,
                state,
                user_id=message.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
            )
            return

        if current_state in {DraftStates.entering_datetime.state, DraftStates.selecting_time.state}:
            await _move_draft_publish_to_confirmation(
                message,
                state,
                user_id=message.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
            )
            return

        if current_state in {EditStates.entering_datetime.state, EditStates.selecting_time.state}:
            await _save_scheduled_post_time(
                message,
                state,
                user_id=message.from_user.id,
                scheduled_at_utc=parsed.utc_epoch,
            )
            return

        if current_state in {BroadcastStates.entering_datetime.state, BroadcastStates.selecting_time.state}:
            await _move_to_post_collection(
                message,
                state,
                scheduled_at_utc=parsed.utc_epoch,
                scheduled_local=str(parsed.local_dt),
                collecting_state=BroadcastStates.collecting_post,
                lang=lang,
            )
            return

        await _move_to_post_collection(
            message,
            state,
            scheduled_at_utc=parsed.utc_epoch,
            scheduled_local=str(parsed.local_dt),
            collecting_state=ScheduleStates.collecting_post,
            lang=lang,
        )

    @router.message(EditStates.collecting_media)
    @router.message(BroadcastStates.collecting_post)
    @router.message(DraftStates.editing_post)
    @router.message(DraftStates.collecting_post)
    @router.message(RepeatStates.collecting_post)
    @router.message(ScheduleStates.collecting_post)
    async def schedule_collect_post(message: Message, state: FSMContext) -> None:
        lang = await _user_lang(message.from_user.id)
        current_state = await state.get_state()
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

        if (
            current_state == EditStates.collecting_media.state
            and bool(data.get("edit_preserve_caption_above"))
            and not had_media_before
            and not caption_from_message
        ):
            caption_above = bool(data.get("caption_above", False))
        else:
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
        current_state = await state.get_state()
        if current_state not in {
            BroadcastStates.collecting_post.state,
            ScheduleStates.collecting_post.state,
            RepeatStates.collecting_post.state,
            DraftStates.collecting_post.state,
            DraftStates.editing_post.state,
            EditStates.collecting_media.state,
        }:
            await query.answer()
            return

        await query.answer()
        if current_state == RepeatStates.collecting_post.state:
            collecting_state = RepeatStates.collecting_post
        elif current_state == BroadcastStates.collecting_post.state:
            collecting_state = BroadcastStates.collecting_post
        elif current_state == EditStates.collecting_media.state:
            collecting_state = EditStates.collecting_media
        elif current_state == DraftStates.editing_post.state:
            collecting_state = DraftStates.editing_post
        elif current_state == DraftStates.collecting_post.state:
            collecting_state = DraftStates.collecting_post
        else:
            collecting_state = ScheduleStates.collecting_post
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
        await state.set_state(collecting_state)
        await query.message.answer(tr(lang, "media_cleared"), reply_markup=_media_collect_kb(lang))

    @router.callback_query(F.data == "smedia:done")
    async def cb_media_done(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if current_state not in {
            BroadcastStates.collecting_post.state,
            ScheduleStates.collecting_post.state,
            RepeatStates.collecting_post.state,
            DraftStates.collecting_post.state,
            DraftStates.editing_post.state,
            EditStates.collecting_media.state,
        }:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        await query.answer()
        data = await state.get_data()
        media: list[dict[str, str]] = list(data.get("media_items", []))
        draft_text = data.get("draft_text")
        draft_text_valid = bool(str(draft_text).strip()) if draft_text is not None else False

        if current_state == EditStates.collecting_media.state:
            if not media:
                await query.message.answer(tr(lang, "media_need_at_least_one"), reply_markup=_media_collect_kb(lang))
                return
            await state.update_data(
                kind="media",
                caption=draft_text if draft_text_valid else None,
                caption_entities_json=data.get("draft_entities_json") if draft_text_valid else None,
                text=None,
                entities_json=None,
            )
            if await _save_scheduled_post_media(query.message, state, user_id=query.from_user.id):
                return
            return

        if media:
            await state.update_data(
                kind="media",
                caption=draft_text if draft_text_valid else None,
                caption_entities_json=data.get("draft_entities_json") if draft_text_valid else None,
                text=None,
                entities_json=None,
            )
            if current_state == DraftStates.editing_post.state:
                if await _update_draft_from_state(query.message, state, user_id=query.from_user.id):
                    return
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(query.from_user.id))
                return
            if current_state == DraftStates.collecting_post.state:
                await _prompt_draft_scope(query.message, state, user_id=query.from_user.id)
                return
            await _send_confirmation(query.message, state, store, user_id=query.from_user.id)
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
            if current_state == DraftStates.editing_post.state:
                if await _update_draft_from_state(query.message, state, user_id=query.from_user.id):
                    return
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(query.from_user.id))
                return
            if current_state == DraftStates.collecting_post.state:
                await _prompt_draft_scope(query.message, state, user_id=query.from_user.id)
                return
            await _send_confirmation(query.message, state, store, user_id=query.from_user.id)
            return

        await query.message.answer(tr(lang, "post_need_content"), reply_markup=_media_collect_kb(lang))

    @router.message(DraftStates.choosing_scope)
    async def draft_choose_scope(message: Message) -> None:
        lang = await _user_lang(message.from_user.id)
        writable_teams = await store.list_writable_teams(message.from_user.id)
        await message.answer(
            tr(lang, "draft_create_scope_prompt"),
            reply_markup=_draft_create_scope_kb(writable_teams, lang),
        )

    async def _send_confirmation(message: Message, state: FSMContext, store_: StateStore, *, user_id: int) -> None:
        lang = await _user_lang(user_id)
        data = await state.get_data()
        current_state = await state.get_state()
        is_repeat = current_state == RepeatStates.collecting_post.state
        is_broadcast = current_state == BroadcastStates.collecting_post.state
        tz_name = await store_.get_user_timezone(user_id) or "UTC"
        local_time = _format_local(int(data["scheduled_at_utc"]), tz_name)
        kind = data.get("kind")
        if kind == "text":
            summary = tr(lang, "kind_text")
            preview = _draft_preview_text(data.get("text"), fallback=tr(lang, "draft_preview_empty"), limit=240)
        else:
            media = list(data.get("media_items", []))
            summary = tr(lang, "kind_media", count=len(media))
            preview = _draft_preview_text(
                data.get("caption"),
                fallback=tr(lang, "draft_preview_media_no_caption"),
                limit=240,
            )
        if is_repeat:
            await state.set_state(RepeatStates.confirming)
            chat_id = int(data["chat_id"])
            title = await store_.get_destination_title(chat_id) or str(chat_id)
            interval_label = _repeat_interval_label(lang, str(data.get("interval_type") or ""))
            text = tr(
                lang,
                "repeat_confirm_template",
                where=title,
                local_time=local_time,
                tz_name=tz_name,
                interval=interval_label,
                kind=summary,
            )
        elif is_broadcast:
            selected_chat_ids, where_lines = await _resolve_broadcast_destination_lines(
                user_id,
                _normalize_selected_chat_ids(data.get("selected_chat_ids")),
            )
            if not selected_chat_ids:
                await state.update_data(selected_chat_ids=[], dest_page=0)
                await state.set_state(BroadcastStates.choosing_destinations)
                await _render_broadcast_destinations(message, state, user_id=user_id, page=0, edit=False)
                return
            await state.update_data(selected_chat_ids=selected_chat_ids)
            await state.set_state(BroadcastStates.confirming)
            text = tr(
                lang,
                "broadcast_confirm_template",
                count=len(selected_chat_ids),
                where_lines=where_lines,
                local_time=local_time,
                tz_name=tz_name,
                kind=summary,
                preview=preview,
            )
        else:
            await state.set_state(ScheduleStates.confirming)
            chat_id = int(data["chat_id"])
            title = await store_.get_destination_title(chat_id) or str(chat_id)
            text = tr(lang, "confirm_template", where=title, local_time=local_time, tz_name=tz_name, kind=summary)
        await message.answer(text, reply_markup=_confirm_kb(lang))

    @router.callback_query(F.data == "sconf:yes")
    async def cb_confirm_yes(query: CallbackQuery, state: FSMContext) -> None:
        current_state = await state.get_state()
        if current_state not in {
            ScheduleStates.confirming.state,
            RepeatStates.confirming.state,
            DraftStates.confirming.state,
            BroadcastStates.confirming.state,
        }:
            await query.answer()
            return

        lang = await _user_lang(query.from_user.id)
        await query.answer()
        data = await state.get_data()
        user_id = query.from_user.id
        scheduled_at_utc = int(data["scheduled_at_utc"])
        kind = data.get("kind")
        tz_name = await store.get_user_timezone(user_id) or "UTC"

        if current_state == BroadcastStates.confirming.state:
            resolved_destinations = await _resolve_broadcast_destinations(
                user_id,
                _normalize_selected_chat_ids(data.get("selected_chat_ids")),
            )
            if not resolved_destinations:
                await state.update_data(selected_chat_ids=[], dest_page=0)
                await state.set_state(BroadcastStates.choosing_destinations)
                await _render_broadcast_destinations(query.message, state, user_id=user_id, page=0, edit=False)
                return

            selected_chat_ids = [chat_id for chat_id, _ in resolved_destinations]
            await state.update_data(selected_chat_ids=selected_chat_ids)
            for chat_id, _ in resolved_destinations:
                ok, err = await _check_user_admin(query.bot, chat_id=chat_id, user_id=user_id, lang=lang)
                if not ok:
                    await query.message.answer(err, reply_markup=_confirm_kb(lang))
                    return
                ok, err = await _check_bot_admin_and_post(query.bot, chat_id=chat_id, lang=lang)
                if not ok:
                    await query.message.answer(err, reply_markup=_confirm_kb(lang))
                    return

            if kind == "text":
                post_ids = await store.create_broadcast_posts(
                    user_id=user_id,
                    chat_ids=selected_chat_ids,
                    scheduled_at_utc=scheduled_at_utc,
                    kind="text",
                    text=str(data.get("text") or ""),
                    entities_json=data.get("entities_json"),
                )
            else:
                media_items: list[dict[str, str]] = list(data.get("media_items", []))
                post_ids = await store.create_broadcast_posts(
                    user_id=user_id,
                    chat_ids=selected_chat_ids,
                    scheduled_at_utc=scheduled_at_utc,
                    kind="media",
                    caption=data.get("caption"),
                    caption_entities_json=data.get("caption_entities_json"),
                    caption_above=bool(data.get("caption_above", False)),
                    media_items=media_items,
                )

            await state.clear()
            local_time = _format_local(scheduled_at_utc, tz_name)
            lines = "\n".join(
                f"- {label}: id={_short_id(post_id)}"
                for (_, label), post_id in zip(resolved_destinations, post_ids)
            )
            await query.message.answer(
                tr(
                    lang,
                    "broadcast_created_ok",
                    count=len(post_ids),
                    local_time=local_time,
                    tz_name=tz_name,
                    lines=lines,
                ),
                reply_markup=await _main_menu_for(query.from_user.id),
            )
            return

        if current_state == RepeatStates.confirming.state:
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

            local_dt = datetime.fromtimestamp(scheduled_at_utc, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
            time_of_day_minutes = local_dt.hour * 60 + local_dt.minute
            interval_type = str(data.get("interval_type") or "")
            pattern_id, post_id = await store.create_recurring_series(
                user_id=user_id,
                chat_id=chat_id,
                interval_type=interval_type,
                weekdays_mask=_repeat_weekdays_mask(interval_type),
                time_of_day_minutes=time_of_day_minutes,
                timezone=tz_name,
                start_at_utc=scheduled_at_utc,
                kind=str(kind or ""),
                text=str(data.get("text") or "") if kind == "text" else None,
                entities_json=data.get("entities_json") if kind == "text" else None,
                caption=data.get("caption") if kind == "media" else None,
                caption_entities_json=data.get("caption_entities_json") if kind == "media" else None,
                caption_above=bool(data.get("caption_above", False)) if kind == "media" else None,
                media_items=list(data.get("media_items", [])) if kind == "media" else None,
            )
            await state.clear()
            local_time = _format_local(scheduled_at_utc, tz_name)
            await query.message.answer(
                tr(
                    lang,
                    "repeat_created_ok",
                    interval=_repeat_interval_label(lang, interval_type),
                    local_time=local_time,
                    tz_name=tz_name,
                    pattern_id=_short_id(pattern_id),
                ),
                reply_markup=await _main_menu_for(query.from_user.id),
            )
            return

        if current_state == DraftStates.confirming.state:
            draft_id = data.get("draft_publish_id")
            if not isinstance(draft_id, str):
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(query.from_user.id))
                return

            permissions = await store.get_draft_permissions(draft_id, user_id)
            draft = await store.get_draft(draft_id) if permissions is not None and permissions.can_publish else None
            if draft is None or permissions is None or not permissions.can_publish:
                await state.clear()
                await query.message.answer(tr(lang, "draft_missing"), reply_markup=await _main_menu_for(query.from_user.id))
                return

            ok, err = await _check_user_admin(query.bot, chat_id=draft.chat_id, user_id=user_id, lang=lang)
            if not ok:
                await query.message.answer(err, reply_markup=await _main_menu_for(query.from_user.id))
                await state.clear()
                return
            ok, err = await _check_bot_admin_and_post(query.bot, chat_id=draft.chat_id, lang=lang)
            if not ok:
                await query.message.answer(err, reply_markup=await _main_menu_for(query.from_user.id))
                await state.clear()
                return

            if draft.kind == "text":
                post_id = await store.create_scheduled_text_post(
                    user_id=user_id,
                    chat_id=draft.chat_id,
                    scheduled_at_utc=scheduled_at_utc,
                    text=str(draft.text or ""),
                    entities_json=draft.entities_json,
                )
            else:
                media_items = await store.get_draft_media(draft.id)
                post_id = await store.create_scheduled_media_post(
                    user_id=user_id,
                    chat_id=draft.chat_id,
                    scheduled_at_utc=scheduled_at_utc,
                    caption=draft.caption,
                    caption_entities_json=draft.caption_entities_json,
                    caption_above=None if draft.caption_above is None else bool(draft.caption_above),
                    media_items=media_items,
                )

            await state.clear()
            local_time = _format_local(scheduled_at_utc, tz_name)
            await query.message.answer(
                tr(
                    lang,
                    "draft_post_created_ok",
                    draft_id=_short_id(draft.id),
                    local_time=local_time,
                    tz_name=tz_name,
                    post_id=_short_id(post_id),
                ),
                reply_markup=await _main_menu_for(query.from_user.id),
            )
            return

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
        await state.update_data(chat_id=chat_id, selected_date=None, calendar_year=None, calendar_month=None)
        local_time = _format_local(scheduled_at_utc, tz_name)
        title = await store.get_destination_title(chat_id) or str(chat_id)
        await query.message.answer(
            tr(lang, "scheduled_ok", local_time=local_time, tz_name=tz_name, post_id=_short_id(post_id)),
        )
        await _prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "schedule_next_prompt", where=title),
            data=await state.get_data(),
            state_name=ScheduleStates.entering_datetime.state,
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
        await _render_queue_page(message, page=0, user_id=message.from_user.id)

    @router.callback_query(F.data.startswith("qpage:"))
    async def cb_queue_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_queue_page(query.message, page=page, user_id=query.from_user.id, edit=True)

    @router.callback_query(F.data.startswith("qview:"))
    async def cb_queue_view(query: CallbackQuery, state: FSMContext) -> None:
        post_id = query.data.split(":", 1)[1]
        await query.answer()
        await _send_post_preview(query.message, user_id=query.from_user.id, post_id=post_id, state=state)

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
