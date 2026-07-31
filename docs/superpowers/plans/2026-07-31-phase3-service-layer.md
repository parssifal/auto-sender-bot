# Phase 3: Service Layer (light, orchestration-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the pure, non-Telegram orchestration currently inlined in the broadcast / draft-publish / team-invite handlers into a thin `core/services/` layer that reads and writes only through the DAL, so that logic becomes unit-testable without aiogram mocks — with zero behavior change.

**Architecture:** Three new modules — `core/services/{broadcast_svc,draft_svc,team_svc}.py` — expose free async functions that take `store: StateStore` as the first parameter (mirroring the handler/DAL convention). Handlers keep everything that touches the Telegram API (admin checks, `message.answer`, FSM state, i18n `tr(...)` formatting) and delegate only the DAL-backed decisions/writes to the services. Transactional DAL methods (`create_broadcast_posts`, `accept_team_invite`) and all stats aggregates stay in `state.py` (decision A1); `rbac.py` stays untouched.

**Tech Stack:** Python 3.12, aiogram 3.25, aiosqlite 0.21, SQLite 3.50, pytest / pytest-asyncio.

---

## Design decisions locked for this plan

These resolve gaps between the design spec (`docs/superpowers/specs/2026-04-12-modular-refactoring-design.md`, "Phase 3") and the actual handler code re-audited on `main` (2026-07-31). The spec's one-line "what moves" table understates how entangled the confirm flow is with Telegram I/O; these decisions make the boundary rule ("services: no Telegram I/O, no raw SQL") concrete.

1. **The real publish orchestration lives in `shared.py::cb_confirm_yes` (the cross-flow `sconf:yes` handler), not in `broadcast.py`/`drafts.py`.** `telegram/handlers/broadcast.py` only does destination selection; `telegram/handlers/drafts.py::_start_draft_publish` only primes FSM state. The actual `store.create_broadcast_posts(...)` call (`shared.py:855-874`) and the draft→`create_scheduled_*_post` call (`shared.py:967-985`) are branches of `cb_confirm_yes` (`shared.py:810-1000`). **Decision:** Phase 3 extracts from `cb_confirm_yes`, plus the resolve/gate helpers those branches call.

2. **Admin checks are Telegram I/O and STAY in the handler.** Each confirm branch interleaves `_check_user_admin` / `_check_bot_admin_and_post` (both call the Bot API) *between* resolving destinations and creating posts. These cannot move into a service. **Decision:** services expose a *resolve* step and a *create* step as separate functions; the handler runs the admin-check loop between them. This keeps the pre-existing ordering (resolve → per-chat admin check → create) byte-for-byte.

