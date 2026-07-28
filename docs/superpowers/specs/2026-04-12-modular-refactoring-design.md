# Design: Modular Refactoring of auto-sender-bot

**Date:** 2026-04-12  
**Status:** Approved  
**Scope:** Maintainability refactoring — no new user-facing features  
**Scale:** 1–10 users (personal/team), SQLite, single-process

---

## Problem

- `telegram/router.py` — 4,200 lines, all FSM handlers in one file inside `build_router(store)` closure; hard to navigate and extend
- `core/state.py` — 2,165 lines mixing SQL DAL with business logic
- No versioned DB migrations — all in a single `migrate()` method
- FSM contexts are untyped string dicts prone to key-name bugs

---

## Goals

1. Split `router.py` into feature-domain modules without changing any behavior
2. Extract service layer from `state.py` (business logic → services, SQL → DAL)
3. Introduce versioned DB migrations with `schema_migrations` table
4. Add typed FSM context dataclasses per flow

---

## Non-Goals

- No new user features
- No database engine change (SQLite stays)
- No scheduler changes (in-process polling stays)
- No webhook support (long polling stays)

---

## Architecture

### Handler Modules (Phase 1)

```
telegram/
  handlers/
    states.py      ← all StatesGroup classes (ScheduleStates, RepeatStates, DraftStates, etc.)
    keyboards.py   ← all InlineKeyboardMarkup builders (_main_menu_kb, _destinations_kb, etc.)
    helpers.py     ← _user_lang(), _check_*admin*(), _render_destinations(), store-dependent helpers,
                      Telegram-API helpers (_check_user_admin, _check_bot_admin_and_post)
    shared.py      ← cross-flow handlers: smedia:done, sconf:yes, scancel,
                      all TimePicker callbacks (tp:nav:, tp:date:, tp:quick:, tp:time:, tp:back:calendar),
                      schedule_enter_datetime (registered for all entering_datetime/selecting_time states),
                      on_my_chat_member (my_chat_member event)
    schedule.py    ← /schedule + ScheduleStates-specific handlers (~700 lines)
    queue.py       ← /queue, /edit, /delete + EditStates-specific handlers (~600 lines)
    recurring.py   ← /repeat + RepeatStates-specific handlers (~500 lines)
    drafts.py      ← /drafts/* + DraftStates-specific handlers (~600 lines)
    broadcast.py   ← /broadcast + BroadcastStates-specific handlers (~400 lines)
    settings.py    ← /timezone, /language, /link, /link_forward (~300 lines)
    teams.py       ← /team_* + invite flow (~300 lines)
  router.py        ← only include_router() calls (~30 lines)
```

#### Boundary rule (replaces old I2 rule)

- **`state.py` (DAL):** Only SQL — INSERT/SELECT/UPDATE/DELETE. No Telegram API calls, no business logic.
- **`core/services/`:** Business orchestration with no Telegram API calls and no raw SQL.
- **`telegram/handlers/helpers.py`:** Shared handler utilities. May contain Telegram API calls (admin checks, render helpers that call `message.answer()` etc.) — this is deliberate, since handlers must coordinate with the Telegram API.
- **`telegram/handlers/shared.py` + feature modules:** FSM-aware handlers. Call helpers, services, and DAL as needed.

#### Cross-flow handler strategy

The following handlers dispatch across multiple FSM flows. They live in **`shared.py`**:

- `smedia:done` / `smedia:clear` — fires in `ScheduleStates.collecting_post`, `RepeatStates.collecting_post`, `BroadcastStates.collecting_post`, `DraftStates.collecting_post`, `DraftStates.editing_post`, `EditStates.collecting_media`
- `sconf:yes` — fires in `ScheduleStates.confirming`, `RepeatStates.confirming`, `BroadcastStates.confirming`, `DraftStates.confirming`
- `scancel` — cross-flow cancellation (fires in multiple states across flows)
- All TimePicker callbacks (`tp:nav:`, `tp:date:`, `tp:quick:`, `tp:time:`, `tp:back:calendar`) — fire in all `entering_datetime`/`selecting_time` states across all flows
- `schedule_enter_datetime` message handler — registered for all `entering_datetime`/`selecting_time` states
- `on_my_chat_member` (`router.my_chat_member`) — standalone

