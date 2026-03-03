from telegram.router import _resolve_caption_above, _schedule_datetime_kb


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


def test_schedule_datetime_keyboard_contains_quick_buttons_and_cancel() -> None:
    kb = _schedule_datetime_kb("ru")

    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]

    assert callbacks[:4] == [
        "tp:quick:1h",
        "tp:quick:today_20",
        "tp:quick:tomorrow_9",
        "tp:quick:next_monday",
    ]
    assert callbacks[-1] == "scancel"
