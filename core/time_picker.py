from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import MIN_SCHEDULE_LEAD_SECONDS, ParsedScheduleTime

_DEFAULT_WEEKDAY_LABELS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_DEFAULT_QUICK_OPTION_KEYS = ("1h", "today_20", "tomorrow_9", "next_monday")


@dataclass(frozen=True)
class CalendarCell:
    value: date | None
    text: str
    callback_data: str
    in_current_month: bool


@dataclass(frozen=True)
class QuickOption:
    key: str
    callback_data: str


def generate_calendar(year: int, month: int) -> tuple[tuple[CalendarCell, ...], ...]:
    month_matrix = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    rows: list[tuple[CalendarCell, ...]] = []
    for week in month_matrix:
        cells: list[CalendarCell] = []
        for day in week:
            if day == 0:
                cells.append(
                    CalendarCell(
                        value=None,
                        text=" ",
                        callback_data=TimePicker.NOOP_CALLBACK,
                        in_current_month=False,
                    )
                )
                continue
            current = date(year, month, day)
            cells.append(
                CalendarCell(
                    value=current,
                    text=str(day),
                    callback_data=TimePicker.pack_date(current),
                    in_current_month=True,
                )
            )
        rows.append(tuple(cells))
    return tuple(rows)


def get_quick_options() -> tuple[QuickOption, ...]:
    return tuple(QuickOption(key=key, callback_data=TimePicker.pack_quick(key)) for key in _DEFAULT_QUICK_OPTION_KEYS)


