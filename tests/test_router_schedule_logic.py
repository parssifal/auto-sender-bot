from datetime import date, datetime, timezone

from telegram.handlers.helpers import _resolve_caption_above, _schedule_validation_text
from telegram.handlers.keyboards import (
    _calendar_month_from_state,
    _parse_calendar_date_token,
    _parse_calendar_month_token,
    _parse_time_token,
    _schedule_calendar_kb,
    _schedule_time_kb,
)


def test_resolve_caption_above_for_text_before_first_media() -> None:
    assert _resolve_caption_above(
        current=False,
        had_media_before=False,
        text_before_media=True,
        text_after_media=False,
        explicit_above=None,
    )


def test_resolve_caption_above_for_text_after_media() -> None:
    assert not _resolve_caption_above(
        current=True,
        had_media_before=True,
        text_before_media=False,
        text_after_media=True,
        explicit_above=None,
    )


def test_resolve_caption_above_prefers_explicit_flag() -> None:
    assert _resolve_caption_above(
        current=False,
        had_media_before=False,
        text_before_media=True,
        text_after_media=False,
        explicit_above=False,
    ) is False
    assert _resolve_caption_above(
        current=False,
        had_media_before=False,
        text_before_media=False,
        text_after_media=False,
        explicit_above=True,
    )


def test_resolve_caption_above_keeps_current_for_extra_media() -> None:
    assert _resolve_caption_above(
        current=True,
        had_media_before=True,
        text_before_media=False,
        text_after_media=False,
        explicit_above=None,
    )


def test_schedule_calendar_keyboard_contains_calendar_quick_buttons_and_cancel() -> None:
    kb = _schedule_calendar_kb("ru", year=2026, month=3, selected=date(2026, 3, 12))

    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]

    assert kb.inline_keyboard[0][0].callback_data == "tp:nav:202602"
    assert [button.text for button in kb.inline_keyboard[1]] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    assert "tp:date:20260312" in callbacks
    assert "tp:quick:1h" in callbacks
    assert "tp:quick:next_monday" in callbacks
    assert callbacks[-1] == "scancel"


def test_schedule_time_keyboard_contains_back_and_cancel() -> None:
    kb = _schedule_time_kb("ru")

    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]

    assert "tp:time:0000" in callbacks
    assert "tp:time:2330" in callbacks
    assert callbacks[-2] == "tp:back:calendar"
    assert callbacks[-1] == "scancel"


def test_parse_time_picker_tokens() -> None:
    assert _parse_calendar_month_token("202603") == (2026, 3)
    assert _parse_calendar_date_token("20260312") == date(2026, 3, 12)
    assert _parse_time_token("0930") == (9, 30)


def test_calendar_month_from_state_defaults_to_local_month() -> None:
    year, month = _calendar_month_from_state(
        {},
        "Europe/Moscow",
        now_utc=datetime(2026, 3, 31, 21, 30, tzinfo=timezone.utc),
    )

    assert (year, month) == (2026, 4)


def test_schedule_validation_text_returns_expected_messages() -> None:
    assert _schedule_validation_text("ru", 100, now_utc=100) == "Время должно быть в будущем."
    assert _schedule_validation_text("ru", 399, now_utc=100) == "Время должно быть минимум через 5 минут."
    assert _schedule_validation_text("ru", 400, now_utc=100) is None
