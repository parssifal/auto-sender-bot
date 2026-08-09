import pytest

from core.notifier import InvalidEntitiesError, _load_entities, send_media_post, send_text


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


@pytest.mark.asyncio
async def test_send_text_with_entities_sends_one_message_carrying_entities() -> None:
    # T-53, entities branch: a short formatted post goes out as ONE send_message with
    # its parsed entities attached (the format survives).
    bot = FakeBot()
    entities_json = '[{"type": "bold", "offset": 0, "length": 5}]'
    stats = await send_text(bot=bot, chat_id=-100, text="hello world", entities_json=entities_json)  # type: ignore[arg-type]

    assert stats.messages_sent == 1
    assert len(bot.calls) == 1
    method, kwargs = bot.calls[0]
    assert method == "send_message"
    assert kwargs["text"] == "hello world"
    entities = kwargs["entities"]
    assert entities is not None and len(entities) == 1
    assert entities[0].type == "bold"
    assert (entities[0].offset, entities[0].length) == (0, 5)


@pytest.mark.asyncio
async def test_send_text_without_entities_uses_split_path_without_entities_kwarg() -> None:
    # T-53, no-entities branch: with no entities_json the entities branch is skipped and
    # split_text runs; a short text is one chunk sent with NO `entities` kwarg at all.
    # Pins the `and entities_json` guard: dropping it would route this through the entity
    # branch and attach entities=None here.
    bot = FakeBot()
    stats = await send_text(bot=bot, chat_id=-100, text="plain text", entities_json=None)  # type: ignore[arg-type]

    assert stats.messages_sent == 1
    assert len(bot.calls) == 1
    method, kwargs = bot.calls[0]
    assert method == "send_message"
    assert kwargs["text"] == "plain text"
    assert "entities" not in kwargs


@pytest.mark.asyncio
async def test_send_text_over_limit_splits_and_drops_entities() -> None:
    # T-53 + report section 6 (known unreachable branch): when text exceeds 4096 the
    # entities-carrying branch is skipped, so split_text sends the chunks WITHOUT
    # entities - the formatting is silently lost. Prod caps stored text at 4096, so this
    # branch never fires in production; the test DOCUMENTS the loss rather than hiding it.
    bot = FakeBot()
    text = "x" * 9000  # no spaces/newlines -> hard cut at 4096: [4096, 4096, 808]
    entities_json = '[{"type": "bold", "offset": 0, "length": 5}]'
    stats = await send_text(bot=bot, chat_id=-100, text=text, entities_json=entities_json)  # type: ignore[arg-type]

    assert stats.messages_sent == 3
    assert len(bot.calls) == 3
    assert all(method == "send_message" for method, _ in bot.calls)
    assert all("entities" not in kwargs for _, kwargs in bot.calls)  # entities dropped on split
    assert "".join(kwargs["text"] for _, kwargs in bot.calls) == text


def test_load_entities_raises_invalid_entities_error_on_bad_json() -> None:
    # T-11: malformed entities_json is deterministic; surface it as a permanent error.
    with pytest.raises(InvalidEntitiesError):
        _load_entities("not json")


def test_load_entities_raises_invalid_entities_error_on_bad_structure() -> None:
    with pytest.raises(InvalidEntitiesError):
        _load_entities('[{"type": "bold"}]')  # MessageEntity needs offset/length
