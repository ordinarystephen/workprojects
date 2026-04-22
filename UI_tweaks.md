# KRONOS — UI Tweaks Cheatsheet

Where to go to fine-tune the most common visual things. All paths are relative to repo root.

Three files do all the work:
- [static/styles.css](static/styles.css) — design tokens, layout, colors, font sizes
- [static/main.js](static/main.js) — what gets rendered and in what order
- [static/index.html](static/index.html) — the markup skeleton

---

## 1. Global type & color tokens (one place to change everything)

[static/styles.css:5-46](static/styles.css#L5-L46) — the `:root` block. Edit a token here and it propagates everywhere.

| Token | Default | Controls |
|---|---|---|
| `--font-sans` | DM Sans | All UI text |
| `--font-mono` | DM Mono | Values, timestamps, file pills |
| `html { font-size: 15px }` | line 47 | Base rem size — bumping this scales the whole UI |
| `--surface` / `--surface-hover` | #FFFFFF / #F7F8FA | Card backgrounds |
| `--accent` | #1C4ED8 | Blueprint blue — focus rings, badges, dots |
| `--cta` | #8B2C35 | Wine red — Run Analysis button only |
| `--radius-sm` / `--radius-lg` | 6px / 14px | Corner rounding |

**Want everything bigger?** Change `html { font-size: 15px }` at [styles.css:47](static/styles.css#L47) to `16px` or `17px`. All `rem`-based sizes scale with it.

---

## 2. Results layout — narrative vs data card ratio

[static/styles.css:829-838](static/styles.css#L829-L838) — `.results-body`

```css
.results-body {
  grid-template-columns: 1.4fr 1fr;   /* narrative wider | data column */
  gap: 20px;
}
```

- **Swap which column is wider** — flip to `1fr 1.4fr` to favor the data column
- **Make data card on top instead of right** — change `1.4fr 1fr` → `1fr` and the columns stack
- **Mobile collapse breakpoint** — [styles.css:836](static/styles.css#L836) `@media (max-width: 860px)` already collapses to a single column under 860px wide

---

## 3. Narrative text font size & spacing

[static/styles.css:876-883](static/styles.css#L876-L883) — `.narrative-text`

```css
.narrative-text {
  padding: 24px;
  font-size: 0.9rem;     /* ← bump to 1rem for larger body text */
  line-height: 1.8;      /* ← reduce to 1.6 for tighter blocks */
  min-height: 200px;
}
```

---

## 4. Metric tile (Data Snapshot card) sizing & layout

[static/styles.css:902-947](static/styles.css#L902-L947)

| What | Where | Change to… |
|---|---|---|
| Tiles per row | `.metric-grid { grid-template-columns: 1fr 1fr }` line 905 | `1fr 1fr 1fr` for 3-up, `1fr` for stacked |
| Tile padding | `.metric-card { padding: 14px }` line 913 | bump for airier tiles |
| Tile label size | `.metric-key { font-size: 0.7rem }` line 923 | controls the small uppercase caption |
| Tile **value** size | `.metric-value { font-size: 1.15rem }` line 932 | the big number; bump to `1.35rem` for emphasis |
| Section header above tiles | `.metric-section-header { font-size: 0.68rem }` line 951 | controls "FIRM-LEVEL OVERVIEW · …" headers |
| Make a tile span full row | add `class="metric-card full-width"` (style at line 947) | useful for wide labels |

**Mobile breakpoint** at [styles.css:1329](static/styles.css#L1329) collapses `.metric-grid` to one column under 480px.

---

## 5. Order of data cards after submission

The order is determined entirely by the **insertion order of keys** in the `metrics` dict returned by your processor. The frontend renders sections top-to-bottom in the order they appear in the JSON.

For Portfolio Summary, that's [pipeline/processors/lending/portfolio_summary.py](pipeline/processors/lending/portfolio_summary.py) — look at the `metrics: dict = { ... }` block. Reorder the dict entries to reorder the rendered sections.

For Firm-Level View, same thing in [pipeline/processors/lending/firm_level.py:125](pipeline/processors/lending/firm_level.py#L125).

**Within a section**, order is the order of the list items:

```python
metrics = {
    "Headline · As of 2026-04-21": [
        {"label": "Total Commitment",  "value": "..."},   # ← first tile
        {"label": "Total Outstanding", "value": "..."},   # ← second
        # ...
    ],
}
```

Reorder the list to reorder the tiles within that section.

**Sentiment colors** on individual tiles come from the `"sentiment"` key on each tile object: `"positive"` (green) / `"negative"` (red) / `"warning"` (amber) / `"neutral"` (default). Defined at [styles.css:938-940](static/styles.css#L938-L940).

---

## 6. Tab bar above the narrative (Analysis / Claims)

Markup in [static/index.html](static/index.html), styles in [static/styles.css](static/styles.css). Search for `.narrative-tabs` and `.narrative-tab` to adjust the toggle look. The Claims count badge is part of the tab label and updated by `main.js`.

---

## 7. Verification badge (green/amber pill)

[static/styles.css](static/styles.css) — search for `.verification-badge`. Two classes drive it: `.verification-badge.all-clear` (green) and `.verification-badge.has-unverified` (amber). The tooltip text comes from [static/main.js](static/main.js) — search for `verification` to find the render block.

---

## 8. FAB (the floating + button) & follow-up box

- FAB position/size: [static/styles.css](static/styles.css) — search for `.fab`
- Follow-up textarea, error row, send button: same file — search for `.followup-`
- Behavior (open, abort, timeout, retry): [static/main.js](static/main.js) — search for `submitFollowup` and `abortFollowup`

---

## 9. Pipeline animation dots & step labels

[static/main.js](static/main.js) — search for `STEP_DELAYS`. Currently `[150, 250, 100, 0, 150]`. These are short visual placeholders; the **displayed time numbers** are overwritten with real server-side timings from `timings_ms` after the API returns.

Step labels (markup) live in [static/index.html](static/index.html) — search for `pipeline-step`.

---

## 10. Dark mode

Activates automatically via `@media (prefers-color-scheme: dark)`. Block lives at the bottom of [static/styles.css](static/styles.css) starting around line 1370+. Tokens are reassigned (`--surface`, `--text`, etc.) — change the dark color values there, not the components themselves.

---

## Quick map: "I want to change X"

| Goal | File · Line(s) |
|---|---|
| Make everything bigger | [styles.css:47](static/styles.css#L47) — `html { font-size }` |
| Change narrative body size | [styles.css:878](static/styles.css#L878) |
| Change tile value (number) size | [styles.css:932](static/styles.css#L932) |
| Reorder data card sections | The processor file (e.g. [portfolio_summary.py](pipeline/processors/lending/portfolio_summary.py)) — reorder the `metrics` dict |
| Reorder tiles within a section | Same processor — reorder the list under that section key |
| Tile color (per-tile) | Set `"sentiment"` on the tile in the processor |
| Change layout ratio (narrative vs data) | [styles.css:831](static/styles.css#L831) — `grid-template-columns` |
| Change accent color (blue) | [styles.css:16](static/styles.css#L16) — `--accent` |
| Change CTA button color | [styles.css:31](static/styles.css#L31) — `--cta` |
| Add or change the canned-prompt buttons | [static/prompts.json](static/prompts.json) |
