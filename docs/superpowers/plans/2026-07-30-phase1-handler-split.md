# Phase 1: Split router.py into handler modules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 4,235-line `telegram/router.py` into a `telegram/handlers/` package of focused feature modules, with **zero behavior change**.

**Architecture:** Dissolve the `build_router(store)` closure. All helpers and handlers become module-level functions that take `store` as an explicit parameter. Each feature domain gets its own module exposing `build_router(store) -> Router`; `telegram/router.py` becomes a thin assembler that includes the sub-routers in priority order. See spec `docs/superpowers/specs/2026-04-12-modular-refactoring-design.md` (Phase 1).

**Tech Stack:** Python 3.10+, aiogram 3.25 (Router/StatesGroup/FSMContext), aiosqlite, pytest.

---

## Ground Rules (read before every task)

**This is a behavior-preserving refactor, not feature work.** There are no new tests to write. The safety net is the existing suite:

> **The gate for every task: `python3 -m pytest -q` reports `227 passed` (or more, never fewer, never a failure).** Baseline confirmed on `main` at commit `123b5b8`.

Additional invariants:
- **Run inside the worktree** `.worktrees/phase1` on branch `refactor/phase1-handler-split`.
- **No logic edits.** Move code verbatim. The only permitted mechanical change is closure dissolution (below). If you feel tempted to "improve" a function, don't — that is a separate phase.
- **Handler registration order is load-bearing** (aiogram 3.x: earlier router wins). Preserve relative order within a module, and include `shared` first at the top level.
- **Commit after every task** with a green suite. Small commits.
- Use `python3` (not `python`) on this machine.

### Closure Dissolution Procedure (the one mechanical transformation)

Handlers and helpers currently live inside `def build_router(store):` as closures capturing `store`. To move one out:

1. Cut the function to the target module at **module level** (dedent one level).
2. Add `store: StateStore` as the **first** parameter of helpers (matching the spec: `_user_lang(user_id)` → `_user_lang(store, user_id)`); handlers keep aiogram's expected signature and receive `store` via a partial (below). Be consistent — store-first everywhere.
3. In the module's `build_router(store)`, register each handler with `store` bound. Two acceptable patterns — pick one and stay consistent:
   - **Nested registration (lowest risk):** keep a thin `def build_router(store)` in the feature module whose body defines the aiogram-decorated wrappers that delegate to the module-level `_impl(..., store)` functions. Verbatim-friendly.
   - **functools.partial:** `router.callback_query(F...)(partial(cb_media_done, store=store))`.
   For Phase 1, prefer **nested registration** — it lets you move bodies with the least edit surface.
4. Update every call site to pass `store`.

---

## File Structure

```
telegram/
  handlers/
    __init__.py     ← empty package marker
    states.py       ← 8 StatesGroup classes (no store, no imports beyond aiogram.fsm)
    keyboards.py    ← pure *_kb() builders + pure token parsers/formatters (no store, no Telegram I/O)
    helpers.py      ← store-dependent + Telegram-API helpers (store passed explicitly)
    shared.py       ← cross-flow handlers + on_my_chat_member; build_router(store)
    schedule.py     ← ScheduleStates flow + /schedule; build_router(store)
    recurring.py    ← RepeatStates flow + /repeat /repeats; build_router(store)
    drafts.py       ← DraftStates flow + /drafts*; build_router(store)
    broadcast.py    ← BroadcastStates flow + /broadcast; build_router(store)
    queue.py        ← EditStates flow + /queue /edit /delete /view + pagination; build_router(store)
    settings.py     ← Timezone/Language/Destinations flows + /timezone /language /link*; build_router(store)
    teams.py        ← /team_* + invite flow; build_router(store)
  router.py         ← build_router(store): assembles sub-routers only (~40 lines)
  admin.py          ← UNCHANGED
```

