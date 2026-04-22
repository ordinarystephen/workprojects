# KRONOS — Available KRIs & verifiable-value labels

This is the reference for prompt authors. It catalogues every KRI the
deterministic cube produces and every label slicers publish into
`verifiable_values`. The verifier ([pipeline/validate.py](../pipeline/validate.py))
matches `claim.source_field` against these labels exactly (whitespace
and case normalised, but otherwise verbatim) — so the labels listed
here are the only strings the LLM can cite and have verified.

For mode-wiring mechanics see [adding-a-mode.md](adding-a-mode.md).

---

## How to read this doc

Each section corresponds to a cube field on
[`LendingCube`](../pipeline/cube/models.py). For each KRI:

- **Label** — exact string published into `verifiable_values` (or noted
  as **cube-only** when the slicer doesn't expose it for verification).
- **Type** — drives the verifier's tolerance: `currency` (matches at
  the LLM's quoted precision), `percentage` (±0.05 pp), `count` (exact
  integer), `date` (normalised ISO), `string` (case-insensitive),
  `weighted-average` (rendered as a string by the slicer).
- **Meaning** — one-liner.
- **Published by** — which active slicers expose it. The four active
  slicers are `firm_level`, `portfolio_summary`,
  `industry_portfolio_level`, `horizontal_portfolio_level`.

Per-slice labels (industry / horizontal) carry a prefix
(`Industry Portfolio: <name>` or `Horizontal Portfolio: <name>`)
followed by ` — ` and the local label. The prefix is load-bearing — it
prevents collisions with firm-level and other-slice labels. Where a
table row shows `<prefix> — Committed Exposure`, the actual published
key is e.g. `Industry Portfolio: Energy — Committed Exposure`.

---

## Rating-category structure (Part 1 reminder)

Every slicer that touches rating composition uses the same five-bucket
shape, refactored in Round 13 to avoid silently bucketing unrated PD
codes as NIG:

| Bucket | PD codes | Treatment in narratives |
|---|---|---|
| **Investment Grade** | C00 – C07 | Top-level peer to NIG. Share denominator = "rated commitment" (IG + NIG only). |
| **Non-Investment Grade** | C08 – C13 | Top-level peer to IG. Share denominator = rated commitment. **Does not absorb CDF or unrated codes.** |
| **of which Distressed** | C13 (subset of NIG) | A sub-line under NIG, never a peer bucket. Share denominator = NIG. |
| **Defaulted** | CDF | Separate top-level peer, **not part of NIG**. Share denominator = total commitment. Terminal-state concern. |
| **Non-Rated** | TBR / NTR / Unrated / NA / N/A / #REF / blank | Separate top-level peer. Share denominator = total commitment. **Data-quality signal, not a credit assessment** — narrate as such. |

Prompt authors should preserve those framings explicitly: Distressed is
"of which", Defaulted is a terminal-state peer, Non-Rated is a
data-quality signal.

---

## firm_level

Source: [`LendingCube.firm_level`](../pipeline/cube/models.py),
slicer: [`firm_level.py`](../pipeline/processors/lending/firm_level.py).
Published by: `firm_level` (and most labels overlap with
`portfolio_summary` — see notes below).

### Counts

| Label | Type | Meaning |
|---|---|---|
| `Distinct ultimate parents` | count | Unique top-of-hierarchy parents in the latest period. |
| `Distinct partners` | count | Unique partners. **Firm-level only — `portfolio_summary` omits.** |
| `Distinct facilities` | count | Unique facilities. |
| `Distinct risk assessment industries` | count | Industry buckets. **Firm-level form.** `portfolio_summary` publishes the same value under the shorter label `Distinct industries`. |

### Exposure totals (currency)

| Label | Meaning |
|---|---|
| `Committed Exposure` | Total committed across the firm. **`portfolio_summary` uses `Total Committed Exposure`** — distinct label, same number. |
| `Outstanding Exposure` | Total outstanding. **`portfolio_summary` uses `Total Outstanding Exposure`.** |
| `Take & Hold Exposure` | Firm-level only. |
| `Temporary Exposure` | Firm-level only. |
| `Approved Limit` | Firm-level only. |

### Regulatory-rating breakdown (currency)

`Pass`, `Special Mention`, `Substandard`, `Doubtful`, `Loss`,
`No Regulatory Rating`. Firm-level only — `portfolio_summary` doesn't
republish these line items.

| Label | Type | Meaning |
|---|---|---|
| `Criticized & Classified (SM + SS + Dbt + L)` | currency | Firm-level form. **`portfolio_summary` uses `Criticized & Classified exposure (SM + SS + Dbt + L)`** — distinct label, same number. |
| `C&C as % of commitment` | percentage | Available on both `firm_level` and `portfolio_summary`. |

