from __future__ import annotations

import time
from dataclasses import dataclass

from core.limits import REFERRAL_BONUS_CAP_DAYS, REFERRAL_BONUS_DAYS
from core.state.base import locked_write

_DAY_SECONDS = 86400


@dataclass(frozen=True)
class ReferralBonusResult:
    """Outcome of a referral activation payout (see ``grant_referral_bonus``).

    ``referrer_id`` is who invited the activated referee. ``referee_days`` /
    ``referrer_days`` are the Pro days actually granted to each (0 when that
    recipient was already at the 90-day cap or on a better plan).
    """

    referrer_id: int
    referee_days: int
    referrer_days: int


class UsersMixin:
    @locked_write
    async def ensure_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO users(user_id, timezone, language, username, first_name, created_at, updated_at)
            VALUES(?, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name)
            """,
            (user_id, username, first_name, now, now),
        )
        await self._conn.commit()

    async def get_user_timezone(self, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT timezone FROM users WHERE user_id=?",
            (user_id,),
        )
        return None if row is None else row["timezone"]

    @locked_write
    async def set_user_timezone(self, user_id: int, tz_name: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            "UPDATE users SET timezone=?, updated_at=? WHERE user_id=?",
            (tz_name, now, user_id),
        )
        await self._conn.commit()

    async def get_user_language(self, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT language FROM users WHERE user_id=?",
            (user_id,),
        )
        return None if row is None else row["language"]

    @locked_write
    async def set_user_language(self, user_id: int, language: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            "UPDATE users SET language=?, updated_at=? WHERE user_id=?",
            (language, now, user_id),
        )
        await self._conn.commit()

    async def all_user_ids(self) -> list[int]:
        rows = await self._conn.execute_fetchall("SELECT user_id FROM users ORDER BY user_id")
        return [int(r["user_id"]) for r in rows]

    async def get_user_plan(self, user_id: int) -> str:
        """Effective plan tier: ``basic``/``pro``/``premium``.

        Lazy expiry (no background job): a ``plan_expires_at`` in the past reverts
        the user to ``basic`` on read. Unknown/missing user also reads ``basic``.
        """
        row = await self._execute_fetchone(
            "SELECT plan, plan_expires_at FROM users WHERE user_id=?",
            (user_id,),
        )
        if row is None or not row["plan"]:
            return "basic"
        expires_at = row["plan_expires_at"]
        if expires_at is not None and int(expires_at) <= int(time.time()):
            return "basic"
        return str(row["plan"])

    @locked_write
    async def set_user_plan(self, user_id: int, plan: str, expires_at: int | None) -> bool:
        """Set the user's plan tier and optional expiry (NULL = indefinite).

        Returns False if the user does not exist (no row updated). Caller
        validates ``plan``.
        """
        # TODO(payments): a paid upgrade flow would call this after capturing
        # payment; today it is admin-only manual grant.
        now = int(time.time())
        cur = await self._conn.execute(
            "UPDATE users SET plan=?, plan_expires_at=?, updated_at=? WHERE user_id=?",
            (plan, expires_at, now, user_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    # --- Referrals (Phase 3) ---

    @locked_write
    async def capture_referral(self, referee_id: int, referrer_id: int) -> bool:
        """Record who referred ``referee_id`` — exactly once, at first /start.

        Anti-abuse gates, all required (returns True only if a referrer was
        recorded): no self-referral; the referee row must be brand-new
        (``created_at == updated_at`` — cmd_start's ``ensure_user`` just inserted
        it; a returning user's ensure_user would have advanced ``updated_at``);
        ``referred_by`` still NULL; and the referrer must already exist. The
        ``referred_by IS NULL`` guard in the UPDATE closes the last write race.
        """
        if referrer_id == referee_id:
            return False
        row = await self._execute_fetchone(
            "SELECT referred_by, created_at, updated_at FROM users WHERE user_id=?",
            (referee_id,),
        )
        if row is None or row["referred_by"] is not None:
            return False
        if int(row["created_at"]) != int(row["updated_at"]):
            return False
        referrer = await self._execute_fetchone(
            "SELECT 1 FROM users WHERE user_id=?", (referrer_id,)
        )
        if referrer is None:
            return False
        cur = await self._conn.execute(
            "UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL",
            (referrer_id, referee_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def _apply_pro_bonus(self, user_id: int, now: int) -> int:
        """Extend ``user_id`` by up to ``REFERRAL_BONUS_DAYS`` of Pro, cap-limited.

        Only ever raises standing, never lowers it: a recipient already on
        (effective) premium, or on indefinite pro, is left untouched. A basic /
        expired user is moved to pro from ``now``; a finite-pro user has their
        expiry extended. Increments ``referral_bonus_days`` by the days granted
        and stops at the 90-day lifetime cap. Returns the days actually granted
        (0 when skipped). No commit — the caller owns the transaction.
        """
        row = await self._execute_fetchone(
            "SELECT plan, plan_expires_at, referral_bonus_days FROM users WHERE user_id=?",
            (user_id,),
        )
        if row is None:
            return 0
        granted_so_far = int(row["referral_bonus_days"] or 0)
        days = min(REFERRAL_BONUS_DAYS, REFERRAL_BONUS_CAP_DAYS - granted_so_far)
        if days <= 0:
            return 0
        plan = row["plan"] or "basic"
        expires = None if row["plan_expires_at"] is None else int(row["plan_expires_at"])
        active = plan if (expires is None or expires > now) else "basic"
        if active == "premium":
            return 0  # premium beats the pro bonus — don't downgrade
        if active == "pro" and expires is None:
            return 0  # indefinite pro already beats a finite pro bonus
        base = max(now, expires) if active == "pro" else now
        new_expires = base + days * _DAY_SECONDS
        await self._conn.execute(
            "UPDATE users SET plan='pro', plan_expires_at=?, referral_bonus_days=?, updated_at=? WHERE user_id=?",
            (new_expires, granted_so_far + days, now, user_id),
        )
        return days

    @locked_write
    async def grant_referral_bonus(self, referee_id: int, now: int) -> ReferralBonusResult | None:
        """Pay the referral bonus on the referee's activation (first delivered post).

        Idempotent via ``referral_bonus_granted``: returns None (no writes) when
        the user has no referrer or was already paid out. Otherwise sets the flag
        and grants both the referee and referrer +7 days Pro (cap-limited), all in
        one transaction. Returns what was granted so the caller can notify.
        """
        row = await self._execute_fetchone(
            "SELECT referred_by, referral_bonus_granted FROM users WHERE user_id=?",
            (referee_id,),
        )
        if row is None or row["referred_by"] is None or int(row["referral_bonus_granted"] or 0) == 1:
            return None
        referrer_id = int(row["referred_by"])
        await self._conn.execute(
            "UPDATE users SET referral_bonus_granted=1, updated_at=? WHERE user_id=?",
            (now, referee_id),
        )
        referee_days = await self._apply_pro_bonus(referee_id, now)
        referrer_days = await self._apply_pro_bonus(referrer_id, now)
        await self._conn.commit()
        return ReferralBonusResult(referrer_id=referrer_id, referee_days=referee_days, referrer_days=referrer_days)

    async def get_referral_stats(self, user_id: int) -> dict[str, int]:
        """Admin view: how many users this person referred and how many activated."""
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS referred, "
            "COALESCE(SUM(referral_bonus_granted), 0) AS activated "
            "FROM users WHERE referred_by=?",
            (user_id,),
        )
        return {
            "referred_count": 0 if row is None else int(row["referred"]),
            "referred_activated": 0 if row is None else int(row["activated"]),
        }
