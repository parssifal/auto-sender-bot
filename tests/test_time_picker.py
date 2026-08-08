from datetime import date, datetime, timezone

import pytest

from core.time_picker import TimePicker, generate_calendar, get_quick_options, resolve_quick_option, resolve_selected_time
from core.utils import NonexistentLocalTimeError


def test_generate_calendar_marks_current_month_days() -> None:
    weeks = generate_calendar(2026, 3)

    assert len(weeks) == 6
    assert [cell.text for cell in weeks[0]] == [" ", " ", " ", " ", " ", " ", "1"]
    assert weeks[0][-1].value == date(2026, 3, 1)
    assert weeks[0][-1].callback_data == "tp:date:20260301"
    assert weeks[-1][-1].value is None
    assert weeks[-1][-1].callback_data == "tp:noop"


def test_get_quick_options_returns_expected_order_and_callbacks() -> None:
    options = get_quick_options()

    assert [option.key for option in options] == ["1h", "today_20", "tomorrow_9", "next_monday"]
    assert [option.callback_data for option in options] == [
        "tp:quick:1h",
        "tp:quick:today_20",
        "tp:quick:tomorrow_9",
        "tp:quick:next_monday",
    ]


def test_calendar_month_builds_navigation_and_selected_day() -> None:
    picker = TimePicker()

    markup = picker.calendar_month(2026, 3, selected=date(2026, 3, 12), month_label="March 2026")

    assert markup.inline_keyboard[0][0].callback_data == "tp:nav:202602"
    assert markup.inline_keyboard[0][1].text == "March 2026"
    assert markup.inline_keyboard[0][2].callback_data == "tp:nav:202604"
    assert [button.text for button in markup.inline_keyboard[1]] == ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    assert markup.inline_keyboard[4][3].text == "[12]"
    assert markup.inline_keyboard[4][3].callback_data == "tp:date:20260312"


def test_quick_buttons_use_external_labels() -> None:
    picker = TimePicker()

    markup = picker.quick_buttons(
        {
            "1h": "In 1 hour",
            "today_20": "Today 20:00",
            "tomorrow_9": "Tomorrow 09:00",
            "next_monday": "Next Monday",
        }
    )

    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["In 1 hour", "Today 20:00"],
        ["Tomorrow 09:00", "Next Monday"],
    ]
    assert markup.inline_keyboard[1][1].callback_data == "tp:quick:next_monday"


def test_time_selection_builds_compact_grid() -> None:
    picker = TimePicker()

    markup = picker.time_selection(hours=(9, 10), minute_values=(0, 30), per_row=2)

    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["09:00", "09:30"],
        ["10:00", "10:30"],
    ]
    assert markup.inline_keyboard[1][1].callback_data == "tp:time:1030"


def test_time_selection_validates_ranges() -> None:
    picker = TimePicker()

    with pytest.raises(ValueError):
        picker.time_selection(hours=(24,), minute_values=(0,))

    with pytest.raises(ValueError):
        picker.time_selection(hours=(10,), minute_values=(60,))


def test_resolve_quick_option_rolls_today_to_tomorrow_when_past() -> None:
    parsed = resolve_quick_option(
        "today_20",
        tz_name="Europe/Moscow",
        now_utc=datetime(2026, 3, 3, 18, 5, tzinfo=timezone.utc),
    )

    assert parsed.local_dt.isoformat() == "2026-03-04T20:00:00+03:00"


def test_resolve_quick_option_rolls_today_to_tomorrow_when_too_soon() -> None:
    parsed = resolve_quick_option(
        "today_20",
        tz_name="Europe/Moscow",
        now_utc=datetime(2026, 3, 3, 16, 57, tzinfo=timezone.utc),
    )

    assert parsed.local_dt.isoformat() == "2026-03-04T20:00:00+03:00"


def test_resolve_quick_option_uses_next_monday_morning() -> None:
    parsed = resolve_quick_option(
        "next_monday",
        tz_name="Europe/Moscow",
        now_utc=datetime(2026, 3, 2, 5, 30, tzinfo=timezone.utc),
    )

    assert parsed.local_dt.isoformat() == "2026-03-09T09:00:00+03:00"


def test_resolve_quick_option_rounds_one_hour_to_next_minute() -> None:
    parsed = resolve_quick_option(
        "1h",
        tz_name="UTC",
        now_utc=datetime(2026, 3, 3, 10, 7, 42, tzinfo=timezone.utc),
    )

    assert parsed.local_dt.isoformat() == "2026-03-03T11:08:00+00:00"


def test_resolve_quick_option_rejects_unknown_option() -> None:
    with pytest.raises(ValueError):
        resolve_quick_option("weekend", tz_name="UTC")


def test_resolve_selected_time_creates_local_and_utc_values() -> None:
    parsed = resolve_selected_time(date(2026, 3, 12), hour=9, minute=30, tz_name="Europe/Moscow")

    assert parsed.local_dt.isoformat() == "2026-03-12T09:30:00+03:00"
    assert parsed.utc_epoch == int(datetime(2026, 3, 12, 6, 30, tzinfo=timezone.utc).timestamp())


def test_resolve_quick_option_1h_adds_real_hour_across_dst_fallback() -> None:
    # T-08: Europe/Berlin fall-back on 2026-10-25, transition at 01:00 UTC.
    # now = 00:30 UTC (02:30 CEST). One REAL hour later = 01:30 UTC = 02:30 CET.
    # Buggy wall-clock arithmetic yields 03:30+01:00 (two real hours).
    parsed = resolve_quick_option(
        "1h",
        tz_name="Europe/Berlin",
        now_utc=datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
    )

    assert parsed.local_dt.isoformat() == "2026-10-25T02:30:00+01:00"
    assert parsed.utc_epoch == int(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).timestamp())


def test_resolve_selected_time_rejects_nonexistent_dst_gap() -> None:
    # T-07/T-09: 02:30 on 2026-03-29 does not exist in Europe/Berlin (spring-forward gap).
    # Calendar time picks feed the recurring flow; rejecting here stops a shifted series.
    with pytest.raises(NonexistentLocalTimeError):
        resolve_selected_time(date(2026, 3, 29), hour=2, minute=30, tz_name="Europe/Berlin")