These handlers inspect `await state.get_state()` internally and are NOT duplicated across feature modules.

#### Router include order

In aiogram 3.x, earlier-registered routers take priority. `shared_router` must be included first because its state filters cover all domains:

```python
def build_router(store):
    router = Router()
    # shared first: cross-flow callbacks must resolve before feature-specific fallbacks
    router.include_router(shared.build_router(store))
    # feature routers: most specific first
    for module in [schedule, recurring, drafts, broadcast, queue, settings, teams]:
        router.include_router(module.build_router(store))
    return router
```

#### Closure dissolution

All helper functions inside `build_router(store)` are currently closures capturing `store`. Migration:

1. Promote all helpers to module-level functions accepting `store` as explicit parameter.  
   Before: `def _user_lang(user_id)` (closure)  
   After: `def _user_lang(store, user_id)` in `helpers.py`

2. Update all call sites accordingly.

3. **Update all test files that import from `telegram.router`** as part of Phase 1:

| Test file | Currently imports | New import path |
|-----------|------------------|-----------------|
| `tests/test_timezone.py` | `_is_valid_tz_name`, `_resolve_timezone_input`, `_timezone_setup_kb` | `telegram.handlers.helpers` / `telegram.handlers.keyboards` |
| `tests/test_router_permissions.py` | `_check_bot_admin_and_post`, `_check_user_admin` | `telegram.handlers.helpers` |
| `tests/test_router_schedule_logic.py` | ~8 functions | `telegram.handlers.helpers` / `telegram.handlers.keyboards` |
| `tests/test_router_schedule_flow.py` | `ScheduleStates`, `build_router` | `telegram.handlers.states`, `telegram.router` |
| `tests/test_router_repeat_flow.py` | `RepeatStates`, `build_router` | `telegram.handlers.states`, `telegram.router` |
| `tests/test_router_drafts.py` | `DraftStates`, `build_router` | `telegram.handlers.states`, `telegram.router` |
| `tests/test_router_edit_posts.py` | `EditStates`, `build_router` | `telegram.handlers.states`, `telegram.router` |
| `tests/test_router_broadcast.py` | `BroadcastStates`, `build_router` | `telegram.handlers.states`, `telegram.router` |

   Note: `telegram/router.py` does NOT re-export StatesGroup classes. Tests import directly from `telegram.handlers.states`.

### DB Migrations (Phase 2)

```
core/
  migrations/
    001_initial.sql       ← users, destinations, user_destinations, scheduled_posts, scheduled_post_media
    002_teams.sql         ← teams, team_members, team_invites
    003_recurring.sql     ← recurring_patterns, recurring_instances
    004_drafts.sql        ← drafts, draft_media
  migrate.py              ← run_migrations(conn)
```

