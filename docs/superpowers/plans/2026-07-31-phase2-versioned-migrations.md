# Phase 2: Versioned Migrations + Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `StateStore.migrate()` DDL blob with numbered `.sql` migration files applied by a versioned runner backed by a `schema_migrations` table, while keeping the live production DB and all existing tests working unchanged.

**Architecture:** A new `core/migrate.py` exposes `run_migrations(conn)`. It ensures a `schema_migrations(version, applied_at)` table, detects an already-deployed pre-migration DB and records versions `1..N` as a baseline without touching DDL, then applies any not-yet-applied `core/migrations/NNN_*.sql` files in order. `StateStore.migrate()` becomes a thin wrapper: it calls `run_migrations`, then runs the two **idempotent legacy-DB reconciliation steps** that must survive every startup (guarded `ALTER TABLE users` for `username`/`first_name`, and the `team_members` owner backfill). `StateStore`'s public interface is unchanged; callers and the ~20 tests that call `store.migrate()` don't notice.

**Tech Stack:** Python 3.12, aiosqlite 0.21, SQLite 3.50, pytest / pytest-asyncio.

---

## Design decisions locked for this plan

These resolve gaps between the April/July design spec (`docs/superpowers/specs/2026-04-12-modular-refactoring-design.md`, "Phase 2") and the actual `state.py:145-379`:

1. **The spec only describes schema DDL. The current `migrate()` also runs data reconciliation** (`state.py:343-378`): three guarded `ALTER TABLE users ADD COLUMN` and a two-statement `team_members` owner backfill (the backfill binds a `now` timestamp param). **Decision:** these stay in the `migrate()` wrapper and run on *every* startup, after `run_migrations`, exactly as today. They are idempotent and cheap at 1-10 user scale. They are NOT turned into `.sql` files (the backfill needs a bound param; both must run on legacy DBs that the baseline path skips DDL for).

2. **Baseline skips DDL — so column-adds cannot live only in `001`.** The baseline path records versions `1..N` as applied *without running any CREATE/ALTER*. A live DB that predates the `username`/`first_name` columns would therefore never get them if we relied on `001` alone. **Decision:** `001` includes `username`/`first_name`/`language` inline (correct for fresh DBs), AND the guarded `ALTER TABLE users` block is kept in the wrapper as an idempotent safety net for legacy DBs (correct for existing DBs). Zero behavior change vs. today.

3. **No true DDL atomicity on Python 3.12.** In legacy transaction mode, `sqlite3` implicitly COMMITs before DDL, so wrapping `CREATE TABLE` in `BEGIN IMMEDIATE ... COMMIT` does not give rollback of DDL. Safety comes instead from every statement being `CREATE TABLE/INDEX IF NOT EXISTS` (idempotent re-run) plus recording each file's version only after all its statements succeed. The verification test asserts the *observable outcome* (failed file's version absent, prior versions present, data intact, safe re-run), not literal DDL rollback.

4. **File split by FK dependency order:** `001` users/destinations → `002` teams → `003` posts → `004` recurring → `005` drafts. (`drafts` FK-references `teams` and `destinations`; `recurring_instances` references `scheduled_posts`.)

5. **`.sql` files contain only bare `CREATE TABLE/INDEX IF NOT EXISTS` statements, no `--`/`/* */` comments**, because the runner splits naively on `;`. (Documented risk in the spec.)

---

## File Structure

- Create: `core/migrations/001_users_destinations.sql` — `users` (with `language`/`username`/`first_name` inline), `destinations`, `user_destinations`
- Create: `core/migrations/002_teams.sql` — `teams`, `team_members`, `team_invites` + their indexes (incl. partial unique single-owner index)
- Create: `core/migrations/003_posts.sql` — `scheduled_posts`, `scheduled_post_media` + `idx_scheduled_due`
- Create: `core/migrations/004_recurring.sql` — `recurring_patterns`, `recurring_instances` + their indexes
- Create: `core/migrations/005_drafts.sql` — `drafts`, `draft_media` + their indexes
- Create: `core/migrate.py` — `run_migrations(conn)`, `_schema_already_present`, migration discovery helpers
- Create: `tests/test_migrations.py` — fresh-DB, baseline, idempotency, and failure-isolation tests
- Modify: `core/state.py:145-379` — replace the DDL blob in `migrate()` with a call to `run_migrations`, keep the two reconciliation steps as small private helpers
- Reference (do NOT modify): `main.py:30` (`await store.migrate()`), the ~20 test files calling `store.migrate()` — all keep working via the unchanged wrapper

---

## Task 1: Extract the current DDL into numbered `.sql` files

**Files:**
- Create: `core/migrations/001_users_destinations.sql`
- Create: `core/migrations/002_teams.sql`
- Create: `core/migrations/003_posts.sql`
- Create: `core/migrations/004_recurring.sql`
- Create: `core/migrations/005_drafts.sql`

