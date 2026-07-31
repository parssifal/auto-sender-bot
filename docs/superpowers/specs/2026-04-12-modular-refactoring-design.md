# Design: Modular Refactoring of auto-sender-bot

**Date:** 2026-04-12 (deepened 2026-07-30)
**Status:** Approved — implementation-ready per phase
**Scope:** Maintainability refactoring — no new user-facing features
**Scale:** 1–10 users (personal/team), SQLite, single-process

---

## Problem

- `telegram/router.py` — **4,235 lines**, all FSM handlers inside a `build_router(store)` closure (~65 nested `async def`, ~50 module-level helpers, 91 handler decorators); hard to navigate and extend.
- `core/state.py` — **2,347 lines** (73 public methods) mixing SQL DAL with business orchestration.
- No versioned DB migrations — schema lives in one `migrate()` method (`state.py:145–380`).
- FSM contexts are untyped string dicts (~26 distinct keys) prone to key-name bugs.

---

## Goals

1. Split `router.py` into feature-domain modules without changing any behavior (Phase 1).
2. Introduce versioned DB migrations with a `schema_migrations` table + baseline for the live DB (Phase 2).
3. Extract a thin service layer of pure orchestration from handlers (Phase 3).
4. Add typed FSM context dataclasses per flow (Phase 4).

---

## Non-Goals

- No new user features.
- No database engine change (SQLite stays).
- No scheduler changes (in-process polling stays).
- No webhook support (long polling stays).
- **No changes to `telegram/admin.py` / `core/webapp.py`** — the admin Mini App already ships its own `build_admin_router` and is the reference pattern for this refactor.

---

## Current-State Audit (2026-07-30)

Grounding facts that this spec is built on (re-verified against `main`, since the original April sketch predates the July admin work):

- **8 `StatesGroup` classes** in `router.py`, not 5: `TimezoneStates`, `LanguageStates`, `DestinationsStates` (settings-ish flows) in addition to `ScheduleStates`, `RepeatStates`, `DraftStates`, `BroadcastStates`, `EditStates`.
- **91 handler decorators**: 50 `@router.message`, 40 `@router.callback_query`, 1 `@router.my_chat_member`.
- `telegram/admin.py` already exposes an independent `build_admin_router(...)` — proof the "feature = its own router" target already works in production.
- `core/state.py` gained ~20 read-only admin/stats aggregates in July (`count_users`, `list_users`, `get_user_profile`, `daily_new_users`, `daily_posts_sent`, `language_distribution`, `top_active_users`, …).
- `core/rbac.py` is **already a clean pure module**: `DraftPermissions` dataclass + `resolve_draft_permissions` + `can_view/edit/delete/publish_draft` + `can_create_team_draft` — no SQL, no Telegram API.
- `create_broadcast_posts` and `accept_team_invite` are **transactional DAL methods** (`BEGIN IMMEDIATE` + multi-statement).
- `migrate()` creates **12 tables + ~12 indexes + 3 guarded `ALTER TABLE users`** (`language`, `username`, `first_name`). Tables: users, destinations, user_destinations, teams, team_members, team_invites, drafts, draft_media, scheduled_posts, scheduled_post_media, recurring_patterns, recurring_instances.

---

## Architecture

### Phase 1 — Handler Modules

Dissolve the `build_router(store)` closure. Every helper and handler becomes module-level and takes `store` as an explicit parameter (mirrors how `admin.py` already works).