### Weighted averages

| Label | Type | Notes |
|---|---|---|
| `Weighted Average PD` | string (rating display, e.g. `C06`) | Slicer publishes the rendered display string, not the raw decimal. |
| `Weighted Average LGD` | string (e.g. `42.00%`) | Same — verifier compares the rendered string. |

### Period metadata

| Label | Type |
|---|---|
| `as of` | date (ISO) |

---

## by_ig_status

Source: [`LendingCube.by_ig_status`](../pipeline/cube/models.py)
(keys: `Investment Grade`, `Non-Investment Grade`).

| Label | Type | Published by |
|---|---|---|
| `Investment Grade` | currency | `firm_level`, `portfolio_summary` |
| `Non-Investment Grade` | currency | `firm_level`, `portfolio_summary` |
| `Investment Grade (% of rated commitment)` | percentage | `portfolio_summary` only |
| `Non-Investment Grade (% of rated commitment)` | percentage | `portfolio_summary` only |

The `% of rated commitment` denominator is `IG + NIG` — Defaulted and
Non-Rated are excluded from the denominator. Legacy semantic, preserved
across the Round 13 refactor.

---

## by_defaulted

Source: [`LendingCube.by_defaulted`](../pipeline/cube/models.py)
(single key `Defaulted`, populated only when any facility has PD = CDF).

| Label | Type | Published by |
|---|---|---|
| `Defaulted` | currency | `firm_level`, `portfolio_summary` |
| `Defaulted (% of total commitment)` | percentage | `portfolio_summary` only |

---

## by_non_rated

Source: [`LendingCube.by_non_rated`](../pipeline/cube/models.py)
(single key `Non-Rated`, populated only when any facility has a
placeholder PD code — `NON_RATED_TOKENS`).

| Label | Type | Published by |
|---|---|---|
| `Non-Rated` | currency | `firm_level`, `portfolio_summary` |
| `Non-Rated (% of total commitment)` | percentage | `portfolio_summary` only |

---

## nig_distressed_substats

Source:
[`LendingCube.nig_distressed_substats`](../pipeline/cube/models.py)
— latest-period C13 subset of NIG, populated only when NIG has any
C13 rows.

| Label | Type | Published by |
|---|---|---|
| `Distressed (of which)` | currency | `firm_level`, `portfolio_summary` |
| `Distressed facility count` | count | `firm_level`, `portfolio_summary` |
| `Distressed (% of NIG)` | percentage | `portfolio_summary` only |

Always cite this as "of which" relative to NIG — never as a peer bucket.

---

## by_horizontal

Source:
[`LendingCube.by_horizontal`](../pipeline/cube/models.py) (keyed by
horizontal portfolio name, e.g. `Leveraged Finance`,
`Global Recovery Management`).

| Label | Type | Published by |
|---|---|---|
| `<horizontal name>` | currency | `firm_level` (the committed figure for each horizontal is published under its own name) |

Note: at firm-level, the horizontal label is **unprefixed** (e.g.
`Leveraged Finance`). When the same horizontal is the subject of a
`horizontal_portfolio_level` slice, all its labels are prefixed
`Horizontal Portfolio: Leveraged Finance — …` — see the per-slice
section below.

---

## by_industry

Source: [`LendingCube.by_industry`](../pipeline/cube/models.py)
(keyed by industry name).

| Label | Type | Published by |
|---|---|---|
| `<industry name>` | currency | `portfolio_summary` (top-5 only) |
| `<industry name> (% of total commitment)` | percentage | `portfolio_summary` (top-5 only) |

Industries outside the top 5 are in the cube but **unverifiable** at
firm-summary scope. To narrate a non-top-5 industry, run the
`industry-portfolio-level` mode against it.

---

## by_segment / by_branch

Source: [`LendingCube.by_segment`](../pipeline/cube/models.py),
[`LendingCube.by_branch`](../pipeline/cube/models.py).

**Cube-only — no active slicer publishes these as `verifiable_values`
yet.** They exist for future modes (e.g. concentration risk by branch).
Don't cite a segment or branch label until a slicer is wired.

---

## top_contributors (parent-level)

Source: [`LendingCube.top_contributors`](../pipeline/cube/models.py)
— top-10 parents by committed / outstanding / WAPD numerator /
C&C exposure.

| Label | Type | Published by |
|---|---|---|
| `<parent name>` | currency (committed) | `portfolio_summary` (top-5 only, by committed) |
| `<parent name> (% of total commitment)` | percentage | `portfolio_summary` (top-5 only) |

