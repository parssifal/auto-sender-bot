"""StateStore facade: composes domain mixins into a single public class.

Public surface (class name, import path, method names/signatures, and the
re-exported dataclasses) is preserved from the original monolithic
``core/state.py`` module. Callers should keep doing:

    from core.state import StateStore
    from core.state import Destination, Team, ...  # dataclasses

Internally the implementation is split by domain into sibling modules
(``users.py``, ``destinations.py``, ``posts.py``, ``recurring.py``,
``drafts.py``, ``teams.py``, ``stats.py``) plus shared infrastructure
(``base.py``, ``row_mappers.py``, ``models.py``). Domains not yet split
out of the monolith temporarily live in ``_legacy.py``.
"""

from __future__ import annotations

# NOTE: imported (not just for re-export) so that
# ``monkeypatch.setattr("core.state.time.time", ...)`` in
# tests/test_state_drafts.py can resolve "core.state.time" via attribute
# lookup on this package. Since it's the same singleton `time` module
# used by every domain submodule's own `import time`, patching it here
# patches it everywhere.
import time

from core.state._legacy import _LegacyMixin
from core.state.base import StateStoreBase
from core.state.models import (
    Destination,
    DraftRow,
    RecurringInstance,
    RecurringPattern,
    RecurringPatternSummary,
    ScheduledPostRow,
    Team,
    TeamInvite,
    TeamInviteAcceptance,
    TeamMember,
)
from core.state.row_mappers import RowMappersMixin
from core.state.users import UsersMixin

__all__ = [
    "StateStore",
    "Destination",
    "Team",
    "TeamMember",
    "TeamInvite",
    "TeamInviteAcceptance",
    "DraftRow",
    "ScheduledPostRow",
    "RecurringPattern",
    "RecurringInstance",
    "RecurringPatternSummary",
    "time",
]


class StateStore(StateStoreBase, UsersMixin, _LegacyMixin, RowMappersMixin):
    pass