**Handler-to-module assignment rule:** a handler goes to the feature module whose `StatesGroup` appears in its state filter (`ScheduleStates.*` → `schedule.py`, etc.). Command handlers (`/schedule`, `/queue`, …) go with their flow. **Cross-flow handlers go to `shared.py`** (list: `smedia:done`/`smedia:clear`, `sconf:yes`, `scancel`, all `tp:*` TimePicker callbacks, **two** cross-flow message handlers — `schedule_enter_datetime` (registered across all `entering_datetime`/`selecting_time` states) and `schedule_collect_post` (`router.py:3481`, registered across `ScheduleStates`/`RepeatStates`/`BroadcastStates`/`DraftStates`/`EditStates` `collecting_post`/`collecting_media`/`editing_post`) — and `on_my_chat_member`). When unsure, grep: `grep -n "SomeStates\." telegram/router.py`. A message/callback handler stacked across ≥2 flows' states has no single owning module ⇒ it is cross-flow ⇒ `shared.py`. Never duplicate it into feature modules.

---

## Task 1: Package skeleton + `states.py`

**Files:**
- Create: `telegram/handlers/__init__.py` (empty)
- Create: `telegram/handlers/states.py`
- Modify: `telegram/router.py` (remove the 8 StatesGroup class defs at ~`:41-94`, import them from `.handlers.states`)

- [ ] **Step 1: Create the package and move states**

Create `telegram/handlers/__init__.py` (empty). Create `telegram/handlers/states.py` with `from aiogram.fsm.state import State, StatesGroup` and the **verbatim** 8 classes currently at `router.py:41-94`: `TimezoneStates`, `LanguageStates`, `DestinationsStates`, `ScheduleStates`, `RepeatStates`, `DraftStates`, `BroadcastStates`, `EditStates`.

- [ ] **Step 2: Re-point router.py**

In `telegram/router.py`, delete the moved class bodies and add near the top:
```python
from telegram.handlers.states import (
    TimezoneStates, LanguageStates, DestinationsStates, ScheduleStates,
    RepeatStates, DraftStates, BroadcastStates, EditStates,
)
```
(Keep the names importable from `telegram.router` for now — Task 12 removes the re-export once tests point at `handlers.states`.)

- [ ] **Step 3: Verify green**

Run: `python3 -m pytest -q`
Expected: `227 passed`.

- [ ] **Step 4: Commit**

```bash
git add telegram/handlers/__init__.py telegram/handlers/states.py telegram/router.py
git commit -m "refactor(handlers): extract StatesGroup classes to handlers/states.py"
```

---

## Task 2: `keyboards.py` (pure builders + parsers)

**Files:**
- Create: `telegram/handlers/keyboards.py`
- Modify: `telegram/router.py` (remove moved defs, import from `.handlers.keyboards`)

Move the **pure, module-level** functions (no `store`, no Telegram I/O — they only build markup or parse/format tokens). By current name: `_main_menu_kb`, `_timezone_setup_kb`, `_language_kb`, `_destinations_kb`, `_broadcast_destinations_kb`, `_repeat_interval_kb`, `_media_collect_kb`, `_confirm_kb`, `_queue_cancel_kb`, `_queue_edit_kb`, `_queue_delete_kb`, `_queue_paged_kb`, `_edit_paged_kb`, `_delete_paged_kb`, `_edit_field_kb`, `_delete_confirm_kb`, `_repeats_manage_kb`, `_drafts_manage_kb`, `_draft_detail_kb`, `_draft_delete_confirm_kb`, `_draft_delete_command_kb`, `_draft_create_scope_kb`, `_schedule_calendar_kb`, `_schedule_time_kb`, `_schedule_datetime_markup`, and the pure helpers `_normalize_selected_chat_ids`, `_toggle_selected_chat_ids`, `_normalize_draft_scope`, `_draft_scope_label`, `_draft_location_label`, `_draft_preview_text`, `_draft_action_labels`, `_draft_post_prompt_text`, `_team_role_label`, `_repeat_interval_label`, `_repeat_weekdays_mask`, `_repeat_count_label`, `_schedule_quick_labels`, `_schedule_weekday_labels`, `_format_selected_date`, `_parse_calendar_date_token`, `_parse_calendar_month_token`, `_parse_time_token`, `_short_id`, `_format_local`, `_destination_label`, `_selected_date_from_state`, `_calendar_month_from_state`.

> Verify each is genuinely pure before moving. If any turns out to reference `store` or call `message.answer()`, leave it for `helpers.py` (Task 3) instead. `_format_local`/`_destination_label` take primitives (epoch/tz string, title/username) — pure.

