# Phase 4 — Typed FSM Contexts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace untyped `dict[str, Any]` FSM data (~26 string keys) with per-flow typed dataclasses composed from shared mixins, giving typo-proof, autocompleted access in handlers — while keeping FSM storage as flat keys so in-flight Redis/memory sessions survive a mid-flow deploy.

**Architecture:** A new pure module `telegram/handlers/contexts.py` defines mixin dataclasses (`PostContent`, `DateTimePick`, `PreviewRef`) and five per-flow contexts (`ScheduleContext`, `RepeatContext`, `BroadcastContext`, `DraftContext`, `EditContext`). Typed getters/patchers in `helpers.py` read `state.get_data()` and hydrate a dataclass from **only the keys present**, and write back via `state.update_data(**flat_keys)`. Handlers are migrated one flow at a time to read through the getters; **no storage key names change**, so partially-migrated and pre-deploy sessions keep working. Settings flows (Timezone/Language/Destinations) intentionally stay untyped (YAGNI).

**Tech Stack:** Python 3.10+, `dataclasses`, aiogram 3.25 FSM (`FSMContext`), pytest / pytest-asyncio.

**Baseline:** 254 tests passing on `main` (verified 2026-07-31). Branch: `refactor/phase4-typed-fsm-contexts`.

---

## Spec-vs-Reality Corrections (READ FIRST)

The design spec (`docs/superpowers/specs/2026-04-12-modular-refactoring-design.md`, Phase 4) is the source of intent, but two grounding facts differ from the sketch and **override** it:

1. **`draft_text` / `draft_entities_json` are cross-flow working fields, not draft-only.** The shared collect handler `schedule_collect_post` (`telegram/handlers/shared.py:545–623`) reads and writes `draft_text`/`draft_entities_json` for *every* flow (schedule/repeat/broadcast/draft/edit), and every flow-init point initializes them to `None` (`helpers.py:205–206`, `drafts.py:25–26,166–167`, `queue.py:283–284`). Therefore they belong in the shared **`PostContent`** mixin, NOT only in `DraftContext` as the spec's snippet shows.
   - The finalized content fields (`kind`, `text`, `entities_json`, `caption`, `caption_entities_json`) are the *committed* form produced at summary-build time (`helpers.py:518,590`; `shared.py:861,917`); `draft_text`/`draft_entities_json`/`text_before_media` are the *in-progress* working form. Both sets live on `PostContent`.

2. **All `update_data(...)` call sites use flat kwargs only** — no nested-dict or `**spread` forms (verified: 41 call sites, all `update_data(key=value, ...)`). This makes the flat-key patcher a drop-in: `patch_*_ctx(state, **changes)` == `state.update_data(**changes)`.

Everything else in the spec's Phase 4 section (mixin composition, flat-key serialization rule, one-flow-at-a-time migration, settings flows stay untyped) holds as written.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `telegram/handlers/contexts.py` | Pure dataclasses: 3 mixins + 5 flow contexts + a `field_names()` helper. No store, no aiogram, no I/O. | **Create** |
| `telegram/handlers/helpers.py` | Add typed getters/patchers (`get_schedule_ctx`/`patch_schedule_ctx`, …). Reads `state.get_data()`, hydrates dataclass from present keys only; patcher forwards flat kwargs. | **Modify** |
| `telegram/handlers/shared.py` | Cross-flow dispatch (`schedule_collect_post`, `cb_media_clear`, `cb_media_done`, `cb_confirm_yes`, datetime handlers) reads via typed getters. | **Modify** |
| `telegram/handlers/schedule.py` | Schedule dest-selection reads via `get_schedule_ctx`. | **Modify** |
| `telegram/handlers/recurring.py` | Repeat flow reads via `get_repeat_ctx`. | **Modify** |
| `telegram/handlers/broadcast.py` | Broadcast flow reads via `get_broadcast_ctx`. | **Modify** |
| `telegram/handlers/drafts.py` | Draft flow reads via `get_draft_ctx`. | **Modify** |
| `telegram/handlers/queue.py` | Edit flow reads via `get_edit_ctx`. | **Modify** |
| `tests/test_fsm_contexts.py` | Unit tests for dataclasses + getters/patchers, incl. old flat-key ("pre-deploy Redis") session hydration. | **Create** |

