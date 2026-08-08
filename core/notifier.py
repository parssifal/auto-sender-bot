from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo, MessageEntity
from pydantic import ValidationError

from core.utils import split_text


class InvalidEntitiesError(ValueError):
    """entities_json that cannot be parsed - a permanent error retrying will never fix."""


def _load_entities(entities_json: str | None) -> list[MessageEntity] | None:
    if not entities_json:
        return None
    try:
        data = json.loads(entities_json)
        return [MessageEntity.model_validate(item) for item in data]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise InvalidEntitiesError(str(exc)) from exc


@dataclass(frozen=True)
class SendStats:
    messages_sent: int
    message_ids: tuple[int, ...] = ()


async def send_text(
    bot: Bot,
    chat_id: int,
    text: str,
    entities_json: str | None,
) -> SendStats:
    if len(text) <= 4096 and entities_json:
        entities = _load_entities(entities_json)
        await bot.send_message(chat_id=chat_id, text=text, entities=entities)
        return SendStats(messages_sent=1)

    chunks = split_text(text, max_len=4096)
    for chunk in chunks:
        await bot.send_message(chat_id=chat_id, text=chunk)
    return SendStats(messages_sent=len(chunks))


async def send_media_post(
    bot: Bot,
    chat_id: int,
    media_items: list[dict[str, str]],
    caption: str | None,
    caption_entities_json: str | None,
    caption_above: bool | None,
) -> SendStats:
    caption_above_value = bool(caption_above) if caption_above is not None else False
    caption_text = (caption or "").strip()
    caption_entities = _load_entities(caption_entities_json) if caption_entities_json else None

    # If caption too long, send it separately as text (ordering respects above/below).
    send_caption_separately = bool(caption_text) and len(caption_text) > 1024

    message_ids: list[int] = []

    def _track(sent: Any) -> None:
        message_id = getattr(sent, "message_id", None)
        if message_id is not None:
            message_ids.append(int(message_id))

    async def _send_caption_plain() -> int:
        if not caption_text:
            return 0
        chunks = split_text(caption_text, max_len=4096)
        for chunk in chunks:
            _track(await bot.send_message(chat_id=chat_id, text=chunk))
        return len(chunks)

    messages_sent = 0
    if send_caption_separately and caption_above_value:
        messages_sent += await _send_caption_plain()

    if len(media_items) == 1:
        item = media_items[0]
        media_type = item["type"]
        file_id = item["file_id"]
        if media_type == "photo":
            _track(await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=None if send_caption_separately else caption_text or None,
                caption_entities=None if send_caption_separately else caption_entities,
                show_caption_above_media=caption_above_value,
            ))
            messages_sent += 1
        elif media_type == "video":
            _track(await bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=None if send_caption_separately else caption_text or None,
                caption_entities=None if send_caption_separately else caption_entities,
                show_caption_above_media=caption_above_value,
                supports_streaming=True,
            ))
            messages_sent += 1
        else:
            raise ValueError(f"Unsupported media type: {media_type}")
    else:
        media: list[Any] = []
        for idx, item in enumerate(media_items):
            media_type = item["type"]
            file_id = item["file_id"]
            common_kwargs: dict[str, Any] = {}
            if caption_text and not send_caption_separately:
                # Telegram media groups require this flag to be identical on every item.
                common_kwargs["show_caption_above_media"] = caption_above_value
            if idx == 0 and (caption_text and not send_caption_separately):
                common_kwargs = {
                    "show_caption_above_media": caption_above_value,
                    "caption": caption_text,
                    "caption_entities": caption_entities,
                }
            if media_type == "photo":
                media.append(InputMediaPhoto(media=file_id, **common_kwargs))
            elif media_type == "video":
                media.append(InputMediaVideo(media=file_id, **common_kwargs, supports_streaming=True))
            else:
                raise ValueError(f"Unsupported media type: {media_type}")

        group_messages = await bot.send_media_group(chat_id=chat_id, media=media)
        if isinstance(group_messages, (list, tuple)):
            for sent in group_messages:
                _track(sent)
        messages_sent += 1

    if send_caption_separately and not caption_above_value:
        messages_sent += await _send_caption_plain()

    return SendStats(messages_sent=messages_sent, message_ids=tuple(message_ids))
