"""Seed-reaction palettes and per-plan validation (single source of truth).

The bot reacts to its own just-published message so the post carries emoji
"seeds" (social proof), one count each — see ``core/scheduler.py``. What a user
may seed is gated by plan here, and enforced server-side (never trust the
client): the allowed *palette* widens by tier and the *count* is capped by
``limit_for(plan, "reactions")`` (basic 1 / pro 2 / premium 3).
"""
from __future__ import annotations

import core.limits as limits

# "Wider already in basic": basic still gets a real choice of three; pro an
# extended set; premium anything the channel's ``available_reactions`` allows
# (validated by Telegram at send time, not here).
BASIC_PALETTE: tuple[str, ...] = ("👍", "❤️", "🔥")
PRO_PALETTE: tuple[str, ...] = ("👍", "❤️", "🔥", "😂", "🙏", "👏", "🎉")


def palette_for(plan: str) -> tuple[str, ...] | None:
    """Allowed emoji for ``plan``; ``None`` means unrestricted (premium)."""
    if plan == "premium":
        return None
    if plan == "pro":
        return PRO_PALETTE
    return BASIC_PALETTE


def presets_allowed(plan: str) -> bool:
    return plan in ("pro", "premium")


class ReactionValidationError(ValueError):
    """A user emoji selection violates the plan's palette or count cap.

    ``reason`` is a stable key the web layer maps to a localized message.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def sanitize_emojis(plan: str, emojis: list[str]) -> list[str]:
    """Validate a user-supplied emoji list for ``plan``.

    De-dupes preserving order, rejects any emoji outside the plan's palette
    (premium excepted), and rejects a selection larger than the plan's count
    cap. Returns the cleaned list (possibly empty = no seed reactions).
    """
    cap = limits.limit_for(plan, "reactions")
    palette = palette_for(plan)
    cleaned: list[str] = []
    for raw in emojis:
        emoji = str(raw)
        if emoji in cleaned:
            continue
        if palette is not None and emoji not in palette:
            raise ReactionValidationError("emoji_not_allowed")
        cleaned.append(emoji)
    if len(cleaned) > cap:
        raise ReactionValidationError("too_many_reactions")
    return cleaned


if __name__ == "__main__":
    # Self-check: palette membership + count cap, per plan.
    assert sanitize_emojis("basic", ["👍"]) == ["👍"]
    assert sanitize_emojis("basic", ["👍", "👍"]) == ["👍"]  # de-dupe
    try:
        sanitize_emojis("basic", ["👍", "❤️"])  # over basic cap of 1
        raise AssertionError("expected too_many_reactions")
    except ReactionValidationError as e:
        assert e.reason == "too_many_reactions"
    try:
        sanitize_emojis("basic", ["😂"])  # not in basic palette
        raise AssertionError("expected emoji_not_allowed")
    except ReactionValidationError as e:
        assert e.reason == "emoji_not_allowed"
    assert sanitize_emojis("pro", ["👍", "😂"]) == ["👍", "😂"]
    try:
        sanitize_emojis("pro", ["👍", "😂", "🔥"])  # over pro cap of 2
        raise AssertionError("expected too_many_reactions")
    except ReactionValidationError as e:
        assert e.reason == "too_many_reactions"
    assert sanitize_emojis("premium", ["🦄", "🎃", "🥑"]) == ["🦄", "🎃", "🥑"]  # any emoji, cap 3
    print("core.reactions self-check OK")