Other contributor sort orders (outstanding / WAPD / CC) are
cube-only — not yet published.

---

## top_wapd_facility_contributors (facility-level)

Source:
[`LendingCube.top_wapd_facility_contributors`](../pipeline/cube/models.py)
— top-10 facilities firm-wide by WAPD numerator (PD × Committed).

For each facility, four parallel labels are published. `<facility>` is
the facility name (or facility id when the name is missing).

| Label | Type | Published by |
|---|---|---|
| `<facility> (committed)` | currency | `portfolio_summary` |
| `<facility> (WAPD numerator)` | currency | `portfolio_summary` |
| `<facility> (share of firm WAPD numerator)` | percentage | `portfolio_summary` |
| `<facility> (implied PD)` | percentage | `portfolio_summary` |

`implied PD` = numerator ÷ committed for the facility (decimal).

---

## wapd_contributors_by_horizontal

Source:
[`LendingCube.wapd_contributors_by_horizontal`](../pipeline/cube/models.py)
— same shape as the firm-level list, computed per horizontal portfolio.

**Cube-only — not surfaced by any current slicer.** Same data is
exposed via `PortfolioSlice.top_wapd_facilities` on the per-slice
container; the standalone dict is scheduled for Path B removal
(see [future-work.md §4](future-work.md)).

---

## watchlist

Source: [`LendingCube.watchlist`](../pipeline/cube/models.py) —
firm-level Credit Watch List Flag = "Y" aggregate.

| Label | Type | Published by |
|---|---|---|
| `Watchlist facility count` | count | `firm_level`, `portfolio_summary` |
| `Watchlist committed exposure` | currency | `firm_level`, `portfolio_summary` |

Per-slice watchlist labels are prefixed — see per-slice section.

---

## month_over_month

Source: [`LendingCube.month_over_month`](../pipeline/cube/models.py)
— populated only when ≥ 2 periods are uploaded.

Counts only — the underlying `RatingChange` / `FacilityChange` /
`ExposureMover` lists are cube-only and not individually citable.

| Label | Type | Published by |
|---|---|---|
| `New originations` | count | `portfolio_summary` |
| `Exits` | count | `portfolio_summary` |
| `New parent relationships` | count | `portfolio_summary` |
| `Parent relationships exited` | count | `portfolio_summary` |
| `PD rating downgrades` | count | `portfolio_summary` |
| `PD rating upgrades` | count | `portfolio_summary` |
| `Regulatory rating downgrades` | count | `portfolio_summary` |
| `Regulatory rating upgrades` | count | `portfolio_summary` |

---

## industry_details / horizontal_details (per-slice)

Source: [`LendingCube.industry_details`](../pipeline/cube/models.py)
and `horizontal_details` — `dict[str, PortfolioSlice]`. Rendered by
the shared
[`_slice_view.render_slice`](../pipeline/processors/lending/_slice_view.py).

Every label in this section is **prefix-disambiguated**:

- Industry slicer prefix: `Industry Portfolio: <industry name>`
- Horizontal slicer prefix: `Horizontal Portfolio: <horizontal name>`

The prefix is followed by ` — ` and the local label. Replace
`<prefix>` in the table below with the full prefix string. Published
by `industry_portfolio_level` and `horizontal_portfolio_level`.

### Slice scale

| Label | Type |
|---|---|
| `<prefix> — Committed Exposure` | currency |
| `<prefix> — Outstanding Exposure` | currency |
| `<prefix> — Distinct ultimate parents` | count |
| `<prefix> — Distinct facilities` | count |
| `<prefix> — Share of firm committed exposure` | percentage |
| `<prefix> — As of` | date |

### Slice credit quality

| Label | Type |
|---|---|
| `<prefix> — Criticized & Classified exposure (SM + SS + Dbt + L)` | currency |
| `<prefix> — C&C as % of commitment` | percentage |
| `<prefix> — Weighted Average PD` | string |
| `<prefix> — Weighted Average LGD` | string |

### Slice rating composition

| Label | Type |
|---|---|
| `<prefix> — Investment Grade` | currency |
| `<prefix> — Investment Grade (% of rated commitment)` | percentage |
| `<prefix> — Non-Investment Grade` | currency |
| `<prefix> — Non-Investment Grade (% of rated commitment)` | percentage |
| `<prefix> — Defaulted` | currency |
| `<prefix> — Defaulted (% of slice commitment)` | percentage |
| `<prefix> — Non-Rated` | currency |
| `<prefix> — Non-Rated (% of slice commitment)` | percentage |
| `<prefix> — Distressed (of which)` | currency |
| `<prefix> — Distressed facility count` | count |
| `<prefix> — Distressed (% of NIG)` | percentage |

