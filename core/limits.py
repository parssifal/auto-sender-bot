"""Per-user resource caps (anti-abuse / self-DoS protection).

A single authenticated user must not be able to grow the database without bound
(unbounded scheduled posts / drafts / destinations / recurring patterns would
exhaust disk and load the scheduler). These caps are enforced at the StateStore
creation choke points — the one place every code path funnels through — so a
missed handler cannot silently bypass them. Handlers catch ``ResourceLimitError``
to show the user a friendly, localized message.

Thresholds are referenced via ``core.limits.<NAME>`` (module attribute, not a
value import) so tests can monkeypatch them to small numbers.
"""
from __future__ import annotations

# Global hard ceilings (absolute anti-DoS cap, independent of plan). The
# effective per-user cap is ``min(plan allowance, ceiling)`` — see
# ``limit_for``. They are kept at the most generous plan (premium) so a normal
# plan is never throttled by them; tests monkeypatch these to small numbers to
# exercise cap enforcement without provisioning a plan.
# Active (un-sent) scheduled posts: status in ('pending', 'sending').
MAX_ACTIVE_POSTS_PER_USER = 1000
MAX_DESTINATIONS_PER_USER = 100
MAX_DRAFTS_PER_USER = 500
MAX_RECURRING_PER_USER = 200

# Per-plan allowances (posts/destinations/drafts/recurring/reactions).
# ``reactions`` is unused in Phase 1 (reactions ship in Phase 2) but carried
# here so the tier table is the single source of truth.
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "basic":   {"posts": 100,  "destinations": 5,   "drafts": 50,  "recurring": 10,  "reactions": 1},
    "pro":     {"posts": 300,  "destinations": 20,  "drafts": 150, "recurring": 50,  "reactions": 2},
    "premium": {"posts": 1000, "destinations": 100, "drafts": 500, "recurring": 200, "reactions": 3},
}

# Referral bonus (Phase 3): activation (referee's first delivered post) grants
# both referrer and referee this many days of Pro. REFERRAL_BONUS_CAP_DAYS is the
# per-user lifetime ceiling on accumulated referral-bonus days (anti-farm).
REFERRAL_BONUS_DAYS = 7
REFERRAL_BONUS_CAP_DAYS = 90

# Resource -> module attribute holding its global ceiling. Resources without an
# entry (e.g. "reactions") have no separate ceiling and use the plan value as-is.
_CEILING_ATTR = {
    "posts": "MAX_ACTIVE_POSTS_PER_USER",
    "destinations": "MAX_DESTINATIONS_PER_USER",
    "drafts": "MAX_DRAFTS_PER_USER",
    "recurring": "MAX_RECURRING_PER_USER",
}


def limit_for(plan: str, resource: str) -> int:
    """Effective per-user cap for ``resource`` under ``plan``.

    The plan's allowance, clamped to the global hard ceiling (read live from the
    module attribute so tests can monkeypatch ``MAX_*`` to small numbers). An
    unknown plan falls back to ``basic``.
    """
    allowance = PLAN_LIMITS.get(plan, PLAN_LIMITS["basic"])[resource]
    ceiling_attr = _CEILING_ATTR.get(resource)
    if ceiling_attr is None:
        return allowance
    return min(allowance, globals()[ceiling_attr])


class ResourceLimitError(Exception):
    """Raised by StateStore create methods when a per-user cap is reached.

    ``resource`` is a stable key (``"posts"``, ``"drafts"``, ``"destinations"``,
    ``"recurring"``) that handlers map to a localized message; ``limit`` is the
    threshold that was hit.
    """

    def __init__(self, resource: str, limit: int) -> None:
        super().__init__(f"{resource} limit reached ({limit})")
        self.resource = resource
        self.limit = limit