Copy the exact DDL from `state.py:146-340`, split across the five files by dependency order. **Change vs. current:** the `users` `CREATE TABLE` in `001` gains `username TEXT NULL` and `first_name TEXT NULL` columns inline (currently added by `ALTER`). Every statement stays `IF NOT EXISTS`. No comments inside the files.

- [ ] **Step 1: Write `001_users_destinations.sql`**

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT NULL,
    language TEXT NULL,
    username TEXT NULL,
    first_name TEXT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS destinations (
    chat_id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    username TEXT NULL,
    bot_status TEXT NOT NULL,
    bot_can_post INTEGER NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_destinations (
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    linked_via TEXT NOT NULL,
    linked_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, chat_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
);
```

- [ ] **Step 2: Write `002_teams.sql`**

Copy `teams`, `idx_teams_owner_created`, `team_members`, `idx_team_members_user_team`, `idx_team_members_single_owner`, `team_invites`, `idx_team_invites_team_created`, `idx_team_invites_expires` verbatim from `state.py:176-227`.

- [ ] **Step 3: Write `003_posts.sql`**

Copy `scheduled_posts`, `idx_scheduled_due`, `scheduled_post_media` verbatim from `state.py:263-294`.

- [ ] **Step 4: Write `004_recurring.sql`**

Copy `recurring_patterns`, `idx_recurring_patterns_user_active`, `idx_recurring_patterns_chat_active`, `recurring_instances`, `idx_recurring_instances_pattern_scheduled` verbatim from `state.py:296-339`.

- [ ] **Step 5: Write `005_drafts.sql`**

Copy `drafts`, `idx_drafts_author_created`, `idx_drafts_team_created`, `draft_media` verbatim from `state.py:229-261`.

- [ ] **Step 6: Verify the SQL parses (all five files, in a throwaway DB)**

Run:
```bash
.venv/bin/python -c "
import sqlite3, pathlib
c = sqlite3.connect(':memory:')
for p in sorted(pathlib.Path('core/migrations').glob('*.sql')):
    c.executescript(p.read_text())
tables = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")]
print(tables)
"
```
Expected: prints the 12 table names (draft_media, drafts, destinations, recurring_instances, recurring_patterns, scheduled_post_media, scheduled_posts, team_invites, team_members, teams, user_destinations, users) with no error.

- [ ] **Step 7: Commit**

```bash
git add core/migrations/
git commit -m "feat(migrations): add numbered .sql schema files (phase 2)"
```

---

## Task 2: The migration runner — fresh DB path

**Files:**
- Create: `core/migrate.py`
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing test for a fresh DB**

```python
# tests/test_migrations.py
import asyncio

import aiosqlite
import pytest

from core.migrate import run_migrations

EXPECTED_TABLES = {
    "users", "destinations", "user_destinations",
    "teams", "team_members", "team_invites",
    "scheduled_posts", "scheduled_post_media",
    "recurring_patterns", "recurring_instances",
    "drafts", "draft_media",
}


async def _tables(conn):
    rows = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_fresh_db_creates_all_tables_and_records_versions():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await run_migrations(conn)

        tables = await _tables(conn)
        assert EXPECTED_TABLES <= tables
        assert "schema_migrations" in tables

        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2, 3, 4, 5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migrations.py::test_fresh_db_creates_all_tables_and_records_versions -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.migrate'`.

- [ ] **Step 3: Write minimal `core/migrate.py`**

```python
from __future__ import annotations

from pathlib import Path

import aiosqlite

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _version_of(path: Path) -> int:
    return int(path.stem.split("_", 1)[0])


def _split_statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


async def _schema_already_present(conn: aiosqlite.Connection) -> bool:
    rows = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_posts'"
    )
    return len(rows) > 0


async def run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )"""
    )
    await conn.commit()

    applied = {
        r[0]
        for r in await conn.execute_fetchall("SELECT version FROM schema_migrations")
    }

    files = _migration_files()

    # BASELINE: a live pre-migration DB (tables exist, no version rows yet).
    if not applied and await _schema_already_present(conn):
        for path in files:
            await conn.execute(
                "INSERT INTO schema_migrations VALUES (?, datetime('now'))",
                (_version_of(path),),
            )
        await conn.commit()
        return

    for path in files:
        version = _version_of(path)
        if version in applied:
            continue
        for stmt in _split_statements(path.read_text()):
            await conn.execute(stmt)
        await conn.execute(
            "INSERT INTO schema_migrations VALUES (?, datetime('now'))",
            (version,),
        )
        await conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_migrations.py::test_fresh_db_creates_all_tables_and_records_versions -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/migrate.py tests/test_migrations.py
git commit -m "feat(migrations): add run_migrations runner with fresh-DB path"
```

---

## Task 3: Idempotency + baseline paths

**Files:**
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing idempotency test**

```python
@pytest.mark.asyncio
async def test_rerun_is_noop_and_versions_stable():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await run_migrations(conn)
        first = await conn.execute_fetchall(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        )
        await run_migrations(conn)  # second run
        second = await conn.execute_fetchall(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        )
        assert [tuple(r) for r in first] == [tuple(r) for r in second]
```

- [ ] **Step 2: Write the failing baseline test**

Simulate a live pre-migration DB: create `scheduled_posts` (the marker table) plus `users` by hand, insert a row, leave `schema_migrations` absent, then run.

```python
@pytest.mark.asyncio
async def test_baseline_records_versions_without_dropping_data():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        # Pre-existing (pre-migration) schema + data.
        await conn.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, "
            "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        await conn.execute(
            "CREATE TABLE scheduled_posts (id TEXT PRIMARY KEY)"
        )
        await conn.execute(
            "INSERT INTO users(user_id, created_at, updated_at) VALUES (42, 0, 0)"
        )
        await conn.commit()

        await run_migrations(conn)

        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2, 3, 4, 5}
        # Data intact, no DDL ran over the existing tables.
        row = await conn.execute_fetchall("SELECT user_id FROM users")
        assert [r[0] for r in row] == [42]
```

- [ ] **Step 3: Run both tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: PASS (the runner from Task 2 already handles both; these lock the behavior in).

> If baseline unexpectedly fails because a fresh, empty DB with no marker table is mistaken for baseline: confirm `_schema_already_present` probes `scheduled_posts` and that an all-empty DB (no marker) takes the normal apply path.

- [ ] **Step 4: Commit**

```bash
git add tests/test_migrations.py
git commit -m "test(migrations): lock idempotency and baseline behavior"
```

---

## Task 4: Failure isolation (a bad migration file)

**Files:**
- Test: `tests/test_migrations.py`

Prove that when file N fails, versions `< N` remain recorded and re-running after the file is fixed completes cleanly. Uses monkeypatch to inject a temporary bad-then-good migration set via a patched `MIGRATIONS_DIR`.

- [ ] **Step 1: Write the failing test**

```python
import pathlib


@pytest.mark.asyncio
async def test_failed_file_leaves_prior_versions_and_reruns_clean(tmp_path, monkeypatch):
    import core.migrate as migrate_mod

    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_a.sql").write_text("CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY);")
    (mig / "002_b.sql").write_text("CREATE TABLE IF NOT EXISTS b (id INTEGER PRIMARY KEY);")
    # 003 is invalid on the first pass.
    bad = mig / "003_c.sql"
    bad.write_text("CREATE TABLE c (this is not valid sql;")

    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", mig)

    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        with pytest.raises(Exception):
            await migrate_mod.run_migrations(conn)

        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2}  # 3 not recorded

        # Fix file 003 and re-run; should complete and record version 3.
        bad.write_text("CREATE TABLE IF NOT EXISTS c (id INTEGER PRIMARY KEY);")
        await migrate_mod.run_migrations(conn)
        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2, 3}
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/test_migrations.py::test_failed_file_leaves_prior_versions_and_reruns_clean -v`
Expected: PASS. If the `INSERT` for version 2 was not committed before 3 ran, this catches it — the runner must `commit()` per file.

- [ ] **Step 3: Commit**

```bash
git add tests/test_migrations.py
git commit -m "test(migrations): failed file isolates prior versions, safe re-run"
```

---

## Task 5: Rewire `StateStore.migrate()` to the runner

**Files:**
- Modify: `core/state.py:145-379`

Replace the giant `executescript` DDL blob with a `run_migrations` call. Keep the guarded `ALTER TABLE users` block and the `team_members` owner backfill as idempotent private helpers that run on every startup (Decisions 1 & 2).

- [ ] **Step 1: Add the import**

At the top of `core/state.py`, next to the existing imports:

```python
from core.migrate import run_migrations
```

- [ ] **Step 2: Replace the `migrate` method body**

Replace the whole method at `state.py:145-379` with:

```python
    async def migrate(self) -> None:
        await run_migrations(self._conn)
        await self._reconcile_user_columns()
        await self._backfill_team_owners()

    async def _reconcile_user_columns(self) -> None:
        # Legacy-DB safety net: baseline path skips DDL, so ensure the columns
        # folded into migration 001 also exist on pre-migration databases.
        user_columns = await self._conn.execute_fetchall("PRAGMA table_info(users)")
        user_column_names = {str(row["name"]) for row in user_columns}
        if "language" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN language TEXT NULL")
        if "username" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN username TEXT NULL")
        if "first_name" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT NULL")
        await self._conn.commit()

    async def _backfill_team_owners(self) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            UPDATE team_members
            SET role='owner', updated_at=?
            WHERE (team_id, user_id) IN (
                SELECT id, owner_user_id
                FROM teams
            )
              AND role <> 'owner'
            """,
            (now,),
        )
        await self._conn.execute(
            """
            INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
            SELECT t.id, t.owner_user_id, 'owner', ?, ?
            FROM teams t
            WHERE NOT EXISTS (
                SELECT 1
                FROM team_members tm
                WHERE tm.team_id = t.id
                  AND tm.user_id = t.owner_user_id
            )
            """,
            (now, now),
        )
        await self._conn.commit()
```

- [ ] **Step 3: Run the migrations test module + a DAL smoke test**

Run: `.venv/bin/python -m pytest tests/test_migrations.py tests/test_state_posts.py tests/test_admin_stats.py -q`
Expected: PASS. `test_state_posts.py`/`test_admin_stats.py` call `store.migrate()` and exercise real DAL methods against the migrated schema.

- [ ] **Step 4: Run the FULL suite (the real regression gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — same count as before Phase 2 (the ~20 `store.migrate()` callers must be unaffected).

> If any test fails, STOP and diagnose with superpowers:systematic-debugging. The most likely culprits: a `.sql` file with a typo vs. the original DDL, or a missing table because a file didn't get read (check `MIGRATIONS_DIR.glob` picks up all five).

- [ ] **Step 5: Commit**

```bash
git add core/state.py
git commit -m "refactor(state): delegate migrate() to versioned run_migrations (phase 2)"
```

---

## Task 6: End-to-end verification against a real on-disk DB

**Files:** none (verification only)

Exercise the exact spec verification matrix row for Phase 2: fresh DB → tables + versions; re-run → unchanged; baseline over a copy of a populated DB → data intact.

- [ ] **Step 1: Fresh on-disk DB creates schema + versions**

Run:
```bash
.venv/bin/python -c "
import asyncio
from core.db import open_db
from core.state import StateStore

async def main():
    import os, tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'fresh.sqlite3')
    conn = await open_db(p)
    store = StateStore(conn)
    await store.migrate()
    rows = await conn.execute_fetchall('SELECT version FROM schema_migrations ORDER BY version')
    print('versions', [r[0] for r in rows])
    await store.migrate()  # re-run
    rows2 = await conn.execute_fetchall('SELECT version FROM schema_migrations ORDER BY version')
    print('versions after rerun', [r[0] for r in rows2])
    await conn.close()

asyncio.run(main())
"
```
Expected: `versions [1, 2, 3, 4, 5]` twice, no error.

- [ ] **Step 2: Baseline over a simulated legacy DB keeps data**

Run:
```bash
.venv/bin/python -c "
import asyncio, os, tempfile, time
from core.db import open_db
from core.state import StateStore

async def main():
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'legacy.sqlite3')
    conn = await open_db(p)
    store = StateStore(conn)
    # First migrate to build a full schema, add a user, then drop schema_migrations
    # to simulate a DB that predates versioning.
    await store.migrate()
    await store.ensure_user(99, username='u', first_name='n')
    await conn.execute('DROP TABLE schema_migrations')
    await conn.commit()
    # Re-run: baseline should record 1..5 and NOT disturb the user row.
    await store.migrate()
    rows = await conn.execute_fetchall('SELECT version FROM schema_migrations ORDER BY version')
    users = await conn.execute_fetchall('SELECT user_id FROM users')
    print('versions', [r[0] for r in rows], 'users', [r[0] for r in users])
    await conn.close()

asyncio.run(main())
"
```
Expected: `versions [1, 2, 3, 4, 5] users [99]`.

- [ ] **Step 3: Confirm no leftover DDL string in `state.py`**

Run: `grep -n "CREATE TABLE" core/state.py`
Expected: no matches (all DDL now lives in `core/migrations/*.sql`).

- [ ] **Step 4: Final full-suite gate**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Update the design spec status (optional but tidy)**

In `docs/superpowers/specs/2026-04-12-modular-refactoring-design.md`, note Phase 2 as implemented (mirroring how Phase 0 is marked DONE).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs(spec): mark phase 2 versioned migrations as implemented"
```

---

## Definition of Done

- `core/migrations/001..005_*.sql` exist; `core/state.py` contains no `CREATE TABLE` DDL.
- `core/migrate.py::run_migrations` handles fresh, baseline, idempotent-rerun, and failed-file paths, all under `tests/test_migrations.py`.
- `StateStore.migrate()` public behavior is unchanged: fresh DBs get all 12 tables + a `schema_migrations` table with versions `1..5`; legacy DBs get baselined + reconciled columns/owners; every existing test still passes.
- `.venv/bin/python -m pytest -q` is green with the same pre-Phase-2 pass count.