3. **`broadcast_svc` = resolve + create, both pure DAL.**
   - `resolve_valid_destinations(store, user_id, selected_chat_ids) -> list[tuple[int, str]]` — moves the body of `helpers._resolve_broadcast_destinations` (`helpers.py:384-392`) into the service. `helpers._resolve_broadcast_destinations` becomes a thin re-export/delegate so `_resolve_broadcast_destination_lines` and any other caller keep working unchanged.
   - `create_broadcast(store, *, user_id, chat_ids, scheduled_at_utc, content: PostContent) -> list[str]` — the text-vs-media dispatch of `shared.py:854-874`, calling the unchanged transactional `store.create_broadcast_posts`.
   - `PostContent` is a small local `@dataclass` (kind + text/entities OR caption/caption_entities/caption_above/media_items) so the two `create_*` functions have a typed input instead of a loose dict. It is a service-layer value object, **not** the Phase 4 FSM dataclass (that's a separate, later concern); keep them independent.

4. **`draft_svc` = permission-gated resolve + publish, pure DAL+DAL-wrapped-rbac.**
   - `resolve_publishable_draft(store, draft_id, user_id) -> DraftRow | None` — the gate at `shared.py:949-954` (`get_draft_permissions` + `can_publish`). Uses `store.get_draft_permissions` (the DAL wrapper that already calls `rbac.resolve_draft_permissions`), so the service reads via the DAL, honoring the boundary rule.
   - `publish_draft(store, *, user_id, draft, scheduled_at_utc) -> str` — the draft→post dispatch of `shared.py:967-985` (fetch `get_draft_media` for media, call `create_scheduled_text_post`/`create_scheduled_media_post`).
   - `resolve_draft_by_ref(store, user_id, draft_ref, *, need) -> tuple[DraftRow | None, DraftPermissions | None]` — DRYs the identical 5×-repeated block in `drafts.py` (`list_drafts` → `_resolve_draft_id` → `get_draft` + `get_draft_permissions` → check `can_{view,edit,delete,publish}`). `need` ∈ `{"view","edit","delete","publish"}`. `_resolve_draft_id` stays in `helpers.py` (pure string matcher) and is imported by the service.

5. **`team_svc` = invite preparation only; accept handling stays in the handler.**
   - `prepare_team_invite(store, owner_id, team_ref, role) -> InvitePreparation` — the role-validation + owned-team resolution + `create_team_invite` logic of `teams.py:91-113`, returning a small result object (`status` ∈ `{"ok","role_invalid","team_missing"}`, plus `invite` and `team` when ok). The handler keeps `message.bot.me()`, link building, `tr(...)`, and `_main_menu_for` (all Telegram/i18n).
   - **Accept (`_handle_team_invite_start`) is NOT wrapped.** `store.accept_team_invite` is already a single transactional DAL method; the handler's only remaining work is mapping `result.status` → an i18n key and rendering — pure presentation. Wrapping it would be grouping-for-grouping's-sake (same A1 rationale the spec used for stats aggregates). Documented here so a reviewer doesn't flag the omission.

6. **Services take `store` as the first positional arg and never import aiogram, `telegram.i18n`, or anything under `telegram/`.** Import direction is one-way: `telegram/handlers/* → core/services/* → core/{state,rbac}`. A guard test asserts no `telegram`/`aiogram` import leaks into `core/services`.

7. **No signature/behavior change to any DAL method, any `tr(...)` string, or any FSM key.** Extraction is mechanical: the handler calls a service function where it used to run the inlined block; outputs are identical. All 233 existing tests stay green throughout; new tests are added per service.

---

## File Structure

- Create: `core/services/__init__.py` — empty package marker.
- Create: `core/services/broadcast_svc.py` — `PostContent` dataclass, `resolve_valid_destinations`, `create_broadcast`.
- Create: `core/services/draft_svc.py` — `resolve_publishable_draft`, `publish_draft`, `resolve_draft_by_ref`.
- Create: `core/services/team_svc.py` — `InvitePreparation` dataclass, `prepare_team_invite`.
- Create: `tests/test_broadcast_svc.py`, `tests/test_draft_svc.py`, `tests/test_team_svc.py` — pure service unit tests (real in-memory `StateStore`, no aiogram).
- Create: `tests/test_services_boundary.py` — asserts `core/services/*` imports no `telegram`/`aiogram`.
- Modify: `telegram/handlers/helpers.py:384-392` — `_resolve_broadcast_destinations` delegates to `broadcast_svc.resolve_valid_destinations`.
- Modify: `telegram/handlers/shared.py:830-874` (broadcast confirm) and `:942-985` (draft confirm) — delegate to the services.
- Modify: `telegram/handlers/drafts.py` — the 5 command/callback gate blocks use `draft_svc.resolve_draft_by_ref` / `resolve_publishable_draft`.
- Modify: `telegram/handlers/teams.py:81-113` (`cmd_team_invite`) — use `team_svc.prepare_team_invite`.
- Reference (do NOT modify): `core/state.py`, `core/rbac.py`, `telegram/admin.py`, `core/webapp.py`.

**Existing tests as the safety net:** `tests/test_state_broadcast.py`, `test_state_drafts.py`, `test_draft_rbac.py`, and the `test_router_*` flow tests exercise these exact paths end-to-end. Run the full suite after every task; a regression there means the extraction changed behavior.

**Test conventions (verified 2026-07-31):** there is **no shared `store` fixture** in `tests/conftest.py` (it only sets `sys.path`). Each `test_state_*.py` defines its **own** module-level `@pytest_asyncio.fixture async def store()` that builds `StateStore(conn)`, `await state.migrate()`, seeds rows, `yield`s, then `await conn.close()` — and marks async tests with `@pytest.mark.asyncio`. New service test files follow the same pattern (copy the 4-line fixture from `tests/test_state_broadcast.py:15-33`). Seed destinations with `store.upsert_destination(chat_id, "channel", title, username, "administrator", True)`; read broadcast/scheduled posts back with `store.list_pending_posts(user_id, limit=...)` (fields `.chat_id`, `.kind`, `.text`) — the same accessors the existing broadcast tests use. Do **not** assume `add_destination`/`get_scheduled_post`-style helpers unless a grep confirms them.

---

## Task 0: Scaffold the services package

**Files:**
- Create: `core/services/__init__.py`
- Test: `tests/test_services_boundary.py`

- [ ] **Step 1: Write the failing boundary test**

```python
# tests/test_services_boundary.py
import ast
import pathlib

SERVICES_DIR = pathlib.Path(__file__).resolve().parent.parent / "core" / "services"


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_services_do_not_import_telegram_or_aiogram():
    offenders: dict[str, set[str]] = {}
    for path in SERVICES_DIR.glob("*.py"):
        bad = {m for m in _imports(path) if m.split(".")[0] in {"telegram", "aiogram"}}
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"services must not import telegram/aiogram: {offenders}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_services_boundary.py -q`
Expected: FAIL — `core/services` does not exist (glob empty → collection error, or `SERVICES_DIR` missing).

- [ ] **Step 3: Create the package**

```python
# core/services/__init__.py
```
(empty file)

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_services_boundary.py -q`
Expected: PASS (0 offenders — dir exists, no modules yet).

- [ ] **Step 5: Commit**

```bash
git add core/services/__init__.py tests/test_services_boundary.py
git commit -m "chore(services): scaffold core/services package + import-boundary guard"
```

---

## Task 1: `broadcast_svc.resolve_valid_destinations`

**Files:**
- Create: `core/services/broadcast_svc.py`
- Modify: `telegram/handlers/helpers.py:384-392`
- Test: `tests/test_broadcast_svc.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_broadcast_svc.py
import aiosqlite
import pytest
import pytest_asyncio

from core.services import broadcast_svc
from core.state import StateStore


@pytest_asyncio.fixture
async def store():
    conn = await aiosqlite.connect(":memory:")   # match tests/test_state_broadcast.py:15-33 exactly
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


@pytest.mark.asyncio
async def test_resolve_valid_destinations_filters_unknown_and_labels(store):
    uid = 42
    await store.ensure_user(uid)
    await store.upsert_destination(-100, "channel", "Alpha", "alpha", "administrator", True)
    await store.upsert_destination(-200, "channel", "Beta", None, "administrator", True)

    resolved = await broadcast_svc.resolve_valid_destinations(store, uid, [-100, -999, -200])

    assert [chat_id for chat_id, _ in resolved] == [-100, -200]   # -999 dropped, order preserved
    assert resolved[0][1]  # non-empty human label
```

> Copy the fixture + seeding **verbatim** from `tests/test_state_broadcast.py:15-33` (it uses `aiosqlite.connect(":memory:")` and `upsert_destination`). Confirm `upsert_destination` alone makes the destination show up in `list_user_destinations` for `uid`; if the existing broadcast test also calls `link_user_destination`, mirror that.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_broadcast_svc.py -q`
Expected: FAIL — `AttributeError: module 'core.services.broadcast_svc' has no attribute 'resolve_valid_destinations'` (module missing).

- [ ] **Step 3: Write the implementation (move logic out of helpers)**

```python
# core/services/broadcast_svc.py
from __future__ import annotations

from core.state import StateStore
from telegram.handlers.keyboards import _normalize_selected_chat_ids, _destination_label
```

> **Boundary note (decision 6):** `keyboards.py` is pure (no store, no Telegram I/O) — but it still lives under `telegram/` and the boundary guard test (`test_services_boundary.py`) fails on ANY `telegram.*` import. So these two pure symbols must be treated like the ref-matchers: **move `_normalize_selected_chat_ids` and `_destination_label` into a new `core/services/_shared.py`** (or `core/text.py`) and leave re-exports in `keyboards.py` (`from core.services._shared import ...`), so `keyboards.py`/handlers/tests keep importing them from the old path. Confirm both symbols are only *pure* (grep their bodies — they are, per `keyboards.py:98,631`). Update the boundary test if you'd rather special-case `keyboards` — but moving is cleaner and consistent with the ref-matchers.

`_list_all_user_destinations` (helpers.py:329) is **trivial** — just `count_user_destinations` + `list_user_destinations(offset=0, limit=total)`, no team-merge. The service owns the same two DAL calls directly:

```python
async def resolve_valid_destinations(
    store: StateStore, user_id: int, selected_chat_ids: list[int]
) -> list[tuple[int, str]]:
    total = await store.count_user_destinations(user_id)
    destinations = (
        await store.list_user_destinations(user_id=user_id, offset=0, limit=total)
        if total > 0 else []
    )
    destination_map = {d.chat_id: d for d in destinations}
    resolved: list[tuple[int, str]] = []
    for chat_id in _normalize_selected_chat_ids(selected_chat_ids):
        d = destination_map.get(chat_id)
        if d is None:
            continue
        resolved.append((chat_id, _destination_label(d.title, d.username)))
    return resolved
```

> This reproduces `_list_all_user_destinations` + `_resolve_broadcast_destinations` exactly. Optionally simplify `helpers._list_all_user_destinations` to delegate too, but that's not required — leave it if it risks touching unrelated callers.

- [ ] **Step 4: Point the helper at the service**

```python
# telegram/handlers/helpers.py — replace body of _resolve_broadcast_destinations
async def _resolve_broadcast_destinations(store, user_id, selected_chat_ids):
    from core.services import broadcast_svc
    return await broadcast_svc.resolve_valid_destinations(store, user_id, selected_chat_ids)
```

> Local import avoids any import cycle (`helpers` → `services` → … ). Keep `_resolve_broadcast_destination_lines` untouched — it now calls the delegating helper.

- [ ] **Step 5: Run the new test + the full suite**

Run: `pytest tests/test_broadcast_svc.py -q && pytest -q`
Expected: new test PASS; full suite still 233 passing (broadcast flow tests unchanged).

- [ ] **Step 6: Commit**

```bash
git add core/services/broadcast_svc.py telegram/handlers/helpers.py tests/test_broadcast_svc.py
git commit -m "refactor(services): extract broadcast destination resolution into broadcast_svc"
```

---

## Task 2: `broadcast_svc.create_broadcast` + wire into confirm handler

**Files:**
- Modify: `core/services/broadcast_svc.py`
- Modify: `telegram/handlers/shared.py:854-874`
- Test: `tests/test_broadcast_svc.py`

- [ ] **Step 1: Write the failing test (text + media)**

```python
@pytest.mark.asyncio
async def test_create_broadcast_text_creates_one_post_per_chat(store):
    uid = 7
    await store.ensure_user(uid)
    content = broadcast_svc.PostContent(kind="text", text="hi", entities_json=None)
    post_ids = await broadcast_svc.create_broadcast(
        store, user_id=uid, chat_ids=[-100, -200], scheduled_at_utc=1_900_000_000, content=content
    )
    assert len(post_ids) == 2
    pending = await store.list_pending_posts(uid, limit=10)   # same accessor as test_state_broadcast.py
    assert {p.chat_id for p in pending} == {-100, -200}
    assert {p.kind for p in pending} == {"text"}


@pytest.mark.asyncio
async def test_create_broadcast_media_passes_items_through(store):
    uid = 8
    await store.ensure_user(uid)
    content = broadcast_svc.PostContent(
        kind="media", caption="c", caption_entities_json=None, caption_above=True,
        media_items=[{"type": "photo", "file_id": "F1"}],
    )
    post_ids = await broadcast_svc.create_broadcast(
        store, user_id=uid, chat_ids=[-100], scheduled_at_utc=1_900_000_000, content=content
    )
    assert len(post_ids) == 1
```

> `list_pending_posts` is what `tests/test_state_broadcast.py` uses to read broadcast posts back — reuse it rather than inventing accessors. (These two tests mirror the existing `test_create_broadcast_*_posts_*` tests but drive the flow through `create_broadcast` instead of the raw DAL, proving the service is a faithful pass-through.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_broadcast_svc.py -q`
Expected: FAIL — `PostContent` / `create_broadcast` undefined.

- [ ] **Step 3: Implement `PostContent` + `create_broadcast`**

```python
# core/services/broadcast_svc.py — add
from dataclasses import dataclass, field


@dataclass
class PostContent:
    kind: str                                   # "text" | "media"
    text: str | None = None
    entities_json: str | None = None
    caption: str | None = None
    caption_entities_json: str | None = None
    caption_above: bool = False
    media_items: list[dict] = field(default_factory=list)


async def create_broadcast(
    store: StateStore, *, user_id: int, chat_ids: list[int],
    scheduled_at_utc: int, content: PostContent,
) -> list[str]:
    if content.kind == "text":
        return await store.create_broadcast_posts(
            user_id=user_id, chat_ids=chat_ids, scheduled_at_utc=scheduled_at_utc,
            kind="text", text=str(content.text or ""), entities_json=content.entities_json,
        )
    return await store.create_broadcast_posts(
        user_id=user_id, chat_ids=chat_ids, scheduled_at_utc=scheduled_at_utc,
        kind="media", caption=content.caption,
        caption_entities_json=content.caption_entities_json,
        caption_above=bool(content.caption_above), media_items=list(content.media_items),
    )
```

> Copy the keyword args **exactly** from `shared.py:855-874`. Do not "clean up" arg names.

- [ ] **Step 4: Wire the confirm handler to the service**

In `shared.py::cb_confirm_yes`, the `BroadcastStates.confirming` branch — replace the inline `if kind == "text": ... else: ...` `store.create_broadcast_posts(...)` block (`:854-874`) with:

```python
from core.services import broadcast_svc   # top-of-file import

if kind == "text":
    content = broadcast_svc.PostContent(kind="text", text=data.get("text"),
                                        entities_json=data.get("entities_json"))
else:
    content = broadcast_svc.PostContent(
        kind="media", caption=data.get("caption"),
        caption_entities_json=data.get("caption_entities_json"),
        caption_above=bool(data.get("caption_above", False)),
        media_items=list(data.get("media_items", [])),
    )
post_ids = await broadcast_svc.create_broadcast(
    store, user_id=user_id, chat_ids=selected_chat_ids,
    scheduled_at_utc=scheduled_at_utc, content=content,
)
```

> Leave the resolve step (`_resolve_broadcast_destinations`, now service-backed via the helper), the admin-check loop, `state.clear()`, and the `broadcast_created_ok` rendering exactly as they are.

- [ ] **Step 5: Run new tests + full suite**

Run: `pytest tests/test_broadcast_svc.py -q && pytest -q`
Expected: PASS; 233+ still green (broadcast router/flow tests unchanged).

- [ ] **Step 6: Commit**

```bash
git add core/services/broadcast_svc.py telegram/handlers/shared.py tests/test_broadcast_svc.py
git commit -m "refactor(services): move broadcast post creation into broadcast_svc.create_broadcast"
```

---

## Task 3: `draft_svc.resolve_publishable_draft` + `publish_draft` + wire draft confirm

**Files:**
- Create: `core/services/draft_svc.py`
- Modify: `telegram/handlers/shared.py:942-985`
- Test: `tests/test_draft_svc.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_draft_svc.py
import pytest

from core.services import draft_svc


@pytest.mark.asyncio
async def test_resolve_publishable_draft_returns_none_without_permission(store):
    # Seed a team draft owned by author A; acting user B is a viewer (can't publish).
    ...  # reuse seeding from tests/test_draft_rbac.py / test_state_drafts.py
    assert await draft_svc.resolve_publishable_draft(store, draft_id, viewer_id) is None


@pytest.mark.asyncio
async def test_resolve_publishable_draft_returns_draft_for_author(store):
    ...
    draft = await draft_svc.resolve_publishable_draft(store, draft_id, author_id)
    assert draft is not None and draft.id == draft_id


@pytest.mark.asyncio
async def test_publish_draft_text_creates_scheduled_post(store):
    ...  # author + text draft
    post_id = await draft_svc.publish_draft(store, user_id=author_id, draft=draft,
                                            scheduled_at_utc=1_900_000_000)
    post = await store.get_scheduled_post(post_id)
    assert post.kind == "text" and post.chat_id == draft.chat_id
```

> Lift seeding helpers from `tests/test_draft_rbac.py` (it already builds authors/teams/roles). Do not re-derive RBAC seeding from scratch.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_draft_svc.py -q`
Expected: FAIL — module/functions undefined.

- [ ] **Step 3: Implement `draft_svc`**

```python
# core/services/draft_svc.py
from __future__ import annotations

from core.state import StateStore, DraftRow   # confirm DraftRow export location


async def resolve_publishable_draft(store: StateStore, draft_id: str, user_id: int) -> DraftRow | None:
    permissions = await store.get_draft_permissions(draft_id, user_id)
    if permissions is None or not permissions.can_publish:
        return None
    return await store.get_draft(draft_id)


async def publish_draft(store: StateStore, *, user_id: int, draft: DraftRow, scheduled_at_utc: int) -> str:
    if draft.kind == "text":
        return await store.create_scheduled_text_post(
            user_id=user_id, chat_id=draft.chat_id, scheduled_at_utc=scheduled_at_utc,
            text=str(draft.text or ""), entities_json=draft.entities_json,
        )
    media_items = await store.get_draft_media(draft.id)
    return await store.create_scheduled_media_post(
        user_id=user_id, chat_id=draft.chat_id, scheduled_at_utc=scheduled_at_utc,
        caption=draft.caption, caption_entities_json=draft.caption_entities_json,
        caption_above=None if draft.caption_above is None else bool(draft.caption_above),
        media_items=media_items,
    )
```

> The arg lists are copied verbatim from `shared.py:967-985`. Note the `caption_above` `None`-preserving ternary — keep it.

- [ ] **Step 4: Wire the draft confirm branch**

In `shared.py::cb_confirm_yes`, `DraftStates.confirming` branch — replace the `get_draft_permissions`+`get_draft` gate (`:949-954`) with `resolve_publishable_draft`, and the text/media create block (`:967-985`) with `publish_draft`:

```python
from core.services import draft_svc   # top-of-file import

draft = await draft_svc.resolve_publishable_draft(store, draft_id, user_id)
if draft is None:
    await state.clear()
    await query.message.answer(tr(lang, "draft_missing"),
                               reply_markup=await _main_menu_for(store, query.from_user.id))
    return

# ... admin checks unchanged (Telegram I/O stays here) ...

post_id = await draft_svc.publish_draft(store, user_id=user_id, draft=draft,
                                        scheduled_at_utc=scheduled_at_utc)
```

> The `isinstance(draft_id, str)` guard (`:944-947`), admin checks, `state.clear()`, and `draft_post_created_ok` rendering stay untouched.

- [ ] **Step 5: Run new tests + full suite**

Run: `pytest tests/test_draft_svc.py -q && pytest -q`
Expected: PASS; full suite green (draft flow/RBAC tests unchanged).

- [ ] **Step 6: Commit**

```bash
git add core/services/draft_svc.py telegram/handlers/shared.py tests/test_draft_svc.py
git commit -m "refactor(services): extract draft publish gate + creation into draft_svc"
```

---

## Task 4: `draft_svc.resolve_draft_by_ref` + DRY the drafts.py command gates

**Files:**
- Modify: `core/services/draft_svc.py`
- Modify: `telegram/handlers/drafts.py` (cmd_draft_edit :226-250, cmd_draft_delete :252-283, cmd_draft_post :285-309, cb_draft_action publish/edit branches :459-496)
- Test: `tests/test_draft_svc.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("need", ["view", "edit", "delete", "publish"])
async def test_resolve_draft_by_ref_gates_on_permission(store, need):
    ...  # viewer on a team draft: only "view" resolves; edit/delete/publish -> (None, perms)
    draft, perms = await draft_svc.resolve_draft_by_ref(store, viewer_id, short_ref, need=need)
    if need == "view":
        assert draft is not None
    else:
        assert draft is None and perms is not None


@pytest.mark.asyncio
async def test_resolve_draft_by_ref_unknown_ref_returns_none_none(store):
    draft, perms = await draft_svc.resolve_draft_by_ref(store, uid, "zzzz", need="view")
    assert draft is None and perms is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_draft_svc.py -k resolve_draft_by_ref -q`
Expected: FAIL — function undefined.

- [ ] **Step 3: Implement `resolve_draft_by_ref`**

```python
# core/services/draft_svc.py — add
from core.rbac import DraftPermissions
from telegram.handlers.helpers import _resolve_draft_id   # pure string matcher
```

> **Boundary note:** `_resolve_draft_id` is a pure function but lives under `telegram/handlers/` — importing it into a service would violate decision 6 and the boundary guard test. **Resolution:** move `_resolve_draft_id` (and only it) from `helpers.py` into `core/services/draft_svc.py` (or a new `core/services/_refs.py` if `team` needs a sibling), and leave a re-export in `helpers.py` (`from core.services.draft_svc import _resolve_draft_id`) so existing `helpers`/handler callers and `tests/` imports keep working. Do the same for `_resolve_team_id` in Task 5. Verify with `grep -rn "_resolve_draft_id" telegram/ tests/` and update the boundary test expectation (it should still pass — the symbol now originates in `core`).

```python
_NEED_ATTR = {"view": "can_view", "edit": "can_edit",
              "delete": "can_delete", "publish": "can_publish"}


async def resolve_draft_by_ref(
    store: StateStore, user_id: int, draft_ref: str, *, need: str
) -> tuple[DraftRow | None, DraftPermissions | None]:
    drafts = await store.list_drafts(user_id, scope="all", limit=200)
    draft_id = _resolve_draft_id(drafts, draft_ref)
    if draft_id is None:
        return None, None
    permissions = await store.get_draft_permissions(draft_id, user_id)
    if permissions is None or not getattr(permissions, _NEED_ATTR[need]):
        return None, permissions
    draft = await store.get_draft(draft_id)
    return draft, permissions
```

- [ ] **Step 4: Rewrite the 4 gate sites in `drafts.py`**

Each currently does: `list_drafts` → `_resolve_draft_id` → `get_draft` + `get_draft_permissions` → check. Replace with a single `resolve_draft_by_ref` call, preserving the exact `draft_missing` responses. Example for `cmd_draft_post` (`:285-309`):

```python
draft, _ = await draft_svc.resolve_draft_by_ref(
    store, message.from_user.id, parts[1].strip().lower(), need="publish")
if draft is None:
    lang = await h._user_lang(store, message.from_user.id)
    await message.answer(tr(lang, "draft_missing"),
                         reply_markup=await h._main_menu_for(store, message.from_user.id))
    return
await _start_draft_publish(store, message, state, user_id=message.from_user.id, draft=draft)
```

> Apply the analogous change to `cmd_draft_edit`, `cmd_draft_delete`, and the `edit`/`publish` branches of `cb_draft_action`. The `cb_draft_*` callbacks that already have a `draft_id` in the callback data (open/delete_prompt/delete_confirm) use `get_draft` + `get_draft_permissions` directly on a known id — for those, a smaller helper `resolve_draft_by_id(store, draft_id, user_id, need=...)` is cleaner; **add it too** and use it there. Keep each `tr(...)` response identical.

- [ ] **Step 5: Run tests + full suite**

Run: `pytest tests/test_draft_svc.py -q && pytest -q`
Expected: PASS; full suite green (`test_router_drafts.py` is the regression guard).

- [ ] **Step 6: Commit**

```bash
git add core/services/draft_svc.py telegram/handlers/drafts.py telegram/handlers/helpers.py tests/test_draft_svc.py
git commit -m "refactor(services): DRY draft permission-gated resolution via draft_svc.resolve_draft_by_ref"
```

---

## Task 5: `team_svc.prepare_team_invite` + wire cmd_team_invite

**Files:**
- Create: `core/services/team_svc.py`
- Modify: `telegram/handlers/teams.py:81-113`, `telegram/handlers/helpers.py` (re-export `_resolve_team_id`)
- Test: `tests/test_team_svc.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_team_svc.py
import pytest

from core.services import team_svc


@pytest.mark.asyncio
async def test_prepare_team_invite_rejects_bad_role(store):
    result = await team_svc.prepare_team_invite(store, owner_id=1, team_ref="abcd", role="admin")
    assert result.status == "role_invalid"


@pytest.mark.asyncio
async def test_prepare_team_invite_missing_team(store):
    await store.ensure_user(1)
    result = await team_svc.prepare_team_invite(store, owner_id=1, team_ref="zzzz", role="viewer")
    assert result.status == "team_missing"


@pytest.mark.asyncio
async def test_prepare_team_invite_ok_returns_invite_and_team(store):
    await store.ensure_user(1)
    team_id = await store.create_team(1, "Team X")
    short = team_id[:4]   # confirm how _short_id/_resolve_team_id matches refs
    result = await team_svc.prepare_team_invite(store, owner_id=1, team_ref=short, role="editor")
    assert result.status == "ok"
    assert result.team is not None and result.invite is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_team_svc.py -q`
Expected: FAIL — module/functions undefined.

- [ ] **Step 3: Implement `team_svc`**

```python
# core/services/team_svc.py
from __future__ import annotations

from dataclasses import dataclass

from core.state import StateStore, Team, TeamInvite   # confirm exact type names/exports
from core.services.draft_svc import _resolve_team_id   # moved here in Step 4 below


@dataclass
class InvitePreparation:
    status: str                       # "ok" | "role_invalid" | "team_missing"
    invite: "TeamInvite | None" = None
    team: "Team | None" = None


async def prepare_team_invite(
    store: StateStore, *, owner_id: int, team_ref: str, role: str
) -> InvitePreparation:
    if role not in {"viewer", "editor"}:
        return InvitePreparation(status="role_invalid")
    owned_teams = await store.list_owned_teams(owner_id, limit=200)
    team_id = _resolve_team_id(owned_teams, team_ref)
    if team_id is None:
        return InvitePreparation(status="team_missing")
    try:
        invite = await store.create_team_invite(team_id, owner_id, role)
    except ValueError:
        return InvitePreparation(status="team_missing")
    team = next((t for t in owned_teams if t.id == team_id), None)
    if team is None:
        return InvitePreparation(status="team_missing")
    return InvitePreparation(status="ok", invite=invite, team=team)
```

> Adjust the signature of `prepare_team_invite` to accept `owner_id` positionally if you prefer keyword-only — match the test. `_resolve_team_id` moves out of `helpers.py` for the same boundary reason as `_resolve_draft_id` (Task 4 Step 3); leave a re-export in `helpers.py`. Confirm `Team`/`TeamInvite` dataclass names in `state.py` and import them for typing only (or use string annotations to avoid a hard import if they're internal).

- [ ] **Step 4: Wire `cmd_team_invite`**

Replace `teams.py:91-113` (role check → `list_owned_teams` → `_resolve_team_id` → `create_team_invite` → find team) with:

```python
prep = await team_svc.prepare_team_invite(
    store, owner_id=message.from_user.id, team_ref=team_ref, role=role)
if prep.status == "role_invalid":
    await message.answer(tr(lang, "team_invite_role_invalid"),
                         reply_markup=await h._main_menu_for(store, message.from_user.id))
    return
if prep.status != "ok":
    await message.answer(tr(lang, "team_missing"),
                         reply_markup=await h._main_menu_for(store, message.from_user.id))
    return
invite, team = prep.invite, prep.team
# ... unchanged: bot.me(), invite_link building, tz_name, tr("team_invite_created", ...) ...
```

> `role` was already lowercased/parsed above (`teams.py:92`); pass it through. The `team_invite_role_invalid` early-return at `:93-95` is now covered by `prepare_team_invite` — remove the now-dead inline check to avoid double validation (or keep it and let the service agree — pick one; removing is DRYer).

- [ ] **Step 5: Run tests + full suite**

Run: `pytest tests/test_team_svc.py -q && pytest -q`
Expected: PASS; full suite green (team invite paths exercised by existing state/router tests).

- [ ] **Step 6: Commit**

```bash
git add core/services/team_svc.py telegram/handlers/teams.py telegram/handlers/helpers.py tests/test_team_svc.py
git commit -m "refactor(services): extract team invite preparation into team_svc"
```

---

## Task 6: Update architecture docs + memory pointer

**Files:**
- Modify: `docs/superpowers/specs/2026-04-12-modular-refactoring-design.md` (mark Phase 3 DONE, like Phase 2)
- Modify: memory `project_architecture.md` roadmap line for Phase 3 (via the memory workflow, not committed to the repo)

- [ ] **Step 1: Mark Phase 3 DONE in the spec**

Add a "Phase 3 — Service Layer — DONE (2026-07-31)" note mirroring the Phase 2 DONE paragraph: list the created modules, the "resolve/create split so admin checks stay in the handler" decision, and the new test files. Reference this plan.

- [ ] **Step 2: Run the full suite one last time**

Run: `pytest -q`
Expected: all green (233 prior + new service tests).

- [ ] **Step 3: pyflakes clean**

Run: `python -m pyflakes core/services telegram/handlers` (or the project's configured linter)
Expected: no unused-import warnings (watch for now-unused imports in `shared.py`/`drafts.py`/`teams.py` after extraction).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-12-modular-refactoring-design.md
git commit -m "docs(spec): mark phase 3 service layer as implemented"
```

- [ ] **Step 5: Update project memory** (outside the repo, per the memory workflow) — update the Phase 3 roadmap line in `project_architecture.md` to ✅ with the module list.

---

## Verification (maps to spec's Phase 3 row)

| Check | Command / criterion |
|-------|---------------------|
| Unit tests without aiogram | `pytest tests/test_broadcast_svc.py tests/test_draft_svc.py tests/test_team_svc.py -q` — all green, no aiogram imported |
| Boundary honored | `pytest tests/test_services_boundary.py -q` — services import no `telegram`/`aiogram` |
| No behavior change | `pytest -q` — full suite (233 prior tests) still green after every task |
| Lint | `python -m pyflakes core/services telegram/handlers` — clean |
| Manual (spec) | Manual broadcast to 2 chats + team-draft publish with an editor and a viewer (RBAC): identical messages to pre-refactor |

## Risks & mitigations

- **Import cycles / boundary leaks:** `core.services` importing anything under `telegram/` (`helpers`, `keyboards`, ...) both risks a cycle AND trips the boundary guard test. Mitigation: services never import from `telegram/`; the pure helpers they need — the ref-matchers (`_resolve_draft_id`, `_resolve_team_id`) and the two broadcast pure symbols (`_normalize_selected_chat_ids`, `_destination_label`) — MOVE into `core/services/` (e.g. `_shared.py`) with re-exports left behind in their old modules so existing callers/tests are unaffected. The boundary test enforces this after every task.
- **`_list_all_user_destinations` is trivial** (`count_user_destinations` + `list_user_destinations(offset=0, limit=total)`, verified helpers.py:329) — no team-merge — so `broadcast_svc.resolve_valid_destinations` owns the two DAL calls directly with no duplicated logic.
- **Dead code after extraction:** removing inline blocks can orphan imports in `shared.py`/`teams.py`/`helpers.py`. The pyflakes step (Task 6) is the gate.
- **Test fixtures:** there is NO shared `store` fixture — each new test file defines its own, copied verbatim from `tests/test_state_broadcast.py:15-33` (`aiosqlite.connect(":memory:")` → `StateStore` → `migrate()`), and marks async tests `@pytest.mark.asyncio`.
