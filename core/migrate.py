from __future__ import annotations

import re
from pathlib import Path

import aiosqlite

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)", re.IGNORECASE
)


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _version_of(path: Path) -> int:
    return int(path.stem.split("_", 1)[0])


def _split_statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


async def _schema_already_present(
    conn: aiosqlite.Connection, files: list[Path]
) -> bool:
    # Baseline only a *complete* pre-migration schema. A partial one must fall
    # through to the normal loop, otherwise it gets stamped and never repaired.
    expected = {
        m for path in files for m in _CREATE_TABLE_RE.findall(path.read_text())
    }
    rows = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    return expected <= {r[0] for r in rows}


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
    if not applied and await _schema_already_present(conn, files):
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
        # Apply the whole file + its version row as ONE transaction via
        # executescript. executescript issues an implicit COMMIT first and
        # ignores isolation_level, so the transaction must live INSIDE the
        # script (BEGIN;...COMMIT;) rather than an outer execute("BEGIN").
        # This makes each file atomic AND tolerates ";" inside trigger/string
        # bodies (a naive split-on-";" would shred those). version is an int
        # from _version_of, so the f-string interpolation is safe.
        script = (
            "BEGIN;\n"
            + path.read_text()
            + f"\nINSERT INTO schema_migrations VALUES ({version}, datetime('now'));\nCOMMIT;"
        )
        try:
            await conn.executescript(script)
        except Exception:
            await conn.rollback()
            raise
