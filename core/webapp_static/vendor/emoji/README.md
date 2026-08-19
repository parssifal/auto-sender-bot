# Vendored: emoji-picker-element

Self-hosted (CSP is `connect-src 'self'` / `script-src 'self'` — a CDN would be
blocked). Used by the composer's premium "any emoji" seed-reaction picker.

- `emoji-picker-element@1.29.1` → `index.js`, `picker.js`, `database.js`
- `emoji-picker-element-data@1.8.0` → `data.json` (its `en/emojibase/data.json`)
- Apache-2.0, see `LICENSE`.

Update:
```
npm pack emoji-picker-element@1 emoji-picker-element-data@1
# unpack, copy index.js picker.js database.js + en/emojibase/data.json here,
# strip the //# sourceMappingURL lines from the three .js files.
```
Served read-only via `add_static("/app/vendor/", ...)`; the picker's data source
is pinned to `/app/vendor/emoji/data.json` (see queue.html), never the CDN.
