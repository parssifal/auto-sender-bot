from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import ReactionTypeEmoji
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from core.limits import ResourceLimitError
from core.notifier import InvalidEntitiesError, send_media_post, send_text
from core.state import RecurringPattern, ScheduledPostRow, StateStore

logger = logging.getLogger(__name__)
_WEEKDAYS_DEFAULT_MASK = 0b0011111


def _compute_backoff_seconds(attempt: int) -> int:
    # attempt starts at 1
    base = [5, 15, 60, 300, 900, 3600]
    idx = min(max(attempt - 1, 0), len(base) - 1)
    jitter = random.randint(0, 3)
    return base[idx] + jitter


# After this many send attempts a post that still fails is marked 'failed' instead of
# retried forever (which would hold an active-posts quota slot indefinitely). Six matches
# the backoff ladder above: each tier is used once, before the repeating 3600s cap.
MAX_SEND_ATTEMPTS = 6


@dataclass
class SchedulerMetrics:
    last_tick_started_at: int | None = None
    last_tick_finished_at: int | None = None
    last_error: str | None = None
    last_due_count: int = 0


async def _mark_retry_or_failed(store: StateStore, post: ScheduledPostRow, *, next_retry_at_utc: int, error: str) -> None:
    # post.attempts is the count BEFORE claim_post_for_sending incremented it for this
    # attempt, so the current attempt number (and the DB value now) is post.attempts + 1.
    if post.attempts + 1 >= MAX_SEND_ATTEMPTS:
        await _mark_failed_and_notify_author(store=store, bot=None, post=post, error=error)
    else:
        await store.mark_retry(post_id=post.id, next_retry_at_utc=next_retry_at_utc, error=error)


async def _mark_retry_or_failed_with_bot(
    store: StateStore,
    bot: Bot,
    post: ScheduledPostRow,
    *,
    next_retry_at_utc: int,
    error: str,
) -> None:
    if post.attempts + 1 >= MAX_SEND_ATTEMPTS:
        await _mark_failed_and_notify_author(store=store, bot=bot, post=post, error=error)
    else:
        await store.mark_retry(post_id=post.id, next_retry_at_utc=next_retry_at_utc, error=error)


def _failure_notice_text(*, post: ScheduledPostRow, destination_title: str, error: str) -> str:
    return (
        "Scheduled post failed.\n"
        f"- id: {post.id[:8]}\n"
        f"- Destination: {destination_title}\n"
        f"- Reason: {error}"
    )


async def _mark_failed_and_notify_author(
    *,
    store: StateStore,
    bot: Bot | None,
    post: ScheduledPostRow,
    error: str,
) -> None:
    await store.mark_failed(post_id=post.id, error=error)
    if bot is None:
        return
    destination = await store.get_destination_title(post.chat_id) or str(post.chat_id)
    try:
        await bot.send_message(
            chat_id=post.user_id,
            text=_failure_notice_text(post=post, destination_title=destination, error=error),
        )
    except Exception:
        logger.info("Could not DM failure notice for post %s to user %s", post.id, post.user_id)


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


async def _end_recurring_series(store: StateStore, pattern_id: str, post: ScheduledPostRow, sent_at_utc: int) -> None:
    # Deactivate first: the post is not 'sent' yet, so a crash here replays the
    # send instead of stranding an active pattern with no successor.
    await store.delete_recurring_pattern(pattern_id)
    await store.mark_sent(post_id=post.id, sent_at_utc=sent_at_utc)