**Non-goals / NOT changed:** `states.py`, `keyboards.py`, `settings.py` (untyped settings flows stay), `core/*`, `telegram/admin.py`, `core/webapp.py`. Getters/patchers live in `helpers.py` (not a new module) because they are store-adjacent handler utilities and every feature module already imports `helpers`.

---

## Design of the typed contexts

```python
# telegram/handlers/contexts.py
from __future__ import annotations
from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class PostContent:
    """Post body — collected/finalized by the cross-flow media & confirm handlers.

    Working fields (during collection): draft_text, draft_entities_json,
      media_items, caption_above, text_before_media.
    Finalized fields (at summary build): kind, text, entities_json, caption,
      caption_entities_json.
    """
    kind: str | None = None                       # "text" | "media"
    text: str | None = None
    entities_json: str | None = None
    caption: str | None = None
    caption_entities_json: str | None = None
    caption_above: bool = False
    text_before_media: bool = False
    draft_text: str | None = None
    draft_entities_json: str | None = None
    media_items: list[dict] = field(default_factory=list)


@dataclass
class DateTimePick:
    calendar_year: int | None = None
    calendar_month: int | None = None
    selected_date: str | None = None
    scheduled_at_utc: int | None = None
    scheduled_local: str | None = None


@dataclass
class PreviewRef:
    preview_msg_ids: list[int] = field(default_factory=list)
    preview_chat_id: int | None = None


@dataclass
class ScheduleContext(PostContent, DateTimePick, PreviewRef):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0


@dataclass
class RepeatContext(PostContent, DateTimePick, PreviewRef):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0
    interval_type: str | None = None


@dataclass
class BroadcastContext(PostContent, DateTimePick, PreviewRef):
    selected_chat_ids: list[int] = field(default_factory=list)
    dest_page: int = 0


@dataclass
class DraftContext(PostContent, DateTimePick, PreviewRef):
    chat_id: int | None = None
    team_id: int | None = None
    draft_publish_id: str | None = None


@dataclass
class EditContext(PostContent, DateTimePick, PreviewRef):
    edit_post_id: str | None = None
    edit_draft_id: str | None = None
    edit_preserve_caption_above: bool = False


def field_names(cls: type) -> frozenset[str]:
    """Storage keys owned by a context dataclass (for present-key hydration)."""
    return frozenset(f.name for f in fields(cls))
```

**Notes for the implementer:**
- `PreviewRef` is included in every flow context because `_send_post_preview` (`helpers.py:406,461`) writes `preview_msg_ids`/`preview_chat_id` during any flow that previews. The spec listed it as a mixin but didn't compose it — do compose it.
- `dest_page` added to `RepeatContext` (spec omitted it): `recurring.py:136` writes `dest_page`.
- `draft_text`/`draft_entities_json` moved to `PostContent` per Correction #1 — so `DraftContext` no longer declares them itself (it inherits them).
- **MRO / default-field rule:** every field across all mixins has a default, so multiple-inheritance composition raises no "non-default after default" `TypeError`. Keep it that way — never add a defaultless field.
- Verify the final field set of each context is a **superset** of the keys that flow actually reads/writes (grep each key). Any key a flow touches but no context declares is a bug in this design — surface it, don't silently drop it.

## Design of the getters/patchers (helpers.py)

```python
# add near the other FSM helpers in telegram/handlers/helpers.py
from telegram.handlers.contexts import (
    ScheduleContext, RepeatContext, BroadcastContext, DraftContext, EditContext,
    field_names,
)

async def _get_ctx(state, cls):
    data = await state.get_data()
    keys = field_names(cls)
    return cls(**{k: v for k, v in data.items() if k in keys})

async def get_schedule_ctx(state) -> ScheduleContext:
    return await _get_ctx(state, ScheduleContext)

async def get_repeat_ctx(state) -> RepeatContext:
    return await _get_ctx(state, RepeatContext)

async def get_broadcast_ctx(state) -> BroadcastContext:
    return await _get_ctx(state, BroadcastContext)

async def get_draft_ctx(state) -> DraftContext:
    return await _get_ctx(state, DraftContext)

async def get_edit_ctx(state) -> EditContext:
    return await _get_ctx(state, EditContext)
```