```
telegram/
  handlers/
    __init__.py
    states.py      ← all 8 StatesGroup: Timezone, Language, Destinations,
                     Schedule, Repeat, Draft, Broadcast, Edit
    keyboards.py   ← ~35 *_kb() builders (pure: no store, no Telegram I/O)
                     + token parsers (_parse_calendar_*, _parse_time_token,
                       _short_id, _format_local, _destination_label)
    helpers.py     ← store-dependent helpers (_user_lang, _render_destinations,
                     _load_pending_post_for_edit, _build_*_summary,
                     _check_user_admin, _check_bot_admin_and_post) — store passed explicitly
    shared.py      ← cross-flow handlers (see below) + on_my_chat_member
    schedule.py    ← cmd_schedule + ScheduleStates handlers
    queue.py       ← /queue /edit /delete /view + EditStates + pagination
                     (qpage/epage/delpage/qview)
    recurring.py   ← cmd_repeat / cmd_repeats + RepeatStates handlers
    drafts.py      ← /drafts* + DraftStates + team-draft rbac gating
    broadcast.py   ← cmd_broadcast + BroadcastStates handlers
    settings.py    ← /timezone /language /link /link_forward
                     + Timezone/Language/Destinations states
    teams.py       ← /team_* + invite flow (cmd_start invite branch delegates here)
  router.py        ← build_router(store): assembles sub-routers only (~40 lines)
  admin.py         ← UNCHANGED (already an independent build_admin_router)
```

#### Boundary rule

- **`state.py` (DAL):** Only SQL — INSERT/SELECT/UPDATE/DELETE, including transactional methods and read-only aggregates. No Telegram API, no business orchestration.
- **`core/services/`:** Business orchestration — no Telegram API, no raw SQL (Phase 3).
- **`telegram/handlers/keyboards.py`:** Pure functions — no `store`, no Telegram I/O.
- **`telegram/handlers/helpers.py`:** Shared handler utilities. May call the Telegram API (admin checks, render helpers) — deliberate, since handlers coordinate with Telegram.
- **`telegram/handlers/shared.py` + feature modules:** FSM-aware handlers. Call helpers, services, DAL, rbac.

#### Cross-flow handler strategy

These handlers dispatch across multiple FSM flows; they live in **`shared.py`**, inspect `await state.get_state()` internally, and are NOT duplicated across feature modules:

- `smedia:done` / `smedia:clear` — fires in `ScheduleStates.collecting_post`, `RepeatStates.collecting_post`, `BroadcastStates.collecting_post`, `DraftStates.collecting_post`, `DraftStates.editing_post`, `EditStates.collecting_media`.
- `sconf:yes` — fires in `ScheduleStates.confirming`, `RepeatStates.confirming`, `BroadcastStates.confirming`, `DraftStates.confirming`.
- `scancel` — cross-flow cancellation.
- All TimePicker callbacks (`tp:nav:`, `tp:date:`, `tp:quick:`, `tp:time:`, `tp:back:calendar`) — fire in all `entering_datetime`/`selecting_time` states across flows.
- `schedule_enter_datetime` (message handler) — registered for all `entering_datetime`/`selecting_time` states.
- `on_my_chat_member` (`router.my_chat_member`) — standalone.

#### Router include order

aiogram 3.x gives earlier-registered routers priority. `shared_router` is included FIRST because its state filters span every domain:

```python
def build_router(store):
    router = Router()
    router.include_router(shared.build_router(store))   # first: cross-flow filters cover all domains
    for module in (schedule, recurring, drafts, broadcast, queue, settings, teams):
        router.include_router(module.build_router(store))
    return router
```

#### Closure dissolution

1. Promote each helper inside `build_router(store)` to a module-level function with an explicit `store` param.
   Before: `def _user_lang(user_id)` (closure) → After: `def _user_lang(store, user_id)` in `helpers.py`.
2. Update all call sites.
3. Update the 8 test files that import from `telegram.router`:

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

`telegram/router.py` does NOT re-export StatesGroup classes; tests import directly from `telegram.handlers.states`.

**Execution:** extract one feature domain at a time (not big-bang). `pytest -q` green after each extracted module.

**Risk/mitigation:** aiogram handler registration order affects routing — extract incrementally, domain by domain, each step under tests and a manual command walk-through.

### Phase 2 — Versioned Migrations + Baseline — DONE (2026-07-31)

Implemented: `core/migrations/001..005_*.sql` + `core/migrate.py::run_migrations`; `StateStore.migrate()` now delegates to the runner and keeps two idempotent legacy-DB reconciliation steps (`_reconcile_user_columns`, `_backfill_team_owners`) that the original `migrate()` performed but this spec's Phase 2 sketch omitted. Covered by `tests/test_migrations.py` (fresh / rerun / baseline / failed-file isolation). Plan: `docs/superpowers/plans/2026-07-31-phase2-versioned-migrations.md`.

