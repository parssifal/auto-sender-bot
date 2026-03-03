from datetime import date

import pytest

from core.time_picker import TimePicker, generate_calendar, get_quick_options


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