Note that within a slice, IG/NIG share is `% of rated commitment`
(IG + NIG within the slice), Defaulted/Non-Rated share is `% of slice
commitment`, and Distressed share is `% of NIG` — same conventions as
firm-level.

### Slice contributors

For each top-5 parent within the slice (`<parent>` is parent name or
parent id):

| Label | Type |
|---|---|
| `<prefix> — <parent>` | currency (committed) |
| `<prefix> — <parent> (% of slice commitment)` | percentage |

### Slice facility-level WAPD drivers

For each top-5 facility within the slice (`<facility>` is facility
name or id):

| Label | Type |
|---|---|
| `<prefix> — <facility> (committed)` | currency |
| `<prefix> — <facility> (WAPD numerator)` | currency |
| `<prefix> — <facility> (share of slice WAPD numerator)` | percentage |
| `<prefix> — <facility> (implied PD)` | percentage |

### Slice watchlist

| Label | Type |
|---|---|
| `<prefix> — Watchlist facility count` | count |
| `<prefix> — Watchlist committed exposure` | currency |

---

## The "calculated" escape hatch

When the LLM cites a number that doesn't correspond to a published
label, the prompt convention is to set `source_field: "calculated"`.
The verifier marks the claim `unverified` (reason `field_not_found`)
without flagging it as a mismatch. Use it for:

- Sums of multiple line items (e.g. "$X across IG + Defaulted").
- Ratios derived inline that no slicer pre-computes (e.g. "the top
  three parents account for N%").
- Period-over-period deltas that aren't pre-computed in the cube.

**Don't use `calculated` as a default.** Almost every figure in the
narrative should map to a real label — `calculated` is only for
genuinely derived quantities. Two failure modes to avoid:

1. **Cite-then-recompute.** The LLM cites `Committed Exposure` then
   restates it slightly differently downstream and tags the second
   mention `calculated`. Prefer one label, one citation.
2. **Hide-the-mismatch.** The LLM gets the figure wrong and tags it
   `calculated` rather than citing the label and getting flagged. The
   prompt should make this explicit: cite the label whenever a label
   exists.

Prompts in
[config/prompts/](../config/prompts/) include the line "For values
you compute (sums, ratios), set source_field to 'calculated'." Keep
that constraint tight — don't widen it.

---

## Label-collision watch

Two slicers publishing the same label for different numbers will
silently make claims resolve to the wrong value. Prefix discipline
prevents this:

- Per-slice labels are always `<kind>: <name> — <local label>`.
- Firm-wide labels never carry that prefix.
- Slicer authors adding new labels should grep `verifiable_values` in
  this doc to confirm the local label is unique within the slicer's
  scope.

When in doubt, add a parenthetical disambiguator (see
`portfolio_summary`'s `<facility> (committed)` /
`<facility> (WAPD numerator)` pair).

---

## Quick map: cube field → publishing slicers

| Cube field | `firm_level` | `portfolio_summary` | `industry_portfolio_level` | `horizontal_portfolio_level` |
|---|:-:|:-:|:-:|:-:|
| `firm_level` totals/counts | ✅ | ✅ | — (uses for share-of-firm only) | — (same) |
| `by_ig_status` | ✅ | ✅ | via `industry_details` | via `horizontal_details` |
| `by_defaulted` | ✅ | ✅ | via `industry_details` | via `horizontal_details` |
| `by_non_rated` | ✅ | ✅ | via `industry_details` | via `horizontal_details` |
| `nig_distressed_substats` | ✅ | ✅ (+ `% of NIG`) | via `industry_details` | via `horizontal_details` |
| `by_industry` | — | top-5 only | — | — |
| `by_horizontal` | ✅ (committed per name) | — | — | — |
| `by_segment` / `by_branch` | — | — | — | — |
| `top_contributors` | — | top-5 by committed | — | — |
| `top_wapd_facility_contributors` | — | full list | — | — |
| `wapd_contributors_by_horizontal` | — | — | — | — (use `top_wapd_facilities` on the slice) |
| `watchlist` | ✅ | ✅ | scoped (slice watchlist) | scoped (slice watchlist) |
| `month_over_month` | — | counts only | — | — |
| `industry_details` | — | — | ✅ (whole slice) | — |
| `horizontal_details` | — | — | — | ✅ (whole slice) |

Empty cells are either (a) future-mode territory (`by_segment`) or (b)
deliberately scoped out of the slicer (e.g. `firm_level` doesn't list
top contributors — that's `portfolio_summary`'s job).