Replace the monolithic `migrate()` (`state.py:145–380`) with numbered `.sql` files + a runner backed by `schema_migrations`. Independent of Phase 1 — can run in parallel.

```
core/
  migrations/
    001_users_destinations.sql   ← users (incl. language/username/first_name inline),
                                    destinations, user_destinations
    002_teams.sql                ← teams, team_members, team_invites + indexes
    003_posts.sql                ← scheduled_posts, scheduled_post_media + idx_scheduled_due
    004_recurring.sql            ← recurring_patterns, recurring_instances + indexes
    005_drafts.sql               ← drafts, draft_media + indexes
  migrate.py                     ← run_migrations(conn)
```

File order follows FK dependencies: users/destinations → teams → posts → recurring → drafts. Because these become the NEW baseline, the 3 guarded `ALTER TABLE users ADD COLUMN` are folded directly into the `users` `CREATE TABLE` in `001`. All statements are `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` (idempotent).

#### Runner with baseline (B1) and correct transactions

`executescript` issues an implicit COMMIT, so it cannot run inside a managed transaction — use individual `conn.execute()` calls (matching the existing `state.py` pattern):

```python
async def run_migrations(conn):
    await conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""")
    await conn.commit()

    applied = {r[0] for r in await conn.execute_fetchall(
        "SELECT version FROM schema_migrations")}

    # --- BASELINE (B1): live pre-migration DB ---
    if not applied and await _schema_already_present(conn):
        for version in _all_migration_versions():          # 1..5
            await conn.execute(
                "INSERT INTO schema_migrations VALUES (?, datetime('now'))", (version,))
        await conn.commit()
        return

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.stem.split("_")[0])
        if version in applied:
            continue
        statements = [s.strip() for s in path.read_text().split(";") if s.strip()]
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                await conn.execute(stmt)
            await conn.execute(
                "INSERT INTO schema_migrations VALUES (?, datetime('now'))", (version,))
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
```

`_schema_already_present(conn)` probes a marker table:
`SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_posts'`.
Tables present + empty `schema_migrations` ⇒ deployed pre-migration DB ⇒ record versions 1..5 as applied without touching DDL.

**Integration:** `StateStore.migrate()` becomes a thin wrapper delegating to `run_migrations(self._conn)`. `StateStore`'s public interface is unchanged; callers and tests don't notice.

**Risk/mitigation:** naive `split(";")` breaks on triggers/CTEs containing semicolons, and does not strip SQL comments. Our migrations are simple CREATE TABLE/INDEX only — keep `.sql` files free of inline `--` / `/* */` comments that contain semicolons; if complex DDL is ever needed, switch that file to an explicit statement list.

### Phase 3 — Service Layer (light, orchestration-only) — DONE (2026-07-31)

Implemented: `core/services/{broadcast_svc,draft_svc,team_svc}.py` (+ `_shared.py` for relocated pure helpers `_normalize_selected_chat_ids`, `_destination_label`, `_resolve_draft_id`, `_resolve_team_id`, each re-exported from its old `telegram/handlers/*` home so callers/tests are unaffected). Services take `store` first and import ONLY `core.*` — enforced by `tests/test_services_boundary.py` (AST guard). Extracted:
- `broadcast_svc.resolve_valid_destinations` + `create_broadcast(PostContent)` — the broadcast branch of `shared.py::cb_confirm_yes`.
- `draft_svc.resolve_publishable_draft` + `publish_draft` (draft-publish branch), plus `resolve_draft_by_ref` / `resolve_draft_by_id` that DRY the 7 repeated permission gates in `drafts.py`.
- `team_svc.prepare_team_invite` (invite preparation) — `cmd_team_invite`.