- [ ] **Step 1:** Create `keyboards.py`; move the functions verbatim with their imports (`aiogram.types` markup classes, `datetime`, i18n `tr`/`DEFAULT_LANGUAGE`).
- [ ] **Step 2:** In `router.py`, delete moved defs; add `from telegram.handlers import keyboards as kb` and either re-import the names or reference `kb.<name>`. Simplest verbatim path: `from telegram.handlers.keyboards import (<all names>)`.
- [ ] **Step 3:** Run `python3 -m pytest -q` → `227 passed`.
- [ ] **Step 4:** Commit: `refactor(handlers): extract pure keyboard builders and parsers to handlers/keyboards.py`

---

## Task 3: `helpers.py` (store-dependent + Telegram-API helpers) + test import updates

**Files:**
- Create: `telegram/handlers/helpers.py`
- Modify: `telegram/router.py`
- Modify: `tests/test_timezone.py`, `tests/test_router_permissions.py`, `tests/test_router_schedule_logic.py`

Move the module-level helper functions that are NOT pure keyboards — the ones that call the Telegram API, validate, or will take `store`: `_is_datetime_entry_state`, `_is_time_selection_state`, `_clear_inline_markup`, `_prompt_for_datetime`, `_edit_datetime_prompt`, `_schedule_time_prompt`, `_schedule_validation_text`, `_move_to_post_collection`, `_check_user_admin`, `_check_bot_admin_and_post`, `_resolve_draft_id`, `_resolve_scheduled_post_id`, `_resolve_team_id`, `_resolve_recurring_pattern_id`, `_is_valid_tz_name`, `_resolve_timezone_input`, `_extract_media_item`, `_resolve_caption_above`, `_format_rights_check_error`, `_schedule_time_prompt`.

> These are already module-level (not closures), so this task is a straight move + import fix. The closure-captured helpers inside `build_router` are handled in Tasks 4–11.

- [ ] **Step 1:** Create `helpers.py`; move the functions verbatim with imports (`aiogram`, `Bot`, `Message`, i18n, `StateStore` type if referenced).
- [ ] **Step 2:** In `router.py`, delete moved defs; import them back: `from telegram.handlers.helpers import (...)`.
- [ ] **Step 3: Update the 3 test files that import these helpers directly.**
  - `tests/test_timezone.py`: `_is_valid_tz_name`, `_resolve_timezone_input` → `telegram.handlers.helpers`; `_timezone_setup_kb` → `telegram.handlers.keyboards`.
  - `tests/test_router_permissions.py`: `_check_bot_admin_and_post`, `_check_user_admin` → `telegram.handlers.helpers`.
  - `tests/test_router_schedule_logic.py`: repoint each imported name to `helpers` or `keyboards` (whichever now owns it). Grep the file's import block and match names against Tasks 2/3 lists.
  - `tests/test_router_queue.py`: `_queue_paged_kb`, `_edit_paged_kb`, `_delete_paged_kb` → `telegram.handlers.keyboards` (these moved in Task 2; repoint here so the suite stays green regardless of whether router.py still re-exports them). Note: `tests/test_router_preview.py` imports only `build_router` (stays in `telegram.router`) — intentionally **no change**.
- [ ] **Step 4:** Run `python3 -m pytest -q` → `227 passed`.
- [ ] **Step 5:** Commit: `refactor(handlers): extract shared helpers to handlers/helpers.py; update 3 test imports`

---

## Tasks 4–11: Extract feature modules (one per task)

**Every feature-module task follows the identical shape below.**

> **EXECUTION ORDER (revised during implementation — Option B):** `shared.py` is extracted **LAST among the modules**, not first. Discovered in the Task 4 attempt: the cross-flow handlers (`cb_media_done`, `cb_confirm_yes`, `schedule_enter_datetime`, `cb_schedule_quick/time`) dispatch by state INTO each flow's continuation helpers (`_save_scheduled_post_time`, `_move_repeat_to_destination_selection`, `_move_draft_publish_to_confirmation`, `_resolve_broadcast_destinations`, …). Extracting `shared` first would force prematurely dragging ~15 single-feature helpers into the shared layer. Instead: extract the feature modules first so each continuation helper gets its proper home, then extract `shared.py`, which **imports those continuation helpers from the feature modules**. Dependency direction stays acyclic: `shared → {schedule, recurring, drafts, broadcast, queue} → {helpers, keyboards, states}` (feature modules do NOT import `shared`).
>
> **So run: Task 5 → 6 → 7 → 8 → 9 → 10 → 11 → 4 → 12.** Until `shared` is extracted (Task 4, near the end), the cross-flow handlers remain inline closures in `router.py`'s `build_router`; after each feature extraction, update those still-inline cross-flow handlers to call the now-module-level continuation helpers via `<feature>._helper(store, …)`.
>
> **Already done (commit `6b3d910`) as the safe part of the original Task 4:** promoted the genuinely cross-cutting helpers `_user_lang(store, user_id)`, `_main_menu_for(store, user_id)`, `_clear_live_preview`, `_send_post_preview(store, …)`, `_build_scheduled_post_summary(store, …)` into `helpers.py`. These are correct regardless of ordering.

