import pytest

from core.notifier import send_media_post


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))

    async def send_photo(self, **kwargs):
        self.calls.append(("send_photo", kwargs))

    async def send_video(self, **kwargs):
        self.calls.append(("send_video", kwargs))

    async def send_media_group(self, **kwargs):
        self.calls.append(("send_media_group", kwargs))


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