All `.sql` files use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` — idempotent on existing databases. First run on an already-deployed database is safe: DDL is a no-op, version is recorded.

#### Migration runner with proper transaction safety

`executescript` issues an implicit `COMMIT` before executing, so it cannot be used inside a manually managed transaction. Use individual `conn.execute()` calls instead, following the existing pattern in `core/state.py`:

```python
async def run_migrations(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    await conn.commit()

    applied = {row[0] for row in await conn.execute_fetchall(
        "SELECT version FROM schema_migrations"
    )}

    migrations_dir = Path(__file__).parent / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        version = int(path.stem.split("_")[0])
        if version in applied:
            continue
        statements = [s.strip() for s in path.read_text().split(";") if s.strip()]
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                await conn.execute(stmt)
            await conn.execute(
                "INSERT INTO schema_migrations VALUES (?, datetime('now'))",
                (version,)
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
```

### Service Layer (Phase 3)

```
core/
  services/
    schedule_svc.py   ← create_broadcast_posts()
    draft_svc.py      ← publish_draft(), DraftPermissions (moved from rbac.py), get_draft_permissions()
    team_svc.py       ← create_team_invite(), accept_invite()
  state.py            ← dataclasses + pure SQL methods only
```

Services contain no Telegram API calls. Admin checks remain in handlers.

### Typed FSM Contexts (Phase 4)

The dataclasses below are **illustrative starting points**. Before implementing Phase 4, the implementor must audit all `state.update_data()` and `state.get_data()` call sites in the entire handler codebase to produce a complete field list. Known missing fields include (non-exhaustive): `entities_json`, `caption_entities_json`, `text_before_media`, `calendar_month`, `calendar_year`, `selected_date`, `scheduled_local`, `dest_page`, `interval_type`, `draft_publish_id`, `edit_draft_id`, `team_id`.

```python
@dataclass
class ScheduleContext:
    # NOTE: actual key may be selected_chat_ids (list) — verify against audit
    selected_chat_id: int | None = None
    scheduled_at_utc: int | None = None
    kind: str | None = None        # "text" | "media"
    text: str | None = None
    media_items: list[dict] = field(default_factory=list)
    caption: str | None = None
    caption_above: bool = False
    # ... complete after audit

@dataclass
class DraftContext:
    selected_chat_id: int | None = None
    kind: str | None = None
    text: str | None = None
    media_items: list[dict] = field(default_factory=list)
    scope: str | None = None       # "personal" | "team"
    # ... complete after audit

@dataclass
class EditContext:
    edit_post_id: int | None = None
    chat_id: int | None = None
    field: str | None = None       # "text" | "time" | "media"
    text: str | None = None
    media_items: list[dict] = field(default_factory=list)
    # ... complete after audit
```

Helpers in `helpers.py`:
```python
async def get_schedule_ctx(state: FSMContext) -> ScheduleContext: ...
async def set_schedule_ctx(state: FSMContext, ctx: ScheduleContext): ...
# etc. per context type
```

Cross-flow handlers in `shared.py` dispatch per state branch using typed context helpers:

```python
async def cb_media_done(callback, state, store):
    current = await state.get_state()
    if current in ScheduleStates:
        ctx = await get_schedule_ctx(state)
        ...
    elif current in DraftStates:
        ctx = await get_draft_ctx(state)
        ...
```

---

## Phase 0: Close Uncommitted Work

Files with uncommitted changes: `telegram/router.py`, `core/state.py`, `telegram/i18n.py`

1. Register pagination callbacks: `qpage:{n}`, `epage:{n}`, `delpage:{n}`
2. Implement `qview:{post_id}` using existing i18n keys: `btn_view_post`, `view_not_found`, `view_post_info`
3. Verify `list_pending_posts(offset=...)` and `list_editable_pending_posts(offset=...)` work
4. **Create** `tests/test_router_queue.py` with pagination and preview tests
5. Commit

---

## Implementation Order & Dependencies

```
Phase 0  →  Phase 1  →  Phase 3
                     ↘  Phase 4
Phase 2  (independent, can run in parallel with Phase 1)
```

---

## Files NOT Changed

- `core/notifier.py`
- `core/scheduler.py`
- `core/time_picker.py`
- `core/utils.py`
- `core/config.py`
- `core/fsm_storage.py`
- `core/timezone_resolver.py`
- `core/healthcheck.py`
- `core/logging_setup.py`

Eight test files (listed in Closure Dissolution table) **will have import paths updated** in Phase 1. All tests must pass after each phase.

---

## Verification

| Phase | Check |
|-------|-------|
| 0 | `pytest tests/test_router_queue.py -q` (new file) |
| 1 | `pytest -q` (all green, including 8 updated test import paths); manually verify all commands respond |
| 2 | Fresh DB run → check tables; re-run → `schema_migrations` unchanged; simulate partial failure (corrupt SQL) → version absent from `schema_migrations` |
| 3 | `pytest -q`; manually test broadcast post + draft RBAC |
| 4 | `pytest -q`; manually walk through `/schedule` and `/drafts` full flow |