async def _mark_sent_with_next_recurring(
    store: StateStore,
    post: ScheduledPostRow,
    now_utc: int,
    sent_at_utc: int,
) -> None:
    instance = await store.get_recurring_instance_by_post_id(post.id)
    pattern = None if instance is None else await store.get_recurring_pattern(instance.pattern_id)
    if instance is None or pattern is None or not pattern.is_active:
        await store.mark_sent(post_id=post.id, sent_at_utc=sent_at_utc)
        return

    current_ordinal = max(pattern.current_count, instance.ordinal)
    next_ordinal = current_ordinal + 1
    next_scheduled_for_utc = calculate_next_occurrence(pattern, instance.scheduled_for_utc)
    # Downtime must not fire a burst: skip every occurrence that is already past
    # and count it as consumed, so a pause cannot stretch the series either.
    while next_scheduled_for_utc <= now_utc:
        next_ordinal += 1
        next_scheduled_for_utc = calculate_next_occurrence(pattern, next_scheduled_for_utc)

    if pattern.max_occurrences is not None and next_ordinal > pattern.max_occurrences:
        await _end_recurring_series(store, pattern.id, post, sent_at_utc)
        logger.info("Recurring pattern %s completed after ordinal %s", pattern.id, next_ordinal - 1)
        return

    if pattern.end_at_utc is not None and next_scheduled_for_utc > pattern.end_at_utc:
        await _end_recurring_series(store, pattern.id, post, sent_at_utc)
        logger.info("Recurring pattern %s completed at end_at=%s", pattern.id, pattern.end_at_utc)
        return

    try:
        next_instance = await store.mark_sent_and_materialize_next(
            post_id=post.id,
            sent_at_utc=sent_at_utc,
            pattern_id=pattern.id,
            next_ordinal=next_ordinal,
            scheduled_for_utc=next_scheduled_for_utc,
        )
    except ResourceLimitError:
        # Background work: nobody is waiting for this error. End the series
        # visibly instead of leaving an active pattern that never moves again.
        await _end_recurring_series(store, pattern.id, post, sent_at_utc)
        logger.warning("Recurring pattern %s stopped: user %s is at the active-posts cap", pattern.id, post.user_id)
        return
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
    metrics: SchedulerMetrics | None = None,
) -> None:
    logger.info("Scheduler started (poll_interval=%.2fs)", poll_interval_seconds)
    while not stop_event.is_set():
        now_utc = int(time.time())
        if metrics is not None:
            metrics.last_tick_started_at = now_utc
        try:
            due = await store.list_due_posts(now_utc=now_utc, limit=10)
            if metrics is not None:
                metrics.last_due_count = len(due)
            for post in due:
                await _process_due_post(bot=bot, store=store, post=post, now_utc=now_utc)
            if metrics is not None:
                metrics.last_tick_finished_at = int(time.time())
                metrics.last_error = None
        except Exception:
            if metrics is not None:
                metrics.last_error = "Scheduler tick failed"
            logger.exception("Scheduler tick failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            continue

    logger.info("Scheduler stopped")


async def _seed_reactions(bot: Bot, post: ScheduledPostRow, message_ids: tuple[int, ...]) -> None:
    """Seed the post's emoji reactions on its first message. Best-effort.

    Two things only a live channel can confirm — the real per-call emoji ceiling
    and whether the chosen emoji are in the channel's ``available_reactions`` —
    mean any error here is expected in the wild. The post is already sent, so we
    log and return; never let this raise back into the send path.
    """
    raw = post.reaction_emojis_json
    if not raw or not message_ids:
        return
    try:
        emojis = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Post %s has unparseable reaction_emojis_json", post.id)
        return
    if not emojis:
        return
    try:
        await bot.set_message_reaction(
            chat_id=post.chat_id,
            message_id=message_ids[0],
            reaction=[ReactionTypeEmoji(emoji=str(e)) for e in emojis],
        )
    except Exception:
        logger.warning("Failed to seed reactions on post %s (chat %s)", post.id, post.chat_id, exc_info=True)


def _referral_bonus_text(days: int) -> str:
    # Inline RU string (not i18n): core must not import telegram (see webapp.py),
    # matching the _failure_notice_text precedent for scheduler-side DMs.
    return (
        f"🎁 Реферальный бонус: вам начислено +{days} дней тарифа Pro.\n"
        "Спасибо, что приглашаете друзей!"
    )


async def _notify_referral_bonus(bot: Bot, *, user_id: int, days: int) -> None:
    if days <= 0:  # nothing granted (already at cap or on a better plan)
        return
    try:
        await bot.send_message(chat_id=user_id, text=_referral_bonus_text(days))
    except Exception:
        logger.info("Could not DM referral bonus notice to user %s", user_id)


async def _maybe_grant_referral_bonus(bot: Bot, store: StateStore, post: ScheduledPostRow) -> None:
    """Pay the referral bonus on the author's first delivered post. Best-effort.

    The post is already sent and marked; referral bookkeeping is gravy that must
    never fail it, so every error — grant or notify — is swallowed here. Idempotent
    at the store level, so re-calling on later posts of a recurring series no-ops.
    """
    try:
        result = await store.grant_referral_bonus(referee_id=post.user_id, now=int(time.time()))
    except Exception:
        logger.warning("Referral bonus grant failed for user %s (post %s)", post.user_id, post.id, exc_info=True)
        return
    if result is None:
        return
    await _notify_referral_bonus(bot, user_id=post.user_id, days=result.referee_days)
    await _notify_referral_bonus(bot, user_id=result.referrer_id, days=result.referrer_days)


async def _process_due_post(bot: Bot, store: StateStore, post: ScheduledPostRow, now_utc: int) -> None:
    if post.status != "pending":
        return
    claimed = await store.claim_post_for_sending(post_id=post.id, now_utc=now_utc)
    if not claimed:
        return

    sent_at_utc = int(time.time())
    try:
        if not await _user_is_admin(bot, chat_id=post.chat_id, user_id=post.user_id):
            await _mark_failed_and_notify_author(
                store=store,
                bot=bot,
                post=post,
                error="User is not admin anymore",
            )
            logger.warning("Post %s failed: user %s is not admin in chat %s", post.id, post.user_id, post.chat_id)
            return
        if not await _bot_can_post(bot, chat_id=post.chat_id):
            await _mark_failed_and_notify_author(
                store=store,
                bot=bot,
                post=post,
                error="Bot cannot post to destination",
            )
            logger.warning("Post %s failed: bot cannot post to chat %s", post.id, post.chat_id)
            return

        if post.kind == "text":
            stats = await send_text(bot=bot, chat_id=post.chat_id, text=post.text or "", entities_json=post.entities_json)
        elif post.kind == "media":
            media = await store.get_post_media(post.id)
            stats = await send_media_post(
                bot=bot,
                chat_id=post.chat_id,
                media_items=media,
                caption=post.caption,
                caption_entities_json=post.caption_entities_json,
                caption_above=None if post.caption_above is None else bool(int(post.caption_above)),
            )
        else:
            raise ValueError(f"Unknown post kind: {post.kind}")

        await _mark_sent_with_next_recurring(store=store, post=post, now_utc=now_utc, sent_at_utc=sent_at_utc)
        logger.info("Sent post %s to chat %s", post.id, post.chat_id)
        # Reactions are pure social-proof gravy: the post is already delivered and
        # marked sent, so a reaction failure must never fail it. _seed_reactions
        # swallows and logs everything internally.
        await _seed_reactions(bot=bot, post=post, message_ids=stats.message_ids)
        # Referral activation: the first delivered post pays both parties +7d Pro.
        # Best-effort and idempotent — never fails the already-delivered post.
        await _maybe_grant_referral_bonus(bot=bot, store=store, post=post)
    except TelegramRetryAfter as exc:
        next_retry_at = int(time.time()) + int(getattr(exc, "retry_after", 1)) + 1
        await _mark_retry_or_failed_with_bot(
            store,
            bot,
            post,
            next_retry_at_utc=next_retry_at,
            error=f"retry_after: {exc}",
        )
        logger.warning("RetryAfter for post %s: %s", post.id, exc)
    except (TelegramNetworkError,) as exc:
        next_retry_at = int(time.time()) + _compute_backoff_seconds(post.attempts + 1)
        await _mark_retry_or_failed_with_bot(store, bot, post, next_retry_at_utc=next_retry_at, error=str(exc))
        logger.warning("Network error for post %s: %s", post.id, exc)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        await _mark_failed_and_notify_author(store=store, bot=bot, post=post, error=str(exc))
        logger.warning("Permanent Telegram error for post %s: %s", post.id, exc)
    except TelegramAPIError as exc:
        next_retry_at = int(time.time()) + _compute_backoff_seconds(post.attempts + 1)
        await _mark_retry_or_failed_with_bot(store, bot, post, next_retry_at_utc=next_retry_at, error=str(exc))
        logger.warning("Telegram API error for post %s: %s", post.id, exc)
    except InvalidEntitiesError as exc:
        # Deterministic content error: retrying can never fix it, so fail immediately.
        await _mark_failed_and_notify_author(
            store=store,
            bot=bot,
            post=post,
            error=f"invalid entities: {exc}",
        )
        logger.warning("Post %s failed: invalid entities_json - %s", post.id, exc)
    except Exception as exc:
        next_retry_at = int(time.time()) + _compute_backoff_seconds(post.attempts + 1)
        await _mark_retry_or_failed_with_bot(store, bot, post, next_retry_at_utc=next_retry_at, error=str(exc))
        logger.exception("Unexpected error sending post %s", post.id)
