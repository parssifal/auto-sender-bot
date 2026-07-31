# Design: Tech-Debt Cleanup (Post-Refactor)

**Date:** 2026-07-31
**Status:** ✅ RESOLVED (2026-07-31). Owner chose to implement **4a, 4b, 4c** despite their defer recommendations below; **4d** recorded in Obsidian instead of archiving the (gitignored) `TODO.md`; **4e** was already in sync. All merged to local `main` (`f6b9358` + `c9dac73`), 300 tests green, not pushed to origin. See Work Log 2026-07-31 "Тех-долг (roadmap item 4)". Recommendation text below is kept for historical context.
**Original status:** Draft — a menu of independent items, each with its own recommendation.
**Scope:** Maintenance/hardening — no new user-facing features.
**Scale:** 1–10 users.
**Roadmap:** Item 4 of `docs/superpowers/2026-07-31-roadmap-post-refactor.md`. Each sub-item can be its own small plan (or skipped). Plans in later sessions.

---

## Problem

After the 4-phase refactor the codebase is clean (no TODO/FIXME markers, 264 tests green). A few optional items remain. **None blocks features.** This spec records them so they aren't lost, with an explicit "do it / defer it" call on each — several are deliberate YAGNI defers.

---

## 4a. Split `core/state.py` (~2160 lines) — optional "Phase 5"

**Situation:** The last remaining large module. Phase 3 decision **A1** deliberately kept read-only stats aggregates *and* transactional methods in the DAL, so the size is intentional, not accidental sprawl.

**If done:** split by domain (users / destinations / posts / recurring / drafts / teams / stats) behind the **same `StateStore` facade**, so callers and tests are untouched — mirror the Phase 1–3 discipline (extract one domain at a time, `pytest -q` green after each; keep `migrate()` and transactional boundaries intact). Candidate approach: mixin classes or submodule functions composed into `StateStore`.

**Recommendation:** **Defer.** Only do this if `state.py` starts causing real navigation/merge-conflict pain. At current scale it's readable enough; a split is churn for cosmetic gain. Revisit if a future feature adds significantly to it.

## 4b. Symmetric typed FSM writes (`patch_*_ctx`)

**Situation:** Phase 4 typed only FSM *reads*; writes stay flat `update_data(**keys)`. A mistyped write key currently surfaces only at read time (the wrong key simply won't hydrate).

**If done:** add `patch_schedule_ctx(state, **changes)` etc. (thin wrappers over `update_data`) and route writes through them, or a typed-field write helper that validates keys against the dataclass fields.

**Recommendation:** **Defer (YAGNI).** No write-side typo bug has occurred. Add only if one does. Recorded in the Phase 4 spec as a known follow-up.

## 4c. Finish typing the datetime-picker nav handlers in `shared.py`

**Situation:** Phase 4 deliberately left the datetime-picker navigation handlers on defensive raw `data.get(...)` reads (they forward the raw `data` dict wholesale to `_edit_datetime_prompt` / `_prompt_for_datetime`). Partial migration is deploy-safe by design.

**If done:** introduce typed access while still producing the `data` dict those prompt helpers need (e.g. type the reads but keep passing `await state.get_data()` to the pure prompt/keyboard layer).

**Recommendation:** **Defer / low priority.** Consistency-only; the current reads are already coerced and correct. Not worth the churn unless touching that code for another reason.

## 4d. Archive / retire `TODO.md` (109 KB)

**Situation:** `TODO.md` is the historical v2.0 build plan (time picker / recurring / drafts-teams) — **all shipped**. It has no live checkboxes and dwarfs the real docs. It misleads anyone treating it as a backlog.

**If done:** move to `docs/archive/TODO-v2.0.md` (preserve history) or delete; update `README.md` / `AGENTS.md` to point contributors at `docs/superpowers/` + the roadmap as the source of truth.

**Recommendation:** **Do it — cheap, reduces confusion.** Good candidate to bundle with 4e.

## 4e. Push `main` to `origin`

**Situation:** Local `main` is ahead of `origin/main` by ~48 commits (Phases 2–4 not on GitHub). The work isn't backed up remotely.

**If done:** `git push origin main` (owner decision — confirm before pushing, since it publishes history).

**Recommendation:** **Do it early**, independent of everything else. ⚠ Requires owner's explicit go-ahead to push (publishing action).

---

## Suggested batching

- **Now, cheap:** 4d (archive TODO.md) + 4e (push main) — small housekeeping, do together.
- **When it hurts:** 4a (state.py split) — reactive, not proactive.
- **Only if a bug appears:** 4b.
- **Opportunistic:** 4c — fold into any future `shared.py` datetime work.

## Verification

- 4a: `pytest -q` green after each extracted domain; `StateStore` public surface unchanged (import + method-presence check).
- 4d: docs build/readme links updated; no code references `TODO.md`.
- 4e: `git status` shows `main` in sync with `origin/main` after push.
