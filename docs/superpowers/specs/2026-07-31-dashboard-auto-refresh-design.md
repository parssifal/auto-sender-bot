# Design: Admin Dashboard Auto-Refresh

**Date:** 2026-07-31
**Status:** Draft — ready for planning
**Scope:** Small frontend-only enhancement — the admin Mini App refreshes stats automatically instead of only via the manual "↻" button.
**Scale:** 1–10 users.
**Roadmap:** Item 3 of `docs/superpowers/2026-07-31-roadmap-post-refactor.md`. Plan in a later session.

---

## Problem

`core/webapp_static/admin.html` refreshes stats only when the admin clicks "↻". Open dashboards go stale.

## Goals

1. The dashboard auto-refreshes its stats on an interval without user action.
2. It's cheap and doesn't hammer the server; it pauses when the tab is hidden.
3. The manual "↻" button stays (immediate refresh).

## Non-Goals

- No real-time push (SSE / websocket) — overkill at this scale (YAGNI).
- No backend change — `GET /api/stats` already returns the full aggregate bundle and the ↻ button already consumes it.
- No auto-refresh of write-heavy or expensive views beyond stats.

## Current-State Grounding

- `GET /api/stats` → `collect_admin_stats(store)` bundle; the ↻ handler in `admin.html` already fetches and re-renders it.
- `admin.html` is a single self-contained file (vanilla JS + inline SVG), theme-aware, renders demo data outside Telegram.

## Architecture (frontend-only, `admin.html`)

- Wrap the existing fetch-and-render in a reusable `refreshStats()` and call it from both the ↻ handler and a timer.
- `setInterval(refreshStats, INTERVAL_MS)` with ⚠ `INTERVAL_MS` = 30–60s (decide in planning; 45s reasonable).
- **Pause when hidden:** guard on `document.visibilityState === "visible"`; refresh once immediately on `visibilitychange` back to visible so a returning admin sees fresh data.
- **"Last updated" indicator:** show a relative/absolute timestamp updated on each successful refresh.
- **Overlap guard:** skip a tick if a previous fetch is still in flight (a simple in-flight boolean).
- ⚠ Optional: also auto-refresh the currently-open user-detail card (`/api/user/{id}`), or leave detail views manual. **Recommendation:** stats only in v1; detail stays manual.

## Verification

- Manual: open the panel, watch stats update on the interval without clicking; switch tabs away and confirm polling pauses (no network calls); switch back and confirm an immediate refresh + updated timestamp.
- Confirm the ↻ button still forces an immediate refresh and doesn't double-fire with the timer.
- No backend/test changes expected; if `admin.html` has any JS unit harness, add a small test for `refreshStats` in-flight guard.
