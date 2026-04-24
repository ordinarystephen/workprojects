# KRONOS — Adding a new analysis mode

How to wire a new mode end-to-end. Two flavours covered: parameterless
(e.g. `firm-level`) and parameterized (e.g.
`industry-portfolio-level`). For the catalogue of cube data and
verifiable-value labels you can cite, see
[available-kris.md](available-kris.md).

---

## 1. Mode lifecycle

A request flows through these stages:

```
upload  →  classification  →  cube compute  →  mode lookup
        →  parameter validation  →  slicer  →  prompt render
        →  LLM narrate  →  claim verification  →  response
```

Where each piece lives:

| Stage | Code |
|---|---|
| Mode lookup | `pipeline/registry.py::get_mode(slug)` against `config/modes.yaml` |
| Parameter validation | `pipeline/registry.py::validate_parameters(mode, params, cube)` |
| Slicer dispatch | `pipeline/registry.py::get_slicer(name)` (registered via `@register_slicer`) |
| Prompt rendering | `pipeline/registry.py::load_prompt(mode, parameters)` reads `config/prompts/<file>.md` and substitutes `{{name}}` placeholders |
| LLM narrate | `pipeline/agent.py::ask_agent(...)` |
| Verification | `pipeline/validate.py::verify_claims(claims, verifiable_values)` |

Adding a mode means writing **a slicer**, **a prompt**, and **a YAML
entry**. The framework handles routing, validation, prompt rendering,
and verification.

---

## 2. Adding a parameterless mode

Worked example: a fictional `risk-segments` mode that lists the
firm-level top-5 industries by criticized-and-classified exposure.

### 2a. Write the slicer

Create `pipeline/processors/lending/risk_segments.py`. The slicer is a
function decorated with `@register_slicer("name")` that takes a
`LendingCube` (and any required keyword params) and returns a dict
with three keys: `context`, `metrics`, `verifiable_values`.

```python
# pipeline/processors/lending/risk_segments.py

from __future__ import annotations

from pipeline.cube.models import LendingCube
from pipeline.registry import register_slicer


@register_slicer("risk_segments")
def slice_risk_segments(cube: LendingCube) -> dict:
    rows = sorted(
        (
            (name, hist.current.totals.criticized_classified, hist.current.totals.committed)
            for name, hist in cube.by_industry.items()
        ),
        key=lambda r: (-r[1], r[0]),  # primary by C&C desc; tiebreaker on name asc
    )[:5]

    lines = ["Top industries by Criticized & Classified exposure:"]
    for name, cc, committed in rows:
        share = (cc / committed * 100) if committed > 0 else 0.0
        lines.append(f"- {name}: ${cc:,.2f} ({share:.2f}% of industry commitment)")

    metrics = {
        "Top 5 by C&C": [
            {"label": name, "value": f"${cc / 1_000_000:,.1f}M", "sentiment": "warning"}
            for name, cc, _ in rows
        ],
    }

    verifiable_values: dict = {}
    for name, cc, committed in rows:
        verifiable_values[f"{name} (C&C)"] = {"value": cc, "type": "currency"}
        if committed > 0:
            verifiable_values[f"{name} (C&C as % of industry commitment)"] = {
                "value": cc / committed, "type": "percentage",
            }

    return {
        "context": "\n".join(lines),
        "metrics": metrics,
        "verifiable_values": verifiable_values,
    }
```

Three things to notice:

- **Explicit tiebreaker on name asc** in the sort. Determinism matters
  — every cube re-run must produce the same order so follow-up turns
  resolve the same labels.
