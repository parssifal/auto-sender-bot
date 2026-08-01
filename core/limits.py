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

# Active (un-sent) scheduled posts: status in ('pending', 'sending').
MAX_ACTIVE_POSTS_PER_USER = 200
MAX_DESTINATIONS_PER_USER = 50
MAX_DRAFTS_PER_USER = 100
MAX_RECURRING_PER_USER = 50


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