def resolve_quick_option(
    option: str,
    *,
    tz_name: str,
    now_utc: datetime | None = None,
    min_lead_seconds: int = MIN_SCHEDULE_LEAD_SECONDS,
) -> ParsedScheduleTime:
    if min_lead_seconds < 0:
        raise ValueError("min_lead_seconds must be >= 0")

    current_utc = _coerce_utc(now_utc)
    tz = ZoneInfo(tz_name)
    current_local = current_utc.astimezone(tz)

    if option == "1h":
        candidate_local = _ceil_to_next_minute(current_local + timedelta(hours=1))
    elif option == "today_20":
        candidate_local = _local_datetime(current_local.date(), hour=20, minute=0, tz=tz)
        if int(candidate_local.astimezone(timezone.utc).timestamp()) <= int(current_utc.timestamp()) + min_lead_seconds:
            candidate_local = _local_datetime(current_local.date() + timedelta(days=1), hour=20, minute=0, tz=tz)
    elif option == "tomorrow_9":
        candidate_local = _local_datetime(current_local.date() + timedelta(days=1), hour=9, minute=0, tz=tz)
    elif option == "next_monday":
        days_ahead = (7 - current_local.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        candidate_local = _local_datetime(current_local.date() + timedelta(days=days_ahead), hour=9, minute=0, tz=tz)
    else:
        raise ValueError(f"unsupported quick option: {option}")

    candidate_utc = candidate_local.astimezone(timezone.utc)
    if int(candidate_utc.timestamp()) <= int(current_utc.timestamp()) + min_lead_seconds:
        raise ValueError(f"quick option {option!r} did not resolve to a future time")

    return ParsedScheduleTime(local_dt=candidate_local, utc_epoch=int(candidate_utc.timestamp()))


def resolve_selected_time(value: date, *, hour: int, minute: int, tz_name: str) -> ParsedScheduleTime:
    if not 0 <= hour <= 23:
        raise ValueError("hour must be in range 0..23")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be in range 0..59")

    tz = ZoneInfo(tz_name)
    local_dt = _local_datetime(value, hour=hour, minute=minute, tz=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    return ParsedScheduleTime(local_dt=local_dt, utc_epoch=int(utc_dt.timestamp()))


class TimePicker:
    NOOP_CALLBACK = "tp:noop"

    def __init__(
        self,
        *,
        weekday_labels: Sequence[str] | None = None,
        previous_label: str = "<",
        next_label: str = ">",
    ) -> None:
        labels = tuple(weekday_labels or _DEFAULT_WEEKDAY_LABELS)
        if len(labels) != 7:
            raise ValueError("weekday_labels must contain exactly 7 values")
        self._weekday_labels = labels
        self._previous_label = previous_label
        self._next_label = next_label

    @staticmethod
    def pack_quick(option: str) -> str:
        return f"tp:quick:{option}"

    @staticmethod
    def pack_nav(year: int, month: int) -> str:
        return f"tp:nav:{year:04d}{month:02d}"

    @staticmethod
    def pack_date(value: date) -> str:
        return f"tp:date:{value:%Y%m%d}"

    @staticmethod
    def pack_time(hour: int, minute: int) -> str:
        if not 0 <= hour <= 23:
            raise ValueError("hour must be in range 0..23")
        if not 0 <= minute <= 59:
            raise ValueError("minute must be in range 0..59")
        return f"tp:time:{hour:02d}{minute:02d}"

    def calendar_month(
        self,
        year: int,
        month: int,
        *,
        selected: date | None = None,
        month_label: str | None = None,
    ) -> InlineKeyboardMarkup:
        previous_year, previous_month = self._shift_month(year, month, delta=-1)
        next_year, next_month = self._shift_month(year, month, delta=1)
        rows: list[list[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(text=self._previous_label, callback_data=self.pack_nav(previous_year, previous_month)),
                InlineKeyboardButton(
                    text=month_label or f"{month:02d}.{year:04d}",
                    callback_data=self.NOOP_CALLBACK,
                ),
                InlineKeyboardButton(text=self._next_label, callback_data=self.pack_nav(next_year, next_month)),
            ],
            [
                InlineKeyboardButton(text=label, callback_data=self.NOOP_CALLBACK)
                for label in self._weekday_labels
            ],
        ]

        for week in generate_calendar(year, month):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self._format_day(cell, selected=selected),
                        callback_data=cell.callback_data,
                    )
                    for cell in week
                ]
            )

        return InlineKeyboardMarkup(inline_keyboard=rows)

    def quick_buttons(
        self,
        labels: Mapping[str, str] | None = None,
        *,
        per_row: int = 2,
    ) -> InlineKeyboardMarkup:
        if per_row <= 0:
            raise ValueError("per_row must be > 0")

        builder = InlineKeyboardBuilder()
        known_labels = labels or {}
        for option in get_quick_options():
            builder.button(text=known_labels.get(option.key, option.key), callback_data=option.callback_data)
        builder.adjust(per_row)
        return builder.as_markup()

    def time_selection(
        self,
        *,
        hours: Sequence[int] | None = None,
        minute_values: Sequence[int] = (0, 30),
        per_row: int = 4,
    ) -> InlineKeyboardMarkup:
        if per_row <= 0:
            raise ValueError("per_row must be > 0")

        hour_values = tuple(hours) if hours is not None else tuple(range(24))
        minutes = tuple(minute_values)
        if not hour_values:
            raise ValueError("hours must not be empty")
        if not minutes:
            raise ValueError("minute_values must not be empty")

        builder = InlineKeyboardBuilder()
        for hour in hour_values:
            if not 0 <= hour <= 23:
                raise ValueError("hours must contain values in range 0..23")
            for minute in minutes:
                if not 0 <= minute <= 59:
                    raise ValueError("minute_values must contain values in range 0..59")
                builder.button(
                    text=f"{hour:02d}:{minute:02d}",
                    callback_data=self.pack_time(hour, minute),
                )
        builder.adjust(per_row)
        return builder.as_markup()

    @staticmethod
    def _shift_month(year: int, month: int, *, delta: int) -> tuple[int, int]:
        total = (year * 12) + (month - 1) + delta
        shifted_year, month_index = divmod(total, 12)
        return shifted_year, month_index + 1

    @staticmethod
    def _format_day(cell: CalendarCell, *, selected: date | None) -> str:
        if cell.value is None:
            return cell.text
        if selected is not None and cell.value == selected:
            return f"[{cell.value.day}]"
        return cell.text


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ceil_to_next_minute(value: datetime) -> datetime:
    rounded = value.replace(second=0, microsecond=0)
    if rounded < value:
        rounded += timedelta(minutes=1)
    return rounded


def _local_datetime(day: date, *, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, dt_time(hour=hour, minute=minute), tzinfo=tz)