- **Disambiguating suffix** `(C&C)` on every label. Without it, an
  industry name (e.g. `Energy`) could collide with another slicer's
  bare-name label for the same industry. See
  [available-kris.md → Label-collision watch](available-kris.md#label-collision-watch).
- **Skip percentage publication when the denominator is zero.**
  Don't publish `0%` as a verifiable value when the slicer hit a
  divide-by-zero — it'll mismatch any reasonable LLM citation.

### 2b. Write the prompt

Create `config/prompts/risk_segments.md`. Conventions:

- Open with one sentence: who you are + what data you've been given.
- One bullet per output requirement, terse.
- Close with the **claim convention**: `source_field` matches a label
  from the data verbatim; computed values get
  `source_field: "calculated"`.
- Cite a few sample labels by name so the LLM has examples to copy.

```markdown
You are a credit portfolio analyst summarising the highest-C&C
industries in the firm's lending book. The data lists the top five
industries by Criticized & Classified exposure with each industry's
C&C dollar figure and its share of that industry's commitment.

Produce a short narrative (2-3 paragraphs) that:
- Names the top one or two industries by C&C dollars
- Notes which of them carry an elevated C&C share (>5% of industry
  commitment is meaningful; >10% is a flag)
- Cites every figure verbatim from the data
- Uses professional, matter-of-fact risk language — no preamble

Claims:
- Emit a Claim for every figure you cite.
- source_field must match a label from the data verbatim
  (e.g. "Energy (C&C)", "Energy (C&C as % of industry commitment)").
- For values you compute (e.g. summing two industries), set
  source_field to "calculated".
```

### 2c. Add the registry entry

Append to `config/modes.yaml`:

```yaml
  - slug: risk-segments
    display_name: Risk Segments
    description: Highest-C&C industries by share & dollars.
    user_prompt: >
      Identify the highest-risk industry segments in the portfolio
      by criticized & classified exposure.
    parameters: []
    cube_slice: risk_segments
    prompt_template: risk_segments.md
    status: active
```

Restart the app. `pipeline/registry.py::load_registry()` runs at
import and will (a) parse the YAML against
[`ModeDefinition`](../pipeline/registry.py), (b) confirm
`risk_segments` is a registered slicer, (c) confirm
`config/prompts/risk_segments.md` exists. Any of those failing is a
startup error — you'll catch the problem before a request hits.

A new button appears in the Quick Analysis grid automatically — the
frontend fetches the mode list from `GET /modes`, no JS edit needed.

---

## 3. Adding a parameterized mode

Worked example: walk through how `industry-portfolio-level` is wired.
The pattern generalises to any mode that takes one or more cube-derived
choices (an industry name, a horizontal portfolio, a date range, …).

### 3a. Declare the parameter on the slicer

The decorator's `required_params` list documents which kwargs the
slicer expects. The registry loader cross-checks this against the YAML
and raises `RegistryError` at startup if they disagree.

```python
# pipeline/processors/lending/industry_portfolio_level.py

from pipeline.registry import ParameterError, register_slicer
from pipeline.processors.lending._slice_view import render_slice


@register_slicer("industry_portfolio_level", required_params=["portfolio"])
def slice_industry_portfolio_level(cube, portfolio: str) -> dict:
    slice_ = cube.industry_details.get(portfolio)
    if slice_ is None:
        raise ParameterError(
            f"Industry portfolio {portfolio!r} not present. "
            f"Available: {sorted(cube.industry_details.keys())}"
        )
    return render_slice(
        slice_=slice_,
        kind="Industry Portfolio",
        firm_committed=cube.firm_level.current.totals.committed,
        as_of=cube.metadata.as_of.isoformat(),
    )
```

Defence-in-depth: even though
`pipeline/registry.py::validate_parameters` is called with the cube
before the slicer runs and rejects unknown industries, the slicer
checks again so a bug elsewhere can't turn into a `KeyError`.

### 3b. Declare the parameter on the YAML

```yaml
  - slug: industry-portfolio-level
    display_name: Industry Portfolio Analysis
    description: Deep-dive on one industry portfolio.
    user_prompt: >
      Provide a detailed analysis of the {{portfolio}} industry portfolio.
    parameters:
      - name: portfolio
        type: enum
        source: cube.available_industries
        required: true
        display_label: Industry Portfolio
    cube_slice: industry_portfolio_level
    prompt_template: industry_portfolio_level.md
    status: active
```

The contract:

- `name: portfolio` matches the slicer's `required_params=["portfolio"]`.
- `type: enum` + `source: cube.available_industries` tells
  `GET /cube/parameter-options?mode=industry-portfolio-level` to
  return `cube.available_industries` (sorted) as the dropdown options.
- Other supported types: `string`, `integer`, `number` (no
  enumeration). For static enums use `values: [...]` instead of
  `source:`.
- `cube.<field>` is the only supported source format. The walker in
  `_resolve_cube_field` traverses dotted paths (`cube.available_industries`,
  `cube.industry_details`); `dict.keys()` are returned for dict-typed
  fields.

### 3c. Reference the parameter in the prompt

`{{name}}` placeholders are substituted by `load_prompt(mode, parameters)`:

```markdown
You are a credit portfolio analyst narrating a deep-dive on the
{{portfolio}} industry portfolio. ...

Claims:
- source_field must match a label verbatim. Industry-scoped labels
  are prefixed "Industry Portfolio: {{portfolio}} —" to disambiguate
  them from firm-level figures
  (e.g. "Industry Portfolio: {{portfolio}} — Committed Exposure").
```

The substitution is plain string `replace` — no Jinja, no expressions.
A missing parameter leaves `{{name}}` in the rendered prompt (a
documented behaviour, locked in by `test_registry.py`).

### 3d. UI flow

The frontend already handles parameterized modes generically:

1. User uploads a file.
2. Cube is computed once on the first request.
3. Clicking a parameterized button triggers `GET
   /cube/parameter-options?mode=<slug>` to populate dropdowns from
   `cube.available_<source>`.
4. `POST /upload` carries `parameters: { portfolio: "Energy" }` in the
   JSON body.

No frontend code changes are required to add a new parameterized
mode — the dropdown is rendered from the YAML's parameter spec.

---

## 4. Conventions and gotchas

### Verifiable-value labels

- Every label in `verifiable_values` should also appear verbatim in
  `context` so the LLM has it in front of itself when writing a
  citation. Don't publish a label the LLM hasn't seen.
- Label matching in [pipeline/validate.py](../pipeline/validate.py)
  normalises whitespace and lower-cases — so `"Committed Exposure"`
  and `" committed  exposure"` resolve to the same key. Don't rely on
  this; pick one canonical form per label and stick to it.
- Per-slice labels MUST be prefixed (`Industry Portfolio: <name>` /
  `Horizontal Portfolio: <name>`). Without the prefix, the per-slice
  Committed Exposure would collide with the firm-level Committed
  Exposure. See
  [_slice_view.render_slice](../pipeline/processors/lending/_slice_view.py).
- When two values would naturally have the same label within the same
  slicer (e.g. a facility's committed exposure and its WAPD
  numerator), suffix one with a parenthetical
  (`<facility> (committed)` / `<facility> (WAPD numerator)`).

Before publishing a new label, scan
[available-kris.md](available-kris.md) for collisions.


### Label-form conventions across slicers
Firm-wide slicers (firm_level) publish totals using bare labels: Committed Exposure, Outstanding Exposure, Criticized & Classified (SM + SS + Dbt + L). The context is entirely firm-wide, so "Total" is implicit. Cross-scope items in the same slicer (per-industry, per-horizontal, per-parent, per-WAPD-driver, per-MoM-event) carry a family prefix (e.g. `Industry: Energy — Committed`, `Top Parent: Acme — Committed`, `MoM: New originations count`).

Per-slice slicers (industry_portfolio_level, horizontal_portfolio_level) use full prefix disambiguation (Industry Portfolio: <name> — Committed Exposure).

If a future slicer juxtaposes firm-wide totals against slice-level figures in the same context, use explicit qualifiers ("Total ...", "Firm ...", etc.) on the firm-wide labels to disambiguate them from the per-slice figures.

When adding a new slicer, pick the convention that matches the scope the slicer operates at:
- If the entire context is one scope (firm-wide, or one slice), use bare labels or the slice prefix respectively.
- If the slicer juxtaposes multiple scopes (firm + slices, or slice + sub-slices), use explicit qualifiers ("Total ...", "Firm ...", etc.) to disambiguate.


### Prompt instructions: cite verbatim

Every prompt should explicitly tell the LLM:

- Cite figures **verbatim** from the data — don't round, restate, or
  introduce numbers absent from the data.
- `source_field` must **match a label from the data verbatim**.
- Computed values (sums, ratios, deltas not pre-computed) get
  `source_field: "calculated"`.

Without those three lines, the LLM tends to paraphrase numbers and
the verifier flags everything as `mismatch`.

### Placeholder modes

Every mode must have `status: active | placeholder | disabled`.

- `active` — fully wired (slicer + prompt + YAML). Loader cross-checks
  all three.
- `placeholder` — mode appears in the UI grid but `/upload` returns
  `mode_not_implemented`. Use this for parameter wiring without a
  slicer (`portfolio-comparison`, `industry-within-horizontal`) or
  for simple "coming soon" buttons (`concentration-risk`,
  `delinquency-trends`, `risk-segments`, `exec-briefing`,
  `stress-outlook`). Placeholders skip the slicer/prompt cross-check
  at startup.
- `disabled` — accepted by the schema but currently unused. Modes
  marked disabled don't render in the UI.

### Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Mode slug | kebab-case | `industry-portfolio-level` |
| Slicer name | snake_case, matches function intent | `industry_portfolio_level` |
| Slicer file | snake_case mode name + `.py` | `industry_portfolio_level.py` |
| Prompt file | snake_case mode name + `.md` | `industry_portfolio_level.md` |
| Verifiable label (per-slice) | `<Kind>: <name> — <local label>` | `Industry Portfolio: Energy — Committed Exposure` |

The slicer name is what `cube_slice:` references in YAML, not the slug.

### Determinism

If you sort, always include an explicit tiebreaker. Pandas / Python
default ordering can shift across versions, and follow-up turns
require byte-identical re-runs. Patterns to follow:

- `sorted(items, key=lambda x: (-x.metric, x.id))` — primary on the
  metric desc, tiebreaker on a stable id asc.
- After a `groupby(...).join(...)`, do
  `joined.reset_index().sort_values("Facility ID").set_index("Facility ID")`
  before iterating (see Round 15 fix in
  [pipeline/cube/lending.py](../pipeline/cube/lending.py)).


### Active vs placeholder: what to ship first
 
When starting a new mode, default to shipping the registry entry before the slicer is real. Add the mode as `status: placeholder` — it will appear in the UI but return `mode_not_implemented` cleanly when a user clicks it. Flip to `active` in a later commit once the slicer and prompt are in place.
 
This pattern is useful when:
 
- You're wiring the parameter surface (YAML parameter definitions, frontend dropdown source) before the slicer exists and want to exercise the plumbing early.
- A mode is architecturally meaningful but depends on cube work that hasn't landed yet (e.g. `industry-within-horizontal` depends on facility-level cube exposure).
- You want to reserve a slug so the UI shows "coming soon" buttons, making the roadmap visible to users without committing to implementation timing.
What to avoid:
 
- Don't add a mode as `active` with a half-written slicer "to be finished later." Startup validation will either fail loudly (if `cube_slice` references an unregistered slicer) or worse, let the mode silently misbehave in production.
- Don't leave modes as `placeholder` indefinitely after their implementation lands. Flip to `active` as soon as the slicer and prompt are committed — stale placeholders confuse users who click and get `mode_not_implemented` on something that looks implemented.
The `status` field is cheap to change. Use it as a deliberate signal about where each mode is in its lifecycle.
 
---

## 5. Testing

### Slicer unit test

Build a minimal `LendingCube` in-memory (or read a small fixture
workbook through `pipeline/loaders/classifier.py +
pipeline/cube/lending.py`) and assert on the slicer's three return
keys. Pattern from existing tests:

```python
# pipeline/tests/test_risk_segments.py

import unittest
from pipeline.processors.lending.risk_segments import slice_risk_segments
# ... build a minimal cube fixture ...

class TestRiskSegments(unittest.TestCase):
    def test_publishes_expected_labels(self):
        result = slice_risk_segments(cube)
        self.assertIn("Energy (C&C)", result["verifiable_values"])
        self.assertEqual(
            result["verifiable_values"]["Energy (C&C)"]["type"], "currency"
        )

    def test_context_mentions_top_industry(self):
        result = slice_risk_segments(cube)
        self.assertIn("Energy", result["context"])
```

### Registry / wiring tests

Run [pipeline/tests/test_registry.py](../pipeline/tests/test_registry.py)
after any YAML change:

```bash
python -m pytest pipeline/tests/test_registry.py
```

It checks: YAML parses, every active mode points at a registered
slicer, every active mode's prompt file exists, parameter required-set
matches the slicer's `required_params`. **A mode that fails registry
validation breaks app import**, not just tests.

### Local prompt smoke test

To check the prompt renders correctly without going through Flask:

```python
from pipeline.registry import get_mode, load_prompt
text = load_prompt(get_mode("industry-portfolio-level"),
                   {"portfolio": "Energy"})
print(text)
```

Look for any unsubstituted `{{...}}` tokens — they indicate a missing
parameter or a typo.

### End-to-end smoke

Use the demo file (or any classified workbook) against a local Flask:

```bash
python server.py
# In another terminal, hit /upload via the UI.
```

For `USE_MOCK_RESULTS = false` (the default), real LLM responses come
back. For UI-only iteration without an Azure round-trip, flip
`USE_MOCK_RESULTS = true` in `static/main.js`.

---

## 6. Pre-PR checklist

Before opening a PR for a new mode:

- [ ] Slicer registered with `@register_slicer("name")`.
- [ ] Slicer returns `{"context", "metrics", "verifiable_values"}`.
- [ ] Every figure in `context` has a corresponding key in
  `verifiable_values` (or is genuinely computed).
- [ ] Every `verifiable_values` key is unique within the slicer and
  prefix-disambiguated against other slicers (cross-checked against
  [available-kris.md](available-kris.md)).
- [ ] Sorts include explicit tiebreakers — no reliance on default
  ordering.
- [ ] Prompt at `config/prompts/<file>.md` instructs the LLM to cite
  verbatim and matches `source_field` to labels.
- [ ] YAML entry parameter `name`s match slicer `required_params`.
- [ ] `python -m pytest pipeline/tests/` is green.
- [ ] Manual smoke test against a real workbook passes — narrative
  renders, claims tab populates, verification badge isn't all amber.
- [ ] If the mode is parameterized, `GET
  /cube/parameter-options?mode=<slug>` returns the expected option
  list.
- [ ] CLAUDE.md "Currently wired modes" table updated if the new mode
  is `active`.
- [ ] [available-kris.md](available-kris.md) updated with any new
  labels exposed for verification.