Patchers are intentionally **not** separate functions — writes stay as the existing `await state.update_data(**flat_keys)` calls (Correction #2), which are already flat and backward-compatible. Adding `patch_*` wrappers would be indirection for no gain (YAGNI). The typed layer is **read-side only**; that alone eliminates the key-name-typo class of bugs the spec targets, and keeps the diff minimal and reviewable.

> Rationale for read-only: mismatched writes are caught at read time (a mistyped write key simply won't hydrate into the dataclass and will surface in tests/manual walk). Symmetric typed writes are a possible follow-up but out of scope for Phase 4 — note it in the memory update, don't build it.

### CRITICAL: keep existing coercion around typed reads

The getter hydrates the **raw stored value** with **no coercion**, and a *present* `None` key **overrides** the dataclass default (e.g. `ScheduleContext(selected_chat_ids=None).selected_chat_ids` is `None`, not `[]`). Several current reads coerce, and that coercion **must stay**:

| Current read (coerced) | After swap — KEEP the wrapper |
|---|---|
| `kb._normalize_selected_chat_ids(data.get("selected_chat_ids"))` (broadcast.py:80/109, shared.py:785/835, helpers.py:339) | `kb._normalize_selected_chat_ids(ctx.selected_chat_ids)` |
| `int(data.get("dest_page", 0) or 0)` (broadcast.py:82) | `int(ctx.dest_page or 0)` |
| `list(data.get("media_items", []))` | `list(ctx.media_items)` |
| `bool(data.get("caption_above", False))` | `bool(ctx.caption_above)` |

**Do not drop these wrappers when swapping to `ctx.<field>`.** Dropping them silently changes behavior for a present-`None`/malformed session — and because well-formed sessions still pass the guard-rail tests, the regression would be **silent**. When in doubt, wrap the typed read exactly as the raw read was wrapped.

---

## Task 1: Context dataclasses

**Files:**
- Create: `telegram/handlers/contexts.py`
- Test: `tests/test_fsm_contexts.py`

- [ ] **Step 1: Write failing tests for the dataclasses**

```python
# tests/test_fsm_contexts.py
from telegram.handlers.contexts import (
    PostContent, DateTimePick, PreviewRef,
    ScheduleContext, RepeatContext, BroadcastContext, DraftContext, EditContext,
    field_names,
)


def test_postcontent_defaults():
    c = PostContent()
    assert c.kind is None and c.caption_above is False
    assert c.media_items == [] and c.text_before_media is False
    assert c.draft_text is None and c.draft_entities_json is None


def test_media_items_not_shared_between_instances():
    a, b = ScheduleContext(), ScheduleContext()
    a.media_items.append({"x": 1})
    assert b.media_items == []  # field(default_factory) — no shared mutable default


def test_schedule_context_field_superset():
    # keys the schedule flow reads/writes (from audit)
    expected = {
        "kind", "text", "entities_json", "caption", "caption_entities_json",
        "caption_above", "text_before_media", "draft_text", "draft_entities_json",
        "media_items", "calendar_year", "calendar_month", "selected_date",
        "scheduled_at_utc", "scheduled_local", "preview_msg_ids", "preview_chat_id",
        "selected_chat_ids", "dest_page",
    }
    assert expected <= field_names(ScheduleContext)


def test_draft_context_extra_fields():
    fn = field_names(DraftContext)
    assert {"chat_id", "team_id", "draft_publish_id"} <= fn
    assert {"draft_text", "draft_entities_json"} <= fn  # inherited from PostContent


def test_edit_context_extra_fields():
    fn = field_names(EditContext)
    assert {"edit_post_id", "edit_draft_id", "edit_preserve_caption_above"} <= fn


def test_repeat_context_has_interval_and_dest_page():
    fn = field_names(RepeatContext)
    assert {"interval_type", "dest_page", "selected_chat_ids"} <= fn


def test_all_contexts_have_preview_ref():
    for cls in (ScheduleContext, RepeatContext, BroadcastContext, DraftContext, EditContext):
        assert {"preview_msg_ids", "preview_chat_id"} <= field_names(cls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fsm_contexts.py -q`
Expected: FAIL — `ModuleNotFoundError: telegram.handlers.contexts`.

- [ ] **Step 3: Create `contexts.py`**

Use the full module from the "Design of the typed contexts" section above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fsm_contexts.py -q`
Expected: PASS (all dataclass tests green).

- [ ] **Step 5: Commit**

```bash
git add telegram/handlers/contexts.py tests/test_fsm_contexts.py
git commit -m "feat(fsm): add typed FSM context dataclasses (phase 4)"
```

---

## Task 2: Typed getters + flat-key hydration (deploy-safety)

**Files:**
- Modify: `telegram/handlers/helpers.py` (add getters near existing FSM helpers)
- Test: `tests/test_fsm_contexts.py` (append)

- [ ] **Step 1: Write failing tests using a fake FSM state**

```python
# append to tests/test_fsm_contexts.py
import pytest
from telegram.handlers.helpers import (
    get_schedule_ctx, get_draft_ctx, get_edit_ctx, get_repeat_ctx, get_broadcast_ctx,
)


class FakeState:
    """Minimal stand-in for aiogram FSMContext.get_data()."""
    def __init__(self, data): self._data = dict(data)
    async def get_data(self): return dict(self._data)


@pytest.mark.asyncio
async def test_getter_hydrates_only_present_keys():
    # simulates an OLD flat-key Redis session captured mid-flow before deploy
    state = FakeState({
        "media_items": [{"type": "photo", "file_id": "abc"}],
        "caption_above": True,
        "draft_text": "hi",
        "selected_chat_ids": [111, 222],
        "dest_page": 2,
        "stray_legacy_key": "ignored",   # unknown keys must not crash hydration
    })
    ctx = await get_schedule_ctx(state)
    assert ctx.media_items == [{"type": "photo", "file_id": "abc"}]
    assert ctx.caption_above is True
    assert ctx.draft_text == "hi"
    assert ctx.selected_chat_ids == [111, 222]
    assert ctx.dest_page == 2
    # keys absent from storage fall back to dataclass defaults
    assert ctx.kind is None
    assert ctx.scheduled_at_utc is None


@pytest.mark.asyncio
async def test_getter_empty_state_all_defaults():
    ctx = await get_draft_ctx(FakeState({}))
    assert ctx.chat_id is None and ctx.team_id is None
    assert ctx.media_items == [] and ctx.caption_above is False


@pytest.mark.asyncio
async def test_edit_getter_reads_edit_fields():
    ctx = await get_edit_ctx(FakeState({
        "edit_post_id": "p1", "edit_preserve_caption_above": True,
        "scheduled_at_utc": 1700000000,
    }))
    assert ctx.edit_post_id == "p1"
    assert ctx.edit_preserve_caption_above is True
    assert ctx.scheduled_at_utc == 1700000000
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_fsm_contexts.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_schedule_ctx'`.

- [ ] **Step 3: Add getters to `helpers.py`**

Insert the getter block from "Design of the getters/patchers" above. Place the `from telegram.handlers.contexts import ...` with the other `telegram.handlers.*` imports at the top of `helpers.py`; confirm no circular import (`contexts.py` imports nothing from the package).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_fsm_contexts.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite still green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — 254 + new tests, no regressions.

- [ ] **Step 6: Commit**

```bash
git add telegram/handlers/helpers.py tests/test_fsm_contexts.py
git commit -m "feat(fsm): typed getters with flat-key hydration (deploy-safe)"
```

---

## Task 3: Migrate cross-flow dispatch in `shared.py`

The highest-value target: `shared.py` reads FSM data with `data.get("...")` in the cross-flow handlers that touch every domain. Replace those reads with typed-getter access. **Writes stay as `state.update_data(...)`** (flat keys, unchanged).

**Files:**
- Modify: `telegram/handlers/shared.py`
- Test: rely on existing `tests/test_router_schedule_flow.py`, `test_router_repeat_flow.py`, `test_router_drafts.py`, `test_router_edit_posts.py`, `test_router_broadcast.py` (behavioral coverage of these handlers).

- [ ] **Step 1: Confirm the pre-existing behavioral tests pass (guard rail)**

Run: `.venv/bin/python -m pytest tests/test_router_schedule_flow.py tests/test_router_repeat_flow.py tests/test_router_drafts.py tests/test_router_edit_posts.py tests/test_router_broadcast.py -q`
Expected: PASS (this is the safety net for a behavior-preserving refactor).

- [ ] **Step 2: Refactor `schedule_collect_post` (`shared.py:545–623`) reads**

Replace the block of `data.get("media_items")`, `data.get("draft_text")`, `data.get("draft_entities_json")`, `data.get("caption_above")`, `data.get("text_before_media")` with a single `ctx = await get_ctx_for_current_state(state, current_state)` typed read. Add a small dispatch helper in `shared.py` (module-local, not exported):

```python
async def _get_content_ctx(state, current_state):
    """Return the right typed context for whichever flow owns current_state.
    All content flows share the PostContent fields we read here, so we only
    need PostContent-level access — but return the flow-correct type so the
    cross-flow handlers that later read flow-specific fields stay correct."""
    from telegram.handlers.helpers import (
        get_schedule_ctx, get_repeat_ctx, get_broadcast_ctx, get_draft_ctx, get_edit_ctx,
    )
    from telegram.handlers.states import (
        ScheduleStates, RepeatStates, BroadcastStates, DraftStates, EditStates,
    )
    if current_state in {s.state for s in RepeatStates.__all_states__}:      return await get_repeat_ctx(state)
    if current_state in {s.state for s in BroadcastStates.__all_states__}:   return await get_broadcast_ctx(state)
    if current_state in {s.state for s in DraftStates.__all_states__}:       return await get_draft_ctx(state)
    if current_state in {s.state for s in EditStates.__all_states__}:        return await get_edit_ctx(state)
    return await get_schedule_ctx(state)
```

Then in `schedule_collect_post`:
```python
ctx = await _get_content_ctx(state, current_state)
media = list(ctx.media_items)
draft_text = ctx.draft_text
draft_entities_json = ctx.draft_entities_json
caption_above = ctx.caption_above
text_before_media = ctx.text_before_media
```
Leave all `await state.update_data(...)` writes exactly as they are.

> **Two more content reads live in the edit branch below the snippet:** `data.get("edit_preserve_caption_above")` (`shared.py:602`) and a second `data.get("caption_above", False)` (`shared.py:606`). Either keep the local `data = await state.get_data()` binding for those, or convert them to `ctx.edit_preserve_caption_above` / `ctx.caption_above` (available because `_get_content_ctx` returns an `EditContext` for edit states). Do **not** delete the `data` binding without converting these two — that's a `NameError`.

> Verify `StatesGroup.__all_states__` is the correct aiogram 3.x introspection attribute for "all states in this group"; if the installed aiogram exposes it differently, use `current_state in SomeStates` (aiogram supports `state_string in StatesGroup` membership) instead. Pick whichever the installed version supports — check with a one-liner before coding:
> `.venv/bin/python -c "from telegram.handlers.states import ScheduleStates as S; print(hasattr(S,'__all_states__')); print(ScheduleStates.collecting_post.state in S)"`

- [ ] **Step 3: Run the guard-rail tests**

Run: `.venv/bin/python -m pytest tests/test_router_schedule_flow.py tests/test_router_repeat_flow.py tests/test_router_drafts.py tests/test_router_edit_posts.py tests/test_router_broadcast.py -q`
Expected: PASS — behavior unchanged.

- [ ] **Step 4: Refactor `cb_confirm_yes` reads (`shared.py` ~840–1010) and datetime handlers**

Replace `data.get("kind")`, `data.get("caption_above")`, `data.get("media_items")`, `data.get("caption_entities_json")`, `data.get("selected_chat_ids")`, `data.get("scheduled_at_utc")`, `data.get("calendar_year")`, etc. with typed-ctx reads via `_get_content_ctx`. Keep `cb_media_clear`/`cb_media_done` state-membership guards and all writes as-is.

- [ ] **Step 5: Full suite green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — no regressions.

- [ ] **Step 6: Commit**

```bash
git add telegram/handlers/shared.py
git commit -m "refactor(fsm): shared cross-flow handlers read via typed contexts"
```

---

## Task 4: Migrate `schedule.py`

**Files:** Modify `telegram/handlers/schedule.py`; guard rail `tests/test_router_schedule_flow.py`.

- [ ] **Step 1:** Run `tests/test_router_schedule_flow.py` — PASS.
- [ ] **Step 2:** Replace `data.get("selected_chat_ids")` / `data.get("dest_page")` reads with `ctx = await get_schedule_ctx(state)` then `ctx.selected_chat_ids` / `ctx.dest_page`. Writes stay `update_data(...)`.
- [ ] **Step 3:** Run `tests/test_router_schedule_flow.py` — PASS.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 5:** Commit `refactor(fsm): schedule flow reads via ScheduleContext`.

---

## Task 5: Migrate `recurring.py` (repeat flow)

**Files:** Modify `telegram/handlers/recurring.py`; guard rail `tests/test_router_repeat_flow.py`.

- [ ] **Step 1:** Run `tests/test_router_repeat_flow.py` — PASS.
- [ ] **Step 2:** Replace `data.get(...)` reads (`selected_chat_ids`, `dest_page`, `interval_type`, `chat_id`) with `get_repeat_ctx(state)`. Writes unchanged.
- [ ] **Step 3:** Run `tests/test_router_repeat_flow.py` — PASS.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 5:** Commit `refactor(fsm): repeat flow reads via RepeatContext`.

---

## Task 6: Migrate `broadcast.py`

**Files:** Modify `telegram/handlers/broadcast.py`; guard rail `tests/test_router_broadcast.py`.

- [ ] **Step 1:** Run `tests/test_router_broadcast.py` — PASS.
- [ ] **Step 2:** Replace `data.get(...)` reads (`selected_chat_ids`, `dest_page`) with `get_broadcast_ctx(state)`. Writes unchanged.
- [ ] **Step 3:** Run `tests/test_router_broadcast.py` — PASS.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 5:** Commit `refactor(fsm): broadcast flow reads via BroadcastContext`.

---

## Task 7: Migrate `drafts.py`

**Files:** Modify `telegram/handlers/drafts.py`; guard rail `tests/test_router_drafts.py`.

- [ ] **Step 1:** Run `tests/test_router_drafts.py` — PASS.
- [ ] **Step 2:** Replace `data.get(...)` reads (`chat_id`, `team_id`, `draft_publish_id`, `dest_page`, plus any `PostContent` reads) with `get_draft_ctx(state)`. Writes unchanged. Note draft-publish resolution already goes through `draft_svc` (Phase 3) — only the FSM read sites change here.
- [ ] **Step 3:** Run `tests/test_router_drafts.py` — PASS.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 5:** Commit `refactor(fsm): draft flow reads via DraftContext`.

---

## Task 8: Migrate `queue.py` (edit flow)

**Files:** Modify `telegram/handlers/queue.py`; guard rail `tests/test_router_edit_posts.py`, `tests/test_router_queue.py`.

- [ ] **Step 1:** Run `tests/test_router_edit_posts.py tests/test_router_queue.py` — PASS.
- [ ] **Step 2:** Replace edit-flow `data.get(...)` reads (`edit_post_id`, `edit_draft_id`, `edit_preserve_caption_above`, plus `PostContent`/`DateTimePick` reads) with `get_edit_ctx(state)`. Leave pagination reads that are pure list-render (non-FSM) alone; only FSM `state.get_data()` reads change. Writes unchanged.
- [ ] **Step 3:** Run `tests/test_router_edit_posts.py tests/test_router_queue.py` — PASS.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 5:** Commit `refactor(fsm): edit flow reads via EditContext`.

---

## Task 9: `helpers.py` shared summary/preview reads + sweep

`helpers.py` has flow-agnostic FSM reads in `_build_*_summary` / `_send_post_preview` (`helpers.py:518,590,737`). These are called from multiple flows with the same `PostContent` keys.

**Files:** Modify `telegram/handlers/helpers.py`.

- [ ] **Step 1:** Full suite green first: `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 2:** Where a summary/preview helper reads only `PostContent`/`DateTimePick` keys and already receives `state`, replace `data = await state.get_data(); data.get("kind")...` with a typed getter. If a helper is genuinely flow-agnostic and only needs `PostContent`, add and use a `get_post_content(state) -> PostContent` getter (hydrates PostContent fields only). Do NOT force a flow type where the flow isn't known.
- [ ] **Step 3:** Final grep sweep — confirm no *content/datetime* FSM key is still read via raw `data.get("<key>")` in migrated handlers (settings flows and non-FSM dict reads are exempt):
```bash
grep -rnE "data\.get\(['\"](kind|text|entities_json|caption|caption_entities_json|caption_above|text_before_media|draft_text|draft_entities_json|media_items|calendar_year|calendar_month|selected_date|scheduled_at_utc|scheduled_local|preview_msg_ids|preview_chat_id|selected_chat_ids|dest_page|interval_type|chat_id|team_id|draft_publish_id|edit_post_id|edit_draft_id|edit_preserve_caption_above)['\"]" telegram/handlers/*.py
```
Expected: only intentional exceptions remain (document any). Writes via `update_data` are expected to remain.

> **Known intended exception — do NOT migrate `_clear_live_preview` (`helpers.py:391–406`).** It reads `preview_chat_id`/`preview_msg_ids` via `data.get`, but its logic hinges on a key-**presence** check (`if "preview_msg_ids" in data or "preview_chat_id" in data`) that a dataclass cannot express (a hydrated `PreviewRef` always has both fields with defaults, losing the "was it ever set?" distinction). The Task 9 grep will flag this function — that flag is expected; leave it on raw-dict access and record it as the documented exception.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` — PASS.
- [ ] **Step 5:** Commit `refactor(fsm): shared summary/preview helpers read via typed contexts`.

---

## Task 10: Docs, memory, and final verification

**Files:** Modify spec status; update project memory.

- [ ] **Step 1:** Mark Phase 4 done in `docs/superpowers/specs/2026-04-12-modular-refactoring-design.md` (Phase 4 heading + implementation-order box), referencing this plan and the spec-vs-reality corrections (draft_text in PostContent; read-only typed layer; PreviewRef composed into all contexts).
- [ ] **Step 2:** Final full run with count:
```bash
.venv/bin/python -m pytest -q 2>&1 | tail -3
```
Expected: `254 + N passed` (N = new context tests), 0 failures.
- [ ] **Step 3:** pyflakes clean:
```bash
.venv/bin/python -m pyflakes telegram/handlers/ 2>&1 | tail -20
```
Expected: no new warnings (baseline may have pre-existing ones — compare).
- [ ] **Step 4:** Update `project_architecture.md` memory: Phase 4 DONE, `contexts.py` + typed getters, flat-key storage unchanged, read-only typed layer, symmetric typed writes noted as a possible follow-up.
- [ ] **Step 5:** Commit `docs(spec): mark phase 4 typed FSM contexts as implemented`.

---

## Verification (from the design spec)

| Check | Command / action |
|-------|------------------|
| Unit: dataclasses + hydration | `pytest tests/test_fsm_contexts.py -q` |
| No regressions | `pytest -q` — 254 + N green |
| **Deploy safety** — old flat-key session reads correctly via new getter | covered by `test_getter_hydrates_only_present_keys` (simulates pre-deploy Redis session with stray legacy key) |
| Manual walk | run bot; `/schedule`, `/repeat`, `/broadcast`, `/drafts`, edit-a-queued-post — compose text+media, pick datetime, confirm; verify preview + final post identical to pre-refactor |
| No key-name drift | Task 9 grep sweep shows content/datetime keys no longer read via raw `data.get(...)` in migrated handlers |

## Risks & Mitigations

- **MRO / default-field ordering:** all fields have defaults → safe. Enforced by `test_postcontent_defaults` and construction in every getter test.
- **Mutable default sharing:** `field(default_factory=list)` for `media_items`/`selected_chat_ids`/`preview_msg_ids` → `test_media_items_not_shared_between_instances` guards it.
- **Behavior drift during read-swap:** each flow migrates under its own pre-existing behavioral test suite as a guard rail (Tasks 3–8 Step 1). Writes are untouched, shrinking the blast radius to read expressions only.
- **Circular import:** `contexts.py` imports only stdlib; `helpers.py` imports `contexts`; feature modules already import `helpers`. No cycle.
- **aiogram introspection (`__all_states__` vs `in StatesGroup`):** verified with a one-liner in Task 3 Step 2 before use.