**Key spec-vs-reality decisions (see plan):** the real publish orchestration lived in the cross-flow `cb_confirm_yes`, entangled with Telegram I/O — so the **admin checks (`_check_user_admin`/`_check_bot_admin_and_post`) stay in the handler** and services expose separate *resolve* and *create* steps. The invite-**accept** path (`accept_team_invite`) was NOT wrapped: it is an already-transactional DAL call whose only remaining handler work is i18n-key selection (same A1 rationale as stats aggregates). `create_broadcast_posts`, `accept_team_invite`, all stats aggregates, and `rbac.py` are unchanged. Tests: **254 passing** (was 233); new `tests/test_{broadcast,draft,team}_svc.py` run without aiogram. Plan: `docs/superpowers/plans/2026-07-31-phase3-service-layer.md`.

A service here is pure business orchestration: no Telegram I/O, no raw SQL. It reads via the DAL, makes decisions, returns a result — extracted from handlers so it is unit-testable without aiogram mocks. Depends on Phase 1 (services are lifted out of the already-split feature modules).

```
core/
  services/
    __init__.py
    broadcast_svc.py  ← resolve selected chat_ids → valid destinations,
                        assemble media_items, call store.create_broadcast_posts()
    draft_svc.py      ← personal/team scope decision, gate via rbac.can_publish_draft,
                        publish orchestration: draft → create_scheduled_*_post
    team_svc.py       ← invite preparation (role validation via rbac.can_create_team_draft),
                        handle accept result
  rbac.py             ← UNCHANGED (already pure permission predicates)
  state.py            ← essentially UNCHANGED: transactional methods AND all read-only
                        stats aggregates stay in the DAL (decision A1)
```

**What moves vs what stays:**

| Logic | Now | After Phase 3 |
|---|---|---|
| Resolve destinations + build posts for broadcast | handler | `broadcast_svc` (calls DAL) |
| Personal/team scope + draft publish orchestration | handler (`_start_draft_publish`) | `draft_svc` |
| Invite role validation + accept handling | handler | `team_svc` (via `rbac`) |
| `create_broadcast_posts`, `accept_team_invite` (transactions) | DAL | **stay in DAL** |
| `DraftPermissions`, `can_*` | `rbac.py` | **stay in `rbac.py`** |
| stats/admin aggregates (~20 methods) | DAL | **stay in DAL (A1)** |

**Decision A1 rationale:** read-only aggregates are pure SQL with no side effects — by the boundary rule they ARE the DAL. Wrapping ~20 of them in a `stats_svc` is grouping for grouping's sake (YAGNI at this scale). Transactional methods stay in the DAL because extracting them would break a single `BEGIN IMMEDIATE` boundary for cosmetic gain.

**Verification:** `pytest -q`; new service unit tests without aiogram (pass a real/fake store); manual broadcast + team-draft publish with RBAC checks.

### Phase 4 — Typed FSM Contexts

Replace untyped `dict[str, Any]` FSM data (~26 keys) with dataclasses. Depends on Phase 1 (needs split modules + `shared.py`); orthogonal to Phase 3.

**Audit-driven insight:** content fields (`kind`, `text`, `media_items`, `caption`, `caption_above`, `entities_json`, `caption_entities_json`, `text_before_media`) are identical across schedule/repeat/broadcast/draft/edit — exactly what cross-flow `smedia:done`/`sconf:yes` touch. Datetime fields are what the shared datetime handler touches. So: **compose from mixins**, not 5 independent classes.

```python
# telegram/handlers/contexts.py

@dataclass
class PostContent:                      # collected by cross-flow media/confirm handlers
    kind: str | None = None             # "text" | "media"
    text: str | None = None
    entities_json: str | None = None
    media_items: list[dict] = field(default_factory=list)
    caption: str | None = None
    caption_entities_json: str | None = None
    caption_above: bool = False
    text_before_media: str | None = None

@dataclass
class DateTimePick:                     # touched by the shared datetime handler
    calendar_year: int | None = None
    calendar_month: int | None = None
    selected_date: str | None = None
    scheduled_at_utc: int | None = None
    scheduled_local: str | None = None

@dataclass
class PreviewRef:                       # cross-flow; set by _send_post_preview
    preview_msg_ids: list[int] = field(default_factory=list)
    preview_chat_id: int | None = None

@dataclass
class ScheduleContext(PostContent, DateTimePick):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0

@dataclass
class RepeatContext(PostContent, DateTimePick):
    selected_chat_ids: list[int] = field(default_factory=list)
    interval_type: str | None = None

@dataclass
class BroadcastContext(PostContent, DateTimePick):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0

@dataclass
class DraftContext(PostContent, DateTimePick):
    chat_id: int | None = None
    team_id: int | None = None
    draft_text: str | None = None
    draft_entities_json: str | None = None
    draft_publish_id: str | None = None

@dataclass
class EditContext(PostContent, DateTimePick):    # DateTimePick required: EditStates has
    edit_post_id: str | None = None              # entering_datetime/selecting_time (reschedule)
    edit_draft_id: str | None = None
    edit_preserve_caption_above: bool = False
```

