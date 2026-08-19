from __future__ import annotations


class StatsMixin:
    async def count_users(self) -> int:
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM users", ())
        return 0 if row is None else int(row["cnt"])

    async def avg_destinations_per_user(self) -> float:
        users = await self.count_users()
        if users == 0:
            return 0.0
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM user_destinations", ())
        links = 0 if row is None else int(row["cnt"])
        return links / users

    async def count_new_users(self, since_ts: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM users WHERE created_at >= ?",
            (since_ts,),
        )
        return 0 if row is None else int(row["cnt"])

    async def count_active_users(self, since_ts: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM scheduled_posts WHERE created_at >= ?",
            (since_ts,),
        )
        return 0 if row is None else int(row["cnt"])

    async def count_posts_by_status(self) -> dict[str, int]:
        counts = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "cancelled": 0}
        rows = await self._conn.execute_fetchall(
            "SELECT status, COUNT(1) AS cnt FROM scheduled_posts GROUP BY status",
            (),
        )
        for r in rows:
            counts[str(r["status"])] = int(r["cnt"])
        return counts

    async def count_posts_sent_since(self, since_ts: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM scheduled_posts WHERE status='sent' AND sent_at >= ?",
            (since_ts,),
        )
        return 0 if row is None else int(row["cnt"])

    async def language_distribution(self) -> list[tuple[str, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT COALESCE(language, 'unknown') AS lang, COUNT(1) AS cnt
            FROM users
            GROUP BY COALESCE(language, 'unknown')
            ORDER BY cnt DESC, lang ASC
            """,
            (),
        )
        return [(str(r["lang"]), int(r["cnt"])) for r in rows]

    async def count_teams(self) -> int:
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM teams", ())
        return 0 if row is None else int(row["cnt"])

    async def count_drafts(self) -> int:
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM drafts", ())
        return 0 if row is None else int(row["cnt"])

    async def top_active_users(self, limit: int, since_ts: int) -> list[tuple[int, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT user_id, COUNT(1) AS cnt
            FROM scheduled_posts
            WHERE created_at >= ?
            GROUP BY user_id
            ORDER BY cnt DESC, user_id ASC
            LIMIT ?
            """,
            (since_ts, limit),
        )
        return [(int(r["user_id"]), int(r["cnt"])) for r in rows]

    async def list_users(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.language,
                u.created_at,
                u.updated_at AS last_active,
                (SELECT COUNT(1) FROM scheduled_posts sp WHERE sp.user_id = u.user_id) AS posts,
                (SELECT COUNT(1) FROM user_destinations ud WHERE ud.user_id = u.user_id) AS channels
            FROM users u
            ORDER BY u.updated_at DESC, u.user_id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [
            {
                "user_id": int(r["user_id"]),
                "username": r["username"],
                "first_name": r["first_name"],
                "language": r["language"],
                "created_at": int(r["created_at"]),
                "last_active": int(r["last_active"]),
                "posts": int(r["posts"]),
                "channels": int(r["channels"]),
            }
            for r in rows
        ]

    async def get_user_profile(self, user_id: int) -> dict[str, object] | None:
        row = await self._execute_fetchone(
            "SELECT user_id, timezone, language, username, first_name, created_at, "
            "plan, plan_expires_at, referred_by FROM users WHERE user_id=?",
            (user_id,),
        )
        if row is None:
            return None
        referral = await self.get_referral_stats(user_id)
        channels = await self.count_user_destinations(user_id)
        posts_row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM scheduled_posts WHERE user_id=?",
            (user_id,),
        )
        posts = 0 if posts_row is None else int(posts_row["cnt"])
        status_rows = await self._conn.execute_fetchall(
            "SELECT status, COUNT(1) AS cnt FROM scheduled_posts WHERE user_id=? GROUP BY status",
            (user_id,),
        )
        posts_by_status = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "cancelled": 0}
        for r in status_rows:
            posts_by_status[str(r["status"])] = int(r["cnt"])
        return {
            "user_id": int(row["user_id"]),
            "timezone": row["timezone"],
            "language": row["language"],
            "username": row["username"],
            "first_name": row["first_name"],
            "created_at": int(row["created_at"]),
            "plan": row["plan"] or "basic",
            "plan_expires_at": None if row["plan_expires_at"] is None else int(row["plan_expires_at"]),
            "referred_by": None if row["referred_by"] is None else int(row["referred_by"]),
            "referred_count": referral["referred_count"],
            "referred_activated": referral["referred_activated"],
            "channels": channels,
            "posts": posts,
            "posts_by_status": posts_by_status,
        }

    async def daily_new_users(self, since_ts: int) -> list[tuple[str, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT strftime('%Y-%m-%d', created_at, 'unixepoch') AS day, COUNT(1) AS cnt
            FROM users
            WHERE created_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (since_ts,),
        )
        return [(str(r["day"]), int(r["cnt"])) for r in rows]

    async def daily_posts_sent(self, since_ts: int) -> list[tuple[str, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT strftime('%Y-%m-%d', sent_at, 'unixepoch') AS day, COUNT(1) AS cnt
            FROM scheduled_posts
            WHERE status='sent' AND sent_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (since_ts,),
        )
        return [(str(r["day"]), int(r["cnt"])) for r in rows]
