from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from core.notifier import send_media_post, send_text
from core.state import RecurringPattern, ScheduledPostRow, StateStore

logger = logging.getLogger(__name__)
_WEEKDAYS_DEFAULT_MASK = 0b0011111


def _compute_backoff_seconds(attempt: int) -> int:
    # attempt starts at 1
    base = [5, 15, 60, 300, 900, 3600]
    idx = min(max(attempt - 1, 0), len(base) - 1)
    jitter = random.randint(0, 3)
    return base[idx] + jitter


async def _user_is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    return member.status in {"creator", "administrator"}


async def _bot_can_post(bot: Bot, chat_id: int) -> bool:
    me = await bot.me()
    member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    if member.status != "administrator":
        return False
    can_post = getattr(member, "can_post_messages", None)
    if can_post is None:
        return True
    return bool(can_post)


def _build_local_datetime(tz_name: str, day: date, minutes_of_day: int) -> datetime:
    tz = ZoneInfo(tz_name)
    hour, minute = divmod(minutes_of_day, 60)
    return datetime.combine(day, clock_time(hour=hour, minute=minute), tzinfo=tz)


def _weekday_allowed(mask: int, weekday: int) -> bool:
    return bool(mask & (1 << weekday))


def calculate_next_occurrence(pattern: RecurringPattern, last_timestamp: int) -> int:
    local_last = datetime.fromtimestamp(last_timestamp, tz=timezone.utc).astimezone(ZoneInfo(pattern.timezone))
    next_day = local_last.date()

    if pattern.interval_type == "daily":
        next_day += timedelta(days=1)
    elif pattern.interval_type == "weekly":
        next_day += timedelta(days=7)
    elif pattern.interval_type == "weekdays":
        allowed_mask = pattern.weekdays_mask if pattern.weekdays_mask is not None else _WEEKDAYS_DEFAULT_MASK
        while True:
            next_day += timedelta(days=1)
            if _weekday_allowed(allowed_mask, next_day.weekday()):
                break
    else:
        raise ValueError(f"Unknown recurring interval: {pattern.interval_type}")

    return int(_build_local_datetime(pattern.timezone, next_day, pattern.time_of_day_minutes).timestamp())


async def _materialize_next_recurring_post(store: StateStore, post: ScheduledPostRow) -> None:
    instance = await store.get_recurring_instance_by_post_id(post.id)
    if instance is None:
        return

    pattern = await store.get_recurring_pattern(instance.pattern_id)
    if pattern is None or not pattern.is_active:
        return

    current_ordinal = max(pattern.current_count, instance.ordinal)
    next_ordinal = current_ordinal + 1
    if pattern.max_occurrences is not None and next_ordinal > pattern.max_occurrences:
        await store.delete_recurring_pattern(pattern.id)
        logger.info("Recurring pattern %s completed after ordinal %s", pattern.id, current_ordinal)
        return

    next_scheduled_for_utc = calculate_next_occurrence(pattern, instance.scheduled_for_utc)
    if pattern.end_at_utc is not None and next_scheduled_for_utc > pattern.end_at_utc:
        await store.delete_recurring_pattern(pattern.id)
        logger.info("Recurring pattern %s completed at end_at=%s", pattern.id, pattern.end_at_utc)
        return

    next_instance = await store.materialize_next_recurring_post(
        pattern_id=pattern.id,
        source_post_id=post.id,
        next_ordinal=next_ordinal,
        scheduled_for_utc=next_scheduled_for_utc,
    )
    if next_instance is None:
        logger.info("Recurring pattern %s became inactive before materialization", pattern.id)
        return

    logger.info(
        "Materialized recurring instance %s for pattern %s at %s",
        next_instance.ordinal,
        pattern.id,
        next_instance.scheduled_for_utc,
    )


async def scheduler_loop(
    bot: Bot,
    store: StateStore,
    stop_event: asyncio.Event,
    poll_interval_seconds: float = 2.0,
) -> None:
    logger.info("Scheduler started (poll_interval=%.2fs)", poll_interval_seconds)
    while not stop_event.is_set():
        now_utc = int(time.time())
        try:
            due = await store.list_due_posts(now_utc=now_utc, limit=10)
            for post in due:
                await _process_due_post(bot=bot, store=store, post=post, now_utc=now_utc)
        except Exception:
            logger.exception("Scheduler tick failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            continue

    logger.info("Scheduler stopped")


async def _process_due_post(bot: Bot, store: StateStore, post: ScheduledPostRow, now_utc: int) -> None:
    if post.status != "pending":
        return
    claimed = await store.claim_post_for_sending(post_id=post.id, now_utc=now_utc)
    if not claimed:
        return

    sent_at_utc = int(time.time())
    try:
        if not await _user_is_admin(bot, chat_id=post.chat_id, user_id=post.user_id):
            await store.mark_failed(post_id=post.id, error="User is not admin anymore")
            logger.warning("Post %s failed: user %s is not admin in chat %s", post.id, post.user_id, post.chat_id)
            return
        if not await _bot_can_post(bot, chat_id=post.chat_id):
            await store.mark_failed(post_id=post.id, error="Bot cannot post to destination")
            logger.warning("Post %s failed: bot cannot post to chat %s", post.id, post.chat_id)
            return

        if post.kind == "text":
            await send_text(bot=bot, chat_id=post.chat_id, text=post.text or "", entities_json=post.entities_json)
        elif post.kind == "media":
            media = await store.get_post_media(post.id)
            await send_media_post(
                bot=bot,
                chat_id=post.chat_id,
                media_items=media,
                caption=post.caption,
                caption_entities_json=post.caption_entities_json,
                caption_above=None if post.caption_above is None else bool(int(post.caption_above)),
            )
        else:
            raise ValueError(f"Unknown post kind: {post.kind}")

        await store.mark_sent(post_id=post.id, sent_at_utc=sent_at_utc)
        try:
            await _materialize_next_recurring_post(store=store, post=post)
        except Exception:
            logger.exception("Sent post %s but failed to materialize next recurring occurrence", post.id)
        logger.info("Sent post %s to chat %s", post.id, post.chat_id)
    except TelegramRetryAfter as exc:
        next_retry_at = int(time.time()) + int(getattr(exc, "retry_after", 1)) + 1
        await store.mark_retry(post_id=post.id, next_retry_at_utc=next_retry_at, error=f"retry_after: {exc}")
        logger.warning("RetryAfter for post %s: %s", post.id, exc)
    except (TelegramNetworkError,) as exc:
        next_retry_at = int(time.time()) + _compute_backoff_seconds(post.attempts + 1)
        await store.mark_retry(post_id=post.id, next_retry_at_utc=next_retry_at, error=str(exc))
        logger.warning("Network error for post %s: %s", post.id, exc)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        await store.mark_failed(post_id=post.id, error=str(exc))
        logger.warning("Permanent Telegram error for post %s: %s", post.id, exc)
    except TelegramAPIError as exc:
        next_retry_at = int(time.time()) + _compute_backoff_seconds(post.attempts + 1)
        await store.mark_retry(post_id=post.id, next_retry_at_utc=next_retry_at, error=str(exc))
        logger.warning("Telegram API error for post %s: %s", post.id, exc)
    except Exception as exc:
        next_retry_at = int(time.time()) + _compute_backoff_seconds(post.attempts + 1)
        await store.mark_retry(post_id=post.id, next_retry_at_utc=next_retry_at, error=str(exc))
        logger.exception("Unexpected error sending post %s", post.id)