> **Settings flows** (`TimezoneStates`, `LanguageStates`, `DestinationsStates`) are trivial single-field flows and intentionally keep untyped dict access — a dataclass isn't worth it (YAGNI). Phase 4 typed contexts cover only the 5 content-heavy flows above.

**Serialization — deploy safety (critical).** FSM storage (Redis/memory) serializes to JSON and sessions can survive a restart mid-flow. Keep storing **flat keys exactly as today** (not a nested object); the wrappers only add typed access:

```python
# helpers.py
async def get_schedule_ctx(state) -> ScheduleContext:
    d = await state.get_data()
    return ScheduleContext(**{k: d[k] for k in _fields(ScheduleContext) if k in d})

async def patch_schedule_ctx(state, **changes):
    await state.update_data(**changes)     # flat keys — backward compatible
```

Partially migrated flows and Redis-persisted in-flight sessions keep working across a rollout: the keys are unchanged, only typed access is layered on top.

Cross-flow dispatch in `shared.py` uses the typed getters:

```python
async def cb_media_done(callback, state, store):
    cur = await state.get_state()
    if cur in ScheduleStates:  ctx = await get_schedule_ctx(state)
    elif cur in DraftStates:   ctx = await get_draft_ctx(state)
    ...  # ctx.media_items, ctx.caption_above — autocompleted, typo-proof
```

**Risk/mitigation:** dataclass mixin inheritance requires default-valued fields after non-default ones — all fields here have defaults, so no MRO conflict. Migrate one flow at a time, each under `pytest -q` + a manual `/schedule` and `/drafts` walk-through, including reading an old flat-key Redis session with the new getter.

---

## Phase 0: Close Uncommitted Work — DONE

Pagination (`qpage`/`epage`/`delpage`), `qview:{post_id}` preview, `tests/test_router_queue.py`, `_QUEUE_PAGE_SIZE`, `_render_post_edit_list` — landed and merged.

---

## Implementation Order & Dependencies

```
Phase 0 (done) → Phase 1 → Phase 3
                        ↘ Phase 4
Phase 2 (independent, can run in parallel with Phase 1)
```

---

## Files NOT Changed

`core/notifier.py`, `core/scheduler.py`, `core/time_picker.py`, `core/utils.py`, `core/config.py`, `core/fsm_storage.py`, `core/timezone_resolver.py`, `core/healthcheck.py`, `core/logging_setup.py`, `telegram/admin.py`, `core/webapp.py`, `core/webapp_static/admin.html`.

The eight test files listed in Phase 1 will have import paths updated. All tests must pass after each phase.

---

## Verification

| Phase | Check |
|-------|-------|
| 0 | `pytest tests/test_router_queue.py -q` (done) |
| 1 | `pytest -q` green (incl. 8 updated test import paths); manually verify every command responds; extract domain-by-domain |
| 2 | Fresh DB → tables + `schema_migrations`={1..5}; re-run → unchanged; **live prod → baseline records versions, data intact**; corrupt SQL in file N → rollback, version N absent, N-1 applied |
| 3 | `pytest -q`; new service unit tests without aiogram; manual broadcast + draft RBAC |
| 4 | `pytest -q`; full walk of `/schedule` `/repeat` `/broadcast` `/drafts`; old flat-key Redis session reads correctly via new getter |