| Task | Module | Owns |
|------|--------|------|
| 4 (run 2nd-to-last) | `shared.py` | cross-flow: `smedia:done`/`smedia:clear`, `sconf:yes`, `scancel`, all `tp:*` **including `tp:noop`**, `schedule_enter_datetime`, `schedule_collect_post`, `on_my_chat_member`; imports continuation helpers from feature modules. Include shared FIRST in `build_router` (Task 12 finalizes order) |
| 5 | `schedule.py` | `ScheduleStates.*` handlers + `cmd_schedule`, `cmd_cancel` |
| 6 | `recurring.py` | `RepeatStates.*` handlers + `cmd_repeat`, `cmd_repeats` + list callbacks `rlpage:`, `rstop:` (`cb_repeats_page`, `cb_repeats_stop`) + `_render_repeats`, `_move_repeat_to_destination_selection` |
| 7 | `drafts.py` | `DraftStates.*` + `cmd_drafts`, `cmd_draft_create/edit/delete` + `_render_drafts`, `_render_draft_detail`, `_save_draft_from_state`, `_start_draft_*`, `_update_draft_from_state`, `_build_draft_summary`, `_prompt_draft_scope` |
| 8 | `broadcast.py` | `BroadcastStates.*` + `cmd_broadcast` + `_render_broadcast_destinations`, `_resolve_broadcast_destination*` |
| 9 | `queue.py` | `EditStates.*` + `/queue /edit /delete /view` + pagination (`qpage/epage/delpage/qview`) + `_render_queue_page`, `_render_edit_posts`, `_render_delete_posts`, `_start_scheduled_post_*_edit`, `_save_scheduled_post_*`, `_render_delete_confirm`, `_confirm_delete_post`, `_load_pending_post_for_edit`, `_build_scheduled_post_summary` |
| 10 | `settings.py` | `TimezoneStates`/`LanguageStates`/`DestinationsStates` + `/timezone /language /link /link_forward` + `_render_destinations`, `_list_all_user_destinations`, `_user_lang`, `_main_menu_for` |
| 11 | `teams.py` | `/team_create /team_invite /team_members` + invite-start flow + `_handle_team_invite_start` |

> `_user_lang` and `_main_menu_for` are used by many modules. Put them in `helpers.py` (promote from closure to `_user_lang(user_id, store)`), not in `settings.py`, and import where needed. Do this promotion in **Task 4** (first module task that needs them) or add a small Task 3b if cleaner — but keep them in `helpers.py`.

**Per-module task shape (template — instantiate for each of Tasks 4–11):**

**Files:**
- Create: `telegram/handlers/<module>.py`
- Modify: `telegram/router.py` (remove the moved closures; wire `include_router`)

