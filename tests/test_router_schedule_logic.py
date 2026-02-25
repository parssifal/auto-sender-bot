from telegram.router import _resolve_caption_above


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
