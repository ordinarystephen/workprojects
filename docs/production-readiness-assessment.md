# KRONOS — Production-Readiness Assessment

> Honest, evidence-based read of the codebase as of 2026-04-22. Cites file
> paths and line numbers throughout so each finding is verifiable.
>
> Five-point rating scale used per dimension:
> **prototype · rough draft · working · solid · production-grade.**

---

## 1. Architecture

The codebase has a clear layered shape: workbook bytes flow through a
`Template` (column tagging) → a `classifier` (sheet-to-template matching) →
a `cube` (deterministic pydantic-typed aggregations) → a `slicer` (mode-
specific projection of the cube) → an `agent` (LangGraph + Azure OpenAI
narration) → a `validate` step (per-claim verification). Boundaries are
explicit, each layer holds a single responsibility, and the contract
between layers is enforced by pydantic models with `extra="forbid"`
(see [pipeline/cube/models.py](pipeline/cube/models.py)). A YAML mode
registry ([config/modes.yaml](config/modes.yaml)) plus an `@register_slicer`
decorator ([pipeline/registry.py:18-26](pipeline/registry.py#L18-L26))
is the integration glue: adding a mode is a YAML entry + a decorated
function + a markdown prompt. Boot-time validation cross-checks every
declared `cube_slice:` against the slicer registry and every
`prompt_template:` against the filesystem
([server.py:88-97](server.py#L88-L97)) so misconfiguration crashes at
import, not per-request.

Strong examples:
- Single source of truth for horizontal-portfolio membership rules:
  [pipeline/templates/lending.py](pipeline/templates/lending.py)'s
  `horizontal_definitions()` is consumed by both
  `compute_lending_cube` and per-slice composition
  ([pipeline/cube/lending.py:97-108](pipeline/cube/lending.py#L97-L108),
  [pipeline/cube/lending.py:194-205](pipeline/cube/lending.py#L194-L205)).
  No risk of two definitions drifting.
- Two-pass parameter validation
  ([server.py:376-390](server.py#L376-L390) cube-agnostic;
  [pipeline/analyze.py](pipeline/analyze.py) cube-aware) catches
  malformed input before the expensive cube build.
- Same-instance invariant: `cube.industry_details[name].grouping IS
  cube.by_industry[name]`, enforced by passing `grouping` into
  `_build_portfolio_slice` rather than recomputing it
  ([pipeline/cube/lending.py:392-427](pipeline/cube/lending.py#L392-L427)).
  The cube cannot disagree with itself.

Weak examples:
- `template_name = "lending"` is hardcoded in
  [pipeline/analyze.py](pipeline/analyze.py) — adding the planned
  `TradedProductsTemplate` requires editing the dispatcher, not just
  registering a new template.
- [pipeline/cube/lending.py](pipeline/cube/lending.py) is 968 lines.
  The rating-mask, KRI, contributor, and MoM helper families could
  each live in their own module without cost.
- `LangGraphResponsesAgent` and `mlflow.models.set_model(...)` exist
  in [pipeline/agent.py:503-568](pipeline/agent.py#L503-L568) but are
  not exercised at Flask runtime — Flask calls `ask_agent` directly
  ([server.py:475-481](server.py#L475-L481)). The agent class is API
  surface that is documented but untested.

**Rating: solid.** The structural discipline (single source of truth,
extra="forbid" pydantic boundaries, registry-driven mode wiring) is
excellent; the remaining issues are localised (one hardcoded string, one
oversized module, one untested abstraction) and not architectural rot.

---

## 2. Error Handling

`/upload` segments the request into named stages and wraps each in a
try/except that emits a stable event-type slug into the JSONL error log
([server.py:404-535](server.py#L404-L535)). The event types
(`upload_parse_failed`, `parameter_validation_failed`,
`mode_not_implemented`, `classification_failed`, `slicer_failed`,
`llm_failed`, `verification_mismatch`,
`cube_parameter_options_failed`) are stable enough to drive triage
dashboards. Failures bubble up as user-readable JSON error responses
with appropriate HTTP status codes (400 for input, 501 for
not-implemented, 500 for internal). MLflow tracking is wrapped in
defensive try/except so an MLflow outage cannot crash a request
([pipeline/tracking.py](pipeline/tracking.py)). The agent layer has a
structured-output → plain-text fallback in `narrate`
([pipeline/agent.py:328-350](pipeline/agent.py#L328-L350)) so the user
always gets a narrative even when the LLM doesn't honour the
`NarrativeResponse` schema. Boot-time registry failures are wrapped in a
friendly `RuntimeError` banner pointing at the offending file
([server.py:88-97](server.py#L88-L97)).

Strong examples:
- Per-stage timing instrumentation co-located with error capture, so
  every error record carries enough context to triage without a
  reproduction
  ([server.py:404-509](server.py#L404-L509)).
- `verification_mismatch` event captures the offending claims (capped
  at 10) so the dangerous case (LLM cited a known field with the wrong
  value) is loggable separately from the transparency case (unverified
  / field_not_found)
  ([server.py:520-535](server.py#L520-L535)).
- `pipeline/error_log.py` is two-tier (always-on JSONL with
  10MB rotation + threading lock; opt-in MLflow) and never raises
  back to the caller.

Weak examples:
- [server.py:434-449](server.py#L434-L449) catches `ValueError` and
  labels it `classification_failed`. But
  [pipeline/cube/lending.py:84](pipeline/cube/lending.py#L84) raises
  `ValueError` for "no valid period values" — a cube error that would
  surface with the wrong event type.
- `narrate()` catches bare `Exception`
  ([pipeline/agent.py:339-342](pipeline/agent.py#L339-L342)) and
  silently downgrades to plain text. A real Azure auth failure or
  network timeout looks identical to a Pydantic validation failure of
  the structured output. The fallback masks at least three distinct
  failure modes that deserve different responses.
- The frontend follow-up path catches `AbortError` and infers
  `timeout` vs user-cancel from `controller.signal.reason`
  ([static/main.js:1357-1369](static/main.js#L1357-L1369)). Browsers
  haven't always honoured `AbortController.abort(reason)` with a
  string reason — fragile across runtime versions.

**Rating: solid.** The error topology is mature for a working app —
named stages, stable event types, persistent JSONL trail, never-raises
discipline on observability. The over-broad excepts in the LLM and
classification paths are real but isolated.

---

## 3. Input Validation

Pydantic with `extra="forbid"` is the validation backbone — every cube
model, every mode definition, every parameter spec rejects unknown
fields at parse time
([pipeline/cube/models.py](pipeline/cube/models.py),
[pipeline/registry.py](pipeline/registry.py)). The classifier raises
explicit `ValueError`s for missing template, multi-template collision,
or duplicate template match
([pipeline/loaders/classifier.py](pipeline/loaders/classifier.py)).
Period parsing uses `pd.to_datetime(..., errors="raise")` rather than
silently coercing
([pipeline/templates/base.py](pipeline/templates/base.py)). The verifier
has type-aware tolerance checkers (count exact, currency scaled to
precision, percentage epsilon, date with prose-format support, string
normalised) — see
[pipeline/validate.py](pipeline/validate.py) and the lock-down tests in
[pipeline/tests/test_validate.py](pipeline/tests/test_validate.py).
Parameter validation runs twice: once cube-agnostic at the route edge,
once cube-aware after classification.

Strong examples:
- `validate_parameters(mode, params, cube=None)` for the early pass
  catches missing-required and unknown keys before classification
  runs
  ([server.py:376-390](server.py#L376-L390),
   [pipeline/registry.py](pipeline/registry.py)).
- Currency tolerance scales to the LLM's cited precision: "$1.2B"
  carries a $50M tolerance, "$4.82B" a smaller one — see
  [pipeline/tests/test_validate.py:65-86](pipeline/tests/test_validate.py#L65-L86).
  Catches LLM rounding without false positives.
- Frontend file-extension allowlist on attach
  ([static/main.js:185-191](static/main.js#L185-L191)).

Weak examples:
- No server-side enforcement of upload size. Flask's `MAX_CONTENT_LENGTH`
  is never set
  ([server.py:80](server.py#L80)) and base64 decoding happens before any
  size check
  ([server.py:317-329](server.py#L317-L329)). A multi-GB body will be
  decoded into memory before failing.
- No server-side enforcement of file extension. The frontend
  allowlist
  ([static/main.js:186-191](static/main.js#L186-L191))
  is bypassable by any client; the classifier will then run
  `pd.read_excel` on whatever bytes arrived.
- User prompt is unbounded
  ([server.py:309](server.py#L309), [server.py:333](server.py#L333)) —
  passed straight to the LLM as a `HumanMessage` content. No truncation,
  no token-budget guard.
- `parameters` is validated for shape but values are not
  length-bounded — `{"portfolio": "X"*100000}` would pass the
  cube-aware enum check (it would fail enum membership but only after
  the cube ran).

**Rating: working.** The validation that exists is rigorous and
type-aware; the gaps are all at the I/O boundary (size limits,
server-side type checks, length bounds). For a tool behind a corporate
proxy with internal users this is acceptable; for any deployment with a
broader trust boundary it is not.

---

## 4. Testing

The cube layer has the strongest test discipline in the codebase.
[pipeline/tests/test_smoke_lending.py](pipeline/tests/test_smoke_lending.py)
runs 17 single-concept assertions against a fixture
([pipeline/tests/fixtures/smoke_lending.xlsx](pipeline/tests/fixtures/smoke_lending.xlsx))
generated from
[pipeline/tests/fixtures/_build_smoke_lending.py](pipeline/tests/fixtures/_build_smoke_lending.py).
Expected values are hand-computed in
[pipeline/tests/fixtures/smoke_lending_expected.py](pipeline/tests/fixtures/smoke_lending_expected.py)
with an explicit policy comment that values must NOT be derived from
cube output (would be circular). Each test asserts one business
invariant so a regression points at the broken thing. A determinism
test re-runs `compute_lending_cube` on the same DataFrame and asserts
byte-identical `model_dump_json`
([pipeline/tests/test_smoke_lending.py:309-315](pipeline/tests/test_smoke_lending.py#L309-L315)).
[pipeline/tests/test_validate.py](pipeline/tests/test_validate.py)
locks in tolerance behaviour per type. [pipeline/tests/test_registry.py](pipeline/tests/test_registry.py)
loads the real YAML and validates parameter resolution.

Strong examples:
- Hand-computed expected values with a discipline note that the
  fixture cheat sheet is the source of truth
  ([pipeline/tests/fixtures/smoke_lending_expected.py:5-8](pipeline/tests/fixtures/smoke_lending_expected.py#L5-L8)).
- The fixture exercises every bucket and edge case shipped in the
  five-category rating refactor: blank PD Rating, TBR token, blank
  industry → Unclassified, LF+GRM overlap on F010, watchlist, MoM
  origination/exit/PD/reg changes
  ([pipeline/tests/fixtures/_build_smoke_lending.py:56-158](pipeline/tests/fixtures/_build_smoke_lending.py#L56-L158)).
- Test failure messages name the likely regression cause: e.g.
  industry-reconciliation failure mentions the Unclassified bucket
  fix
  ([pipeline/tests/test_smoke_lending.py:107-112](pipeline/tests/test_smoke_lending.py#L107-L112)).

Weak examples:
- Zero integration tests for the Flask routes. There is no Werkzeug
  test client invocation of `/upload`, `/modes`, or
  `/cube/parameter-options` anywhere under
  [pipeline/tests/](pipeline/tests/). The 591-line `server.py` is
  tested only by manual browser interaction.
- Zero tests of the LLM path. `ask_agent`, `narrate`, the
  structured-output fallback, the `_build_message_sequence` follow-up
  branch — none have a test, even with a mocked Azure client.
- Zero frontend tests — 1,519 lines of vanilla JS in
  [static/main.js](static/main.js) including a stateful AbortController
  follow-up flow, no automated coverage.
- [CLAUDE.md](claude.md) references `pipeline/tests/test_label_collisions.py`
  multiple times but the file does not exist on disk
  (`ls pipeline/tests/` shows only `test_registry.py`,
  `test_smoke_lending.py`, `test_validate.py`). Documentation drift —
  either the test was deleted without updating the docs, or it was
  planned and the docs jumped ahead.
- One workbook fixture covering one shape (10 facilities × 2 periods).
  No malformed-data tests, no large-fixture stress test, no test for
  3+ period MoM, no test of the Path B / future cube-collapse branch.
- No CI configuration in the repo root — no `.github/workflows/`, no
  `.gitlab-ci.yml`. Tests are run on demand.

**Rating: working.** The cube layer is solid-to-production-grade in
isolation; the rest of the system is essentially untested. A change to
`server.py`, `agent.py`, or `static/main.js` has no automated guard.

---

## 5. Observability

Two independent telemetry layers cover the request lifecycle. The
always-on tier is JSONL: every error event becomes one structured
record in `<KRONOS_ERROR_LOG_DIR>/kronos-errors.jsonl` with rotation on
≥10MB or date change
([pipeline/error_log.py](pipeline/error_log.py)). The opt-in tier is
MLflow: when `KRONOS_MLFLOW_ENABLED=true`, every `/upload` becomes an
MLflow run with tags (mode, component), params (file_name,
user_prompt), metrics (latency by stage, narrative_length,
verified_count), and a `context_sent.txt` artifact
([pipeline/tracking.py](pipeline/tracking.py),
[server.py:393-538](server.py#L393-L538)). Stage timings are returned
to the frontend
([server.py:546-550](server.py#L546-L550)) and overwrite placeholder
labels in the loading animation
([static/main.js:571-580](static/main.js#L571-L580)) so what the user
reads is honest. Per-tab session ID via `X-Kronos-Session` correlates
multi-turn requests
([server.py:54-62](server.py#L54-L62),
 [static/main.js:39-63](static/main.js#L39-L63)).

Strong examples:
- Stage-level latency (`analyze_ms`, `llm_ms`, `verify_ms`) logged to
  both the API response and MLflow
  ([server.py:464-514](server.py#L464-L514)).
- Non-fatal data-quality signals are surfaced via
  `cube.metadata.warnings` rather than raised
  ([pipeline/cube/lending.py:155-185](pipeline/cube/lending.py#L155-L185)).
  Triage can detect drift in workbook schemas without breaking the
  request.
- `/errors/recent` is gated, returns 404 (not 403) when disabled, and
  the bound is hardcoded to 500 to prevent log scraping
  ([server.py:571-587](server.py#L571-L587)).

Weak examples:
- `KRONOS_ERROR_LOG_DIR` defaults to `"logs/"` relative to the
  working directory ([CLAUDE.md](claude.md) acknowledges the Domino
  persistent-path TODO). On any deployment that doesn't set it, logs
  vanish on container restart.
- No health-check endpoint (`/healthz`, `/ready`). A load balancer or
  Domino lifecycle hook has no way to probe liveness without
  triggering a real request.
- Token counts (input / output / cost) are only captured when MLflow
  is enabled (via `mlflow.langchain.autolog()`). The default path has
  no visibility into LLM spend.
- The `[KRONOS]` console.log instrumentation in
  [static/main.js](static/main.js) is good for DevTools triage but
  ships to production verbatim — a production build would normally
  strip it.

**Rating: solid.** The dual-tier design (always-on JSONL +
gated MLflow) is the right pattern for a regulated app that needs an
audit trail before its centralised tracking backend is provisioned. The
gaps (working-dir defaults, no health endpoint, no token-count
visibility off-MLflow) are all closable without architectural change.

---

## 6. Determinism / Correctness

Every order-sensitive cube output has an explicit secondary sort
tiebreaker — `Facility ID`, `Ultimate Parent Code`, or rating index —
applied immediately after any join or groupby:
[pipeline/cube/lending.py](pipeline/cube/lending.py) (`_top_contributors`,
`_top_wapd_facility_contributors`, `_pd_rating_changes`,
`_reg_rating_changes`, `_facility_changes`, `_exposure_movers`). The
five rating masks are computed once and reused across firm-level and
per-slice composition so the bucketing rule cannot drift between layers
([pipeline/cube/lending.py:302-325](pipeline/cube/lending.py#L302-L325)).
A coverage invariant emits a `pd_rating_unclassified` warning if any
row escapes the four mutually-exclusive top-level masks
([pipeline/cube/lending.py:155-172](pipeline/cube/lending.py#L155-L172)).
A dim-reconciliation invariant warns if `by_industry` / `by_segment` /
`by_branch` totals diverge from firm totals beyond a $2 tolerance
([pipeline/cube/lending.py:174-185](pipeline/cube/lending.py#L174-L185)).
The LLM runs at `temperature=0.0`
([pipeline/agent.py:165](pipeline/agent.py#L165)). Determinism is
explicitly tested
([pipeline/tests/test_smoke_lending.py:309-315](pipeline/tests/test_smoke_lending.py#L309-L315)).

Strong examples:
- Same-instance invariant: `industry_details[name].grouping IS
  by_industry[name]`
  ([pipeline/cube/lending.py:407-427](pipeline/cube/lending.py#L407-L427))
  — the cube physically cannot disagree with itself.
- `non_rated` mask takes priority over C-code masks defensively, so a
  bad upstream PD value coincidentally matching a C-code can't
  double-count
  ([pipeline/cube/lending.py:312-325](pipeline/cube/lending.py#L312-L325)).
- Per-claim verifier is type-aware with appropriate tolerances per
  type — currency tolerance scales to cited precision
  ([pipeline/validate.py](pipeline/validate.py)).

Weak examples:
- Two known correctness gaps remain open in
  [docs/calculation-audit.md](docs/calculation-audit.md) — the
  `_grouping_by_dim` NaN-drop issue and the WAPD-numerator-as-C07
  data-contract assumption (acknowledged in CLAUDE.md "Still To Do").
- LLM correctness is fundamentally non-deterministic. The verifier
  badge mitigates by flagging mismatches, but `narrate()` falls back
  to plain text on structured-output failure
  ([pipeline/agent.py:329-350](pipeline/agent.py#L329-L350)) — when
  this happens, claims are empty and verification reports
  `no_structured_claims`. The user has no quality signal in that
  case.
- The verifier accepts `{{portfolio}}` substituted into prompts via
  plain string replacement
  ([pipeline/registry.py](pipeline/registry.py),
   [pipeline/tests/test_registry.py:96-101](pipeline/tests/test_registry.py#L96-L101)).
  A missing key leaves the placeholder visible in the LLM's input.
  Tests lock this in as documented behaviour but it's a sharp edge.

**Rating: solid (cube layer); working (LLM layer).** The deterministic
side is visibly engineered for correctness — explicit tiebreakers,
mutual-exclusion masks, reconciliation invariants, hand-computed
expected values. The non-deterministic side is mitigated rather than
solved.

---

## 7. Documentation

Inline documentation density is unusually high. Every Python file has a
header block describing purpose. Every public function has a docstring
with Args/Returns. Comments consistently explain WHY rather than WHAT —
e.g. why `non_rated` takes priority over C-code masks
([pipeline/cube/lending.py:312-325](pipeline/cube/lending.py#L312-L325)),
why the same-instance invariant matters
([pipeline/cube/lending.py:407-411](pipeline/cube/lending.py#L407-L411)),
why the workspace proxy quirk requires base64 transport
([server.py:65-78](server.py#L65-L78)). Three contributor docs anchor
the system: [docs/calculation-audit.md](docs/calculation-audit.md) (a
real read-only audit with ranked gaps),
[docs/adding-a-mode.md](docs/adding-a-mode.md) (step-by-step new-mode
guide), [docs/available-kris.md](docs/available-kris.md) (catalogue of
every KRI and verifiable_value label).
[CONTRIBUTING.md](CONTRIBUTING.md) covers the test workflow.
[docs/future-work.md](docs/future-work.md) tracks deferred items
concretely.

Strong examples:
- The fixture cheat-sheet docstring at the top of
  [pipeline/tests/fixtures/smoke_lending_expected.py:9-44](pipeline/tests/fixtures/smoke_lending_expected.py#L9-L44)
  derives every expected total from inline arithmetic visible in the
  comment.
- [docs/calculation-audit.md](docs/calculation-audit.md) is a real
  audit with ranked severity, not marketing copy. It identifies
  open gaps and they appear later in CLAUDE.md "Still To Do".
- TODO-banner comments mark places future contributors should adjust
  (e.g., `Claim` field descriptions in
  [pipeline/agent.py:78-83](pipeline/agent.py#L78-L83)).

Weak examples:
- [CLAUDE.md](claude.md) references `pipeline/tests/test_label_collisions.py`
  in multiple places but the file does not exist
  (verified with `ls pipeline/tests/`). Either deleted without
  doc update or planned and the docs jumped ahead.
- [config/prompts/portfolio_level.md](config/prompts/portfolio_level.md)
  is an orphan — no `portfolio-level` mode in
  [config/modes.yaml](config/modes.yaml) references it. Boot-time
  validation only checks declared `prompt_template:` paths exist; it
  does not enforce that every prompt file is referenced.
- [PULL_ME.md](PULL_ME.md) is 38KB — the per-round sync log has grown
  past the point where a human can scan it for current state. The
  workflow described in CLAUDE.md ("delete after sync") is not
  visibly being followed.
- No end-user / risk-officer-facing documentation. Everything is
  contributor-oriented; the people who would read the narrative have
  no docs.

**Rating: solid.** Internally, this is documented better than most
codebases this size — the why-not-what comment discipline alone is rare.
Externally, the user-facing surface has nothing.

---

## 8. Dependencies

[requirements.txt](requirements.txt) pins the LangChain stack
(`langchain==0.3.27`, `langchain-openai==0.3.33`, `langgraph==0.6.7`,
`langgraph-checkpoint==2.1.1`), `azure-identity==1.25.0`, and
`mlflow[databricks]==3.7.0` to exact versions, with a comment noting
that the LangChain stack is tested as a set per the AICE cookbook. This
is the right pinning for the most volatile dependencies. However, the
foundational packages — `flask`, `pandas`, `openpyxl`, `pyyaml` — are
unpinned. There is no `pyproject.toml`, no lockfile (no
`requirements-lock.txt` or pip-tools artifact), and no equivalent
mechanism. Reproducibility relies on disciplined manual pinning across
the team. `aice-mlflow-plugins==0.1.3` is required for production
observability but cannot be installed from public PyPI — acknowledged
in CLAUDE.md "Still To Do" and in
[requirements.txt:44-53](requirements.txt#L44-L53), but it is a hard
deployment gap.

Strong examples:
- The four-package LangChain set is pinned with an inline comment
  explaining why
  ([requirements.txt:21-28](requirements.txt#L21-L28)).
- MLflow is gated by `KRONOS_MLFLOW_ENABLED` so a missing
  `aice-mlflow-plugins` install does not crash the app
  ([pipeline/tracking.py](pipeline/tracking.py)).

Weak examples:
- `flask`, `pandas`, `openpyxl`, `pyyaml` unpinned
  ([requirements.txt:9-18](requirements.txt#L9-L18)). A fresh install
  in 2 years could pick up `pandas` 3.x or breaking `openpyxl`
  changes.
- No lockfile. Two developers running `pip install -r requirements.txt`
  on different days can end up with different transitive dependencies.
- No `pyproject.toml`. The project is pip-installable only via a
  requirements file; no metadata, no entry points, no extras.
- No dependency vulnerability scanning visible (no `pip-audit`, no
  Dependabot config, no `.snyk` file).
- `aice-mlflow-plugins` is a hard prerequisite for the documented
  production observability story but lives outside the install
  pipeline. Anyone who clones this repo and follows the README cannot
  reach the documented end state.

**Rating: rough draft.** The most volatile dependencies are pinned
correctly; everything else is informal. For a tool destined for a bank
production environment with reproducibility and audit requirements,
this is the lowest-rated dimension.

---

## 9. Security

The most serious finding is on the entry point.
[server.py:590-591](server.py#L590-L591):

```python
if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
```

`debug=True` enables the Werkzeug interactive debugger, which exposes a
PIN-protected (but PIN-defeatable) interactive Python shell to any
client that triggers an unhandled exception. `host="0.0.0.0"` binds the
server to all interfaces. [app.sh](app.sh) is `#!/usr/bin/env bash\npython
server.py` — i.e. this exact code path is the deployment entry point.
On any environment more exposed than the user's local laptop (Domino
workspace included, since CLAUDE.md notes the workspace runs on port
5000 inside a multi-tenant container), this is an arbitrary code
execution vulnerability if combined with any unhandled exception path.

Beyond that, the auth model is sound (DefaultAzureCredential, no API
keys, [pipeline/agent.py:225-232](pipeline/agent.py#L225-L232)), the
error-log field policy explicitly never logs file contents, narratives,
or user prompts ([pipeline/error_log.py](pipeline/error_log.py)), and
the `/errors/recent` triage endpoint is gated and returns 404 (not 403)
when disabled. HTML insertion goes through `escapeHtml`
([static/main.js:1473-1475](static/main.js#L1473-L1475)).

Strong examples:
- No API keys anywhere in the codebase. Bearer token via
  `DefaultAzureCredential` + scope string
  ([pipeline/agent.py:225-232](pipeline/agent.py#L225-L232)). The
  scope is a hardcoded constant with a "do not change" comment.
- Snippet bounding in error log: ≤500 chars context, ≤250 chars
  prompt
  ([pipeline/error_log.py](pipeline/error_log.py)). Exposure data and
  PII cannot leak through the JSONL trail.
- Session ID is documented as "not auth, not identity — pure
  correlation"
  ([server.py:54-58](server.py#L54-L58)) so its
  client-controllability is correctly scoped.

Weak examples:
- `app.run(debug=True, host="0.0.0.0", port=5000)` as the deployment
  entry point ([server.py:590-591](server.py#L590-L591),
   [app.sh](app.sh)). This is the headline finding.
- No production WSGI server (gunicorn / uwsgi). The Werkzeug dev
  server is explicitly not for production.
- No upload size enforcement. `Flask.MAX_CONTENT_LENGTH` is unset
  ([server.py:80](server.py#L80)) and the base64 path decodes the
  entire body before any size check
  ([server.py:317-329](server.py#L317-L329)).
- No CSRF protection on POST routes, no SameSite cookie strategy
  declared.
- No rate limiting on `/upload` — every request is an LLM call, so
  this is a cost vector as well as a DoS vector.
- No security headers (CSP, X-Frame-Options, X-Content-Type-Options)
  set on responses.
- `prior_narrative` is a client-supplied string echoed verbatim into
  an LLM `AIMessage`
  ([pipeline/agent.py:303-310](pipeline/agent.py#L303-L310)). A
  malicious `prior_narrative` is a prompt-injection vector. Less
  severe in single-user mode (you can only inject yourself), worth
  flagging for any multi-tenant future.

**Rating: rough draft.** The auth and field-policy work is genuinely
careful. The deployment entry point and the lack of WSGI / size limits /
rate limits / security headers mean that today's setup is
"acceptable in the user's local Domino workspace, dangerous anywhere
else."

---

## 10. Code Style / Craftsmanship

Naming is consistent (`compute_lending_cube`,
`_build_portfolio_slice`, `verify_claims`), typing is
modern (`from __future__ import annotations`, `Optional[T]`, `dict[str,
GroupingHistory]`, pydantic-typed everywhere), and the file-header
banner pattern + section dividers are uniform across every Python
module. Docstrings are present on every public function with explicit
Args/Returns. Comments routinely explain *why* a non-obvious decision
was made (e.g., why structured-output fallback exists, why same-instance
invariant matters, why the secondary sort tiebreakers were added).
Module-level constants are named and documented
(`EXPOSURE_VALIDATION_TOLERANCE`, `TOP_N_CONTRIBUTORS`,
`_UNCLASSIFIED_SAMPLE_LIMIT`). Defensive guards are explicitly labelled
as defense-in-depth where they overlap with earlier validation
([pipeline/processors/lending/industry_portfolio_level.py:32-42](pipeline/processors/lending/industry_portfolio_level.py#L32-L42)).

Strong examples:
- The TODO-banner comment style flags exactly the lines a future
  contributor should adjust without bloating the diff
  ([pipeline/agent.py:78-83](pipeline/agent.py#L78-L83),
   [pipeline/agent.py:249-253](pipeline/agent.py#L249-L253)).
- `pipeline/processors/lending/_slice_view.py` uses a
  prefix-disambiguated label scheme
  (`"Industry Portfolio: {name} —"` /
  `"Horizontal Portfolio: {name} —"`) so verifier label lookups can
  never silently resolve to the wrong slicer's value
  ([CLAUDE.md](claude.md) describes this).
- Module-level constants for behaviour-affecting magic numbers, with
  doc comments
  ([pipeline/cube/lending.py:46-65](pipeline/cube/lending.py#L46-L65)).

Weak examples:
- [pipeline/cube/lending.py](pipeline/cube/lending.py) is 968 lines.
  [static/main.js](static/main.js) is 1,519 lines.
  [static/styles.css](static/styles.css) is 1,653 lines. Each is
  internally well-organised but past the size where a new contributor
  can hold the whole file in their head.
- No linter or formatter config (no `.ruff.toml`, no `pyproject.toml`
  with ruff config, no `.prettierrc`, no `.editorconfig`). Code
  style is consistent because one author is disciplined, not because
  it is enforced.
- No type checker config (no `mypy.ini`, no `pyright` config).
  Annotations are present but not validated.
- Some inline commentary is essay-length and would normally live in a
  separate doc — e.g. the multi-paragraph "FUTURE — slice-result
  caching" block in
  [pipeline/agent.py:459-466](pipeline/agent.py#L459-L466) and the
  per-CLAUDE.md round narratives.
- `USE_MOCK_RESULTS` and the unused `typewrite()` function survive in
  production source
  ([static/main.js:29](static/main.js#L29),
   [static/main.js:931-948](static/main.js#L931-L948))
  with comments saying "safe to delete" — not deleted.

**Rating: solid.** The hand-craftsmanship is genuinely high — naming,
typing, comment-the-why discipline, defensive guards labelled as such.
The gaps are mechanical (no enforcement, oversized files, dead code) and
would be closed by introducing tooling, not by rewriting logic.

---

## Overall Rating

**Working, with a few dimensions reaching solid and one (security)
sitting at rough draft.**

The deterministic core (templates → classifier → cube → slicer →
verifier) is the strongest part of the codebase: well-typed pydantic
boundaries, explicit sort tiebreakers, mutual-exclusion masks, hand-
computed test expectations, a determinism test that catches
nondeterministic ordering. That layer is genuinely close to
production-grade in isolation. The narration layer (LangGraph + Azure
OpenAI) is competent and follows the bank's deployment pattern but is
under-tested. The Flask serving layer is functional but the deployment
entry point and lack of production hardening (WSGI server, size limits,
rate limits, security headers) keep the system as a whole at "working,"
not "production-grade."

## What a Senior Reviewer Would Say

The first thing a senior reviewer would notice is the gap between the
careful engineering inside the cube (every order-sensitive output has an
explicit tiebreaker, every model rejects unknown fields, every
invariant is checked and warned on) and the casual posture of the
deployment surface (`app.run(debug=True, host="0.0.0.0", port=5000)` as
the entry point, no WSGI server, no upload size limit, unpinned
foundational dependencies). Those are not the same author writing on
the same day — the cube layer reads like someone optimising for
auditability, and `server.py:591` reads like someone optimising for
"works on my machine." Closing that gap is mostly mechanical —
gunicorn in front, `MAX_CONTENT_LENGTH` set, the four Flask/pandas
deps pinned, the debug flag flipped — but until it is closed the
overall risk profile is dominated by the weakest layer, not the
strongest.

The second thing they would notice is the testing imbalance.
[pipeline/tests/test_smoke_lending.py](pipeline/tests/test_smoke_lending.py)
is the kind of test suite a regulated team would point at as evidence
they take correctness seriously: hand-computed expecteds, single-concept
assertions, a determinism assertion, named regression hints. Then they
would look for the equivalent for `server.py`, `agent.py`, or
`static/main.js` and find nothing. A change to any of those three files
ships untested. That is a decision worth making explicitly, not a
condition worth drifting into.

The third thing they would notice — more cultural than technical — is
the documentation discipline: 70+KB of CLAUDE.md, [docs/calculation-audit.md](docs/calculation-audit.md)
that names its own gaps and ranks them by severity, comments that
explain *why* throughout. That is the signature of a team that wants
the system to outlast its current authors. The drift items
(test_label_collisions.py referenced but missing, orphan prompt file,
38KB PULL_ME.md that nobody is pruning) suggest the documentation is
ahead of the maintenance discipline that would keep it honest.

## Gap Between Current State and Production-Grade for Bank Internal Use

**Small (one focused change each):**
- Replace `app.run(debug=True, host="0.0.0.0", port=5000)` with a
  production WSGI server entry point in [app.sh](app.sh).
  ([server.py:591](server.py#L591))
- Pin `flask`, `pandas`, `openpyxl`, `pyyaml` to exact versions in
  [requirements.txt](requirements.txt).
- Set `Flask.MAX_CONTENT_LENGTH` and add a server-side file-extension
  check in `/upload`.
- Bound `prompt`, `parameters` value, and `prior_narrative` lengths in
  `/upload`.
- Add a `/healthz` route returning `{ "status": "ok" }`.
- Add a `KRONOS_ERROR_LOG_DIR` default that resolves an absolute path,
  not `"logs/"` relative to CWD.
- Reconcile [CLAUDE.md](claude.md) against the actual filesystem
  (drop the `test_label_collisions.py` references or write the file).
- Delete the orphan
  [config/prompts/portfolio_level.md](config/prompts/portfolio_level.md)
  or restore the mode that referenced it.
- Delete `USE_MOCK_RESULTS` + `typewrite()` from
  [static/main.js](static/main.js) once the Domino deploy is stable.
- Tighten `narrate()`'s `except Exception` so Azure auth failures and
  network timeouts do not silently downgrade to plain text
  ([pipeline/agent.py:339-342](pipeline/agent.py#L339-L342)).

**Medium (a thread of work, not one PR):**
- Build a Flask-test-client integration suite for `/upload`, `/modes`,
  and `/cube/parameter-options` covering the eight error event types
  in [server.py](server.py) and the JSON / multipart transport split.
- Build a mocked-LLM test suite for `pipeline/agent.py` covering
  first-turn vs follow-up message shaping, structured-output success,
  structured-output → plain-text fallback, parameter substitution.
- Introduce a lockfile (pip-tools `requirements.txt` + generated
  `requirements-lock.txt`, or migrate to `pyproject.toml` + uv / poetry).
- Add `ruff` + `mypy` config and a CI workflow that runs them plus
  `pytest`. Block merges on red.
- Provision Databricks / AICE access, install
  `aice-mlflow-plugins==0.1.3`, flip `KRONOS_MLFLOW_ENABLED=true`, and
  validate the documented per-request logging actually shows up in the
  Databricks experiment.
- Resolve the two open correctness gaps from
  [docs/calculation-audit.md](docs/calculation-audit.md) — the
  `_grouping_by_dim` NaN-drop and the WAPD-numerator data-contract
  assumption.
- Add rate limiting + per-session quotas on `/upload` so cost and DoS
  cannot run unbounded.
- Add Content-Security-Policy / X-Frame-Options / X-Content-Type-Options
  response headers; verify the Domino proxy isn't already injecting
  them.

**Large (architectural or organisational):**
- Wire the placeholder modes (`industry-within-horizontal`,
  `portfolio-comparison`, `concentration-risk`, `delinquency-trends`,
  `risk-segments`, `exec-briefing`, `stress-outlook`) — six of the ten
  visible UI buttons currently raise `mode_not_implemented`.
- Add a second workbook template
  (`TradedProductsTemplate` per [CLAUDE.md](claude.md)) and refactor
  [pipeline/analyze.py](pipeline/analyze.py) to dispatch on classifier
  output rather than the hardcoded `template_name = "lending"`.
- Decide formally whether the LLM narration layer is a feature or an
  appliance — i.e. whether the verification badge is a product
  acceptance gate or a transparency widget. Today it is the latter, but
  the bank-internal trust model arguably needs the former. Either build
  the gate (block-and-ask-for-rerun on mismatch) or document publicly
  that narratives are advisory.
- Build an end-user-facing manual for the credit / risk officers who
  will read these narratives — not for contributors.
- Stand up a Path B cube collapse per
  [docs/future-work.md](docs/future-work.md) so `by_industry` /
  `by_horizontal` and the `*_details` dicts stop carrying the same data
  in two shapes.
- Establish a documentation-vs-code drift check (e.g. a CI step that
  greps CLAUDE.md for `pipeline/tests/*.py` references and asserts the
  files exist) so docs cannot get ahead of reality silently.