- [ ] **Step 1: Identify the handlers.** Three sources, union them: (a) `grep -n "<StatesGroup>\." telegram/router.py` for state-filtered handlers; (b) the module's command handlers (`Command(...)`); (c) **stateless callbacks by data-prefix domain** — callbacks filtered only on `F.data.startswith("<prefix>")` with no state (e.g. `rlpage:`/`rstop:` for recurring, `qpage:`/`qview:` for queue, draft/broadcast list-page prefixes). Grep candidate prefixes: `grep -nE "startswith\(\"[a-z]+" telegram/router.py` and assign each prefix to the owning domain. Confirm none are in the shared cross-flow list (a handler stacked across ≥2 flows' states is shared, not yours). At Task 12, `router.py` must contain **zero** leftover handlers — if a callback has no obvious home, it is cross-flow → shared.
- [ ] **Step 2: Create `<module>.py` with `def build_router(store) -> Router:`.** Move the identified handlers and their private closure helpers into it, applying the Closure Dissolution Procedure (nested registration pattern). Move helpers verbatim, adding `store` param where they used the captured `store`. Import `states`, `keyboards as kb`, `helpers as h`, services/rbac/notifier as the originals did.
- [ ] **Step 3: Delete the moved closures from `build_router` in `router.py`.**
- [ ] **Step 4: Wire it up.** In `router.py`'s `build_router`, add `router.include_router(<module>.build_router(store))` in the correct order (see Task 12 for the final order; add incrementally as you go).
- [ ] **Step 5: Verify green.** `python3 -m pytest -q` → `227 passed`. If a handler's state filter or callback pattern changed behavior, the flow tests (`test_router_<flow>_flow.py`) will catch it — do not proceed until green.
- [ ] **Step 6: Manual smoke (shared + schedule + one more only).** For Tasks 4, 5, and 9, additionally note in the commit that the extracted flow should be manually walked after Phase 1 completes. (Full manual pass happens once in Task 12.)
- [ ] **Step 7: Commit.** `refactor(handlers): extract <module> flow to handlers/<module>.py`

> **Import-cycle caution:** feature modules import from `states`/`keyboards`/`helpers` only — never from each other, and never from `telegram.router`. If two feature modules need to share a handler, that handler is cross-flow → it belongs in `shared.py`.

---

## Task 12: Slim `router.py` to a thin assembler + final test import updates

**Files:**
- Modify: `telegram/router.py`
- Modify: `tests/test_router_schedule_flow.py`, `tests/test_router_repeat_flow.py`, `tests/test_router_drafts.py`, `tests/test_router_edit_posts.py`, `tests/test_router_broadcast.py`

- [ ] **Step 1: Reduce `build_router`** to only sub-router assembly, shared included first:
```python
def build_router(store: StateStore) -> Router:
    router = Router()
    router.include_router(shared.build_router(store))
    for module in (schedule, recurring, drafts, broadcast, queue, settings, teams):
        router.include_router(module.build_router(store))
    return router
```
Keep module imports at top. Remove any now-dead helper imports that were only re-exports.

- [ ] **Step 2: Remove the StatesGroup re-export** added in Task 1 (the `from telegram.handlers.states import ...` line) **only after** repointing the 5 flow test files.

- [ ] **Step 3: Update the 5 flow test imports.** In each, change `from telegram.router import <FlowStates>, build_router` to:
  - `from telegram.handlers.states import <FlowStates>`
  - `from telegram.router import build_router` (unchanged — `build_router` still lives in `telegram.router`).

- [ ] **Step 4: Verify green.** `python3 -m pytest -q` → `227 passed`. Also confirm `telegram/router.py` is now ≲60 lines: `wc -l telegram/router.py`.

- [ ] **Step 5: Full manual smoke test.** With a bot token in a scratch env, walk each command once: `/start`, `/schedule` (text + media, calendar + quick time), `/repeat`, `/broadcast`, `/queue` → view/edit/delete + pagination, `/drafts` create/edit/publish (personal + team), `/timezone`, `/language`, `/link`, `/team_create` + invite. Confirm no handler is dead or double-firing. (If no token is available in this environment, record that manual verification is deferred and must be run before merge — do NOT claim it passed.)

- [ ] **Step 6: Commit.** `refactor(handlers): reduce router.py to a thin assembler; repoint 5 flow test imports`

---

## Done criteria

- `telegram/router.py` ≲ 60 lines (only imports + `build_router` assembly).
- `telegram/handlers/` holds `states`, `keyboards`, `helpers`, `shared`, and 7 feature modules.
- `python3 -m pytest -q` → `227 passed` (unchanged count).
- All 9 previously-`telegram.router`-importing test files repointed (3 helper/kb in Tasks 2–3, `test_router_queue.py` in Task 2, 5 flow files in Task 12; `test_router_preview.py` unchanged by design).
- No feature module imports another feature module or `telegram.router`.
- Manual command walk-through green (or explicitly recorded as deferred-to-pre-merge).

## Follow-ups (NOT this plan)

- Phase 2 (migrations) can run in parallel on its own branch.
- Phase 3 (services) and Phase 4 (typed FSM) depend on this being merged first.
