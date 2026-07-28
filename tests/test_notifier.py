import pytest

from core.notifier import send_media_post


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 100

    def _make_message(self) -> _FakeMessage:
        self._next_id += 1
        return _FakeMessage(self._next_id)

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        return self._make_message()

    async def send_photo(self, **kwargs):
        self.calls.append(("send_photo", kwargs))
        return self._make_message()

    async def send_video(self, **kwargs):
        self.calls.append(("send_video", kwargs))
        return self._make_message()

    async def send_media_group(self, **kwargs):
        self.calls.append(("send_media_group", kwargs))
        return [self._make_message() for _ in kwargs["media"]]


@pytest.mark.asyncio
async def test_send_media_group_caption_only_first_item() -> None:
    bot = FakeBot()
    media = [
        {"type": "photo", "file_id": "p1"},
        {"type": "video", "file_id": "v2"},
        {"type": "photo", "file_id": "p3"},
    ]
    await send_media_post(
        bot=bot,  # type: ignore[arg-type]
        chat_id=-100,
        media_items=media,
        caption="cap",
        caption_entities_json=None,
        caption_above=True,
    )
    assert bot.calls[0][0] == "send_media_group"
    built = bot.calls[0][1]["media"]
    assert built[0].caption == "cap"
    assert getattr(built[0], "show_caption_above_media") is True
    assert built[1].caption is None
    assert built[2].caption is None
    assert getattr(built[1], "show_caption_above_media") is True
    assert getattr(built[2], "show_caption_above_media") is True


@pytest.mark.asyncio
async def test_send_single_video_uses_send_video() -> None:
    bot = FakeBot()
    media = [{"type": "video", "file_id": "v1"}]
    await send_media_post(
        bot=bot,  # type: ignore[arg-type]
        chat_id=-100,
        media_items=media,
        caption="hello",
        caption_entities_json=None,
        caption_above=False,
    )
    assert bot.calls[0][0] == "send_video"
    assert bot.calls[0][1]["video"] == "v1"
    assert bot.calls[0][1]["caption"] == "hello"


@pytest.mark.asyncio
async def test_returns_message_ids_for_single_photo() -> None:
    bot = FakeBot()
    stats = await send_media_post(
        bot=bot,  # type: ignore[arg-type]
        chat_id=-100,
        media_items=[{"type": "photo", "file_id": "p1"}],
        caption="cap",
        caption_entities_json=None,
        caption_above=False,
    )
    assert stats.message_ids == (101,)


@pytest.mark.asyncio
async def test_returns_message_ids_for_media_group() -> None:
    bot = FakeBot()
    media = [
        {"type": "photo", "file_id": "p1"},
        {"type": "photo", "file_id": "p2"},
        {"type": "video", "file_id": "v3"},
    ]
    stats = await send_media_post(
        bot=bot,  # type: ignore[arg-type]
        chat_id=-100,
        media_items=media,
        caption=None,
        caption_entities_json=None,
        caption_above=False,
    )
    assert stats.message_ids == (101, 102, 103)


@pytest.mark.asyncio
async def test_returns_message_ids_including_separate_long_caption() -> None:
    bot = FakeBot()
    long_caption = "x" * 1500  # > 1024 -> sent separately, below the media
    stats = await send_media_post(
        bot=bot,  # type: ignore[arg-type]
        chat_id=-100,
        media_items=[{"type": "photo", "file_id": "p1"}],
        caption=long_caption,
        caption_entities_json=None,
        caption_above=False,
    )
    # photo first (101), then the separate caption message (102)
    assert stats.message_ids == (101, 102)
