# KRONOS — EXPLAINME

This file is a plain-English walkthrough of every code file in this project.
It is written for someone who understands their domain (credit portfolio analysis)
but is not a software developer. You do not need to understand Python deeply to
use this guide to integrate your work.

---

## What KRONOS Does, In One Sentence

A user uploads an Excel workbook → Python reads and analyzes it → the results
are passed to Azure OpenAI → the AI writes a narrative → the UI displays the
narrative and summary metric tiles.

---

## The Big Picture: Data Flow

```
User browser
  ↓ uploads file + selects a button (or types a question)
  ↓
POST /upload  (HTTP request to Flask)
  ↓
server.py              ← orchestrator, wraps the request in an MLflow run
  │
  └─ with mlflow_run(...) as run:               → pipeline/tracking.py
       (no-op when KRONOS_MLFLOW_ENABLED is off — default)
       │
       ├─ analyze(file, mode)                   → pipeline/analyze.py
       │    └─ reads Excel with pandas
       │    └─ runs deterministic calculations
       │    └─ returns:
       │         context  = text summary of the data  (goes to the LLM)
       │         metrics  = numbers for the tile panel (goes to the UI)
       │
       └─ ask_agent(context, prompt, mode)      → pipeline/agent.py
            └─ invokes a compiled LangGraph
            └─ the graph has one node: narrate
                  └─ gets the system prompt for this mode → pipeline/prompts.py
                  └─ builds the Azure OpenAI client (inline, no separate llm.py)
                  └─ calls the LLM with structured output
            └─ returns: { narrative, claims }
  ↓
server.py returns JSON: { narrative, metrics, claims, context_sent, verification }
  ↓
Browser renders narrative + claims tab + verification badge + tiles + source data panel
```

---

## File-by-File Walkthrough

### `server.py` — The Orchestrator

**What it is:** The Flask web server. It's the "main" file — the thing that
runs when you start the app.

**What it does:**
- Starts a web server on port 8888
- Serves the frontend files (HTML, CSS, JavaScript)
- Receives the file upload from the browser
- Calls `analyze()` then `ask_agent()`
- Sends the result back to the browser as JSON

**What you might need to change:**
- Almost nothing. The logic is fully wired.
- If `ask_agent()` returns a key other than `"narrative"`, update line ~97.
- If your real processor returns a key other than `"context"`, update line ~90.

**What NOT to touch:**
- The static file routes (`@app.route('/')` and `@app.route('/<path:path>')`)
- The Flask app initialization
- Port 8888

---

### `pipeline/analyze.py` — The Analysis Dispatcher

**What it is:** The Python analysis layer. Reads the uploaded Excel file and
produces two things: a text summary (for the LLM) and a metrics dict (for the tiles).

**Current state:** A placeholder processor is wired. It reads any Excel or CSV
with pandas and computes basic statistics (row count, column means, etc.).
This is enough to run end-to-end and see real AI output.

**What you will eventually change:**
1. Import your real processor scripts at the top of the file
2. Register them in `SCRIPT_MAP` (one entry per analysis mode)
3. The `placeholder_processor()` function can be deleted once all modes are covered

**How `SCRIPT_MAP` works:**

```python
SCRIPT_MAP = {
    "portfolio-summary":  portfolio_summary.run,   # mode slug → function
    "concentration-risk": concentration_risk.run,
    # ...
}
```

The key (e.g. `"portfolio-summary"`) must match the `"mode"` field in
`static/prompts.json`. When a user clicks the "Portfolio Summary" button,
the frontend sends `mode="portfolio-summary"` and `analyze()` calls
`portfolio_summary.run(file_obj)`.

**What your processor functions must return:**

```python
def run(file_obj) -> dict:
    # ... your analysis ...
    return {
        "context": "A text string summarizing the key findings. This is what the LLM reads.",
        "metrics": {
            "Section Name": [
                { "label": "Metric Label", "value": "4.1%", "delta": "+0.4pp", "sentiment": "negative" }
            ]
        }
    }
```

`context` is the string that goes to the LLM. It should be prose-style, not
raw JSON — the AI narrates better from readable text.

`metrics` is the tile panel data. `sentiment` controls tile color:
`"positive"` → green, `"negative"` → red, `"warning"` → amber, `"neutral"` → grey.
`delta` is optional.

---

### `pipeline/prompts.py` — The Wrapper Prompts

**What it is:** All the LLM prompt templates. This is the primary file to edit
to control what the AI says and how it says it.

**Two types of prompts:**

**1. `SYSTEM_PROMPT` — the global wrapper prompt**

This is the AI's persona and standing rules. It is sent on every request,
regardless of which analysis mode is selected.

This is where you paste your wrapper prompt from Domino v1.

```python
SYSTEM_PROMPT = """You are a credit portfolio analyst...

TODO: Replace with your actual wrapper prompt from Domino v1.
"""
```

**2. `MODE_SYSTEM_PROMPTS` — per-mode prompts**

Each analysis mode can have its own system prompt that overrides the global one.
If a mode has an entry here, it is used instead of `SYSTEM_PROMPT`.
If a mode does NOT have an entry here, `SYSTEM_PROMPT` is used as the fallback.

```python
MODE_SYSTEM_PROMPTS = {
    "portfolio-summary": """You are a credit portfolio analyst.
    Focus on: overall health, key risk indicators...
    TODO: Replace with your actual portfolio-summary prompt.
    """,

    "exec-briefing": """You are a senior risk officer...
    Respond with exactly 3-5 bullet points only.
    TODO: Replace with your actual exec-briefing prompt.
    """,
}
```

**To customize:**
1. Open `pipeline/prompts.py`
2. Replace the text inside `SYSTEM_PROMPT` with your global wrapper prompt
3. For each mode, replace the text inside its `MODE_SYSTEM_PROMPTS` entry
4. If a mode should use the global prompt, simply delete its entry from `MODE_SYSTEM_PROMPTS`

You do not need to touch any other file to change prompt behavior.

---

### `pipeline/agent.py` — The LangGraph Agent (Bank-Standard Pattern)

**What it is:** Builds the LLM call using the bank-standard LangGraph pattern
from the AICE cookbook. Also builds the Azure OpenAI client directly inside
this file (there is no separate `llm.py` anymore).

**You rarely need to edit this file.** It follows a template that your bank's
code reviewers will recognize — deviating from it makes deployment review harder.

**What's inside (roughly in order from top of file):**

1. **`Claim` and `NarrativeResponse` (Pydantic models)** — the shape the LLM
   is forced to return. `NarrativeResponse.narrative` is the text; `claims` is
   the list that populates the Claims tab.
2. **`MlflowConfigAgentContext` + `Context` (Pydantic)** — runtime params
   (model name, temperature, api_version). Defaults come from environment
   variables. This is the cookbook pattern — do not modify `MlflowConfigAgentContext`.
3. **`State` (Pydantic)** — the data that flows through the graph: `messages`,
   `context_data`, `mode`, `narrative`, `claims`.
4. **`create_llm()`** — builds the `AzureChatOpenAI` client using
   `DefaultAzureCredential` + bearer token. No API key.
5. **`HUMAN_TEMPLATE`** — how the user's question and portfolio data are
   presented to the LLM in the "human" turn.
6. **`narrate()` + `anarrate()`** — the graph node (sync + async). Builds the
   prompt, calls the LLM with structured output, falls back to plain text if
   the LLM returns non-compliant JSON.
7. **`load_graph()`** — compiles the StateGraph. One node today: `narrate`.
8. **`ask_agent(context, user_prompt, mode)`** — the Flask-facing entry point.
   `server.py` calls this. Returns `{ narrative, claims }`.
9. **`LangGraphResponsesAgent`** — MLflow-compatible wrapper. Not called by
   Flask at runtime today — present so this file can be logged as an MLflow
   model later (for AICE Studio deployment).
10. **`mlflow.models.set_model(...)`** — bottom of the file. Registers the
    agent for MLflow model-from-code logging. Inert unless MLflow is logging
    this file.

**The human-turn template (`HUMAN_TEMPLATE`):**

```python
HUMAN_TEMPLATE = """{user_question}

---
Portfolio Data:
{context}
"""
```

`{user_question}` is filled with the user's question or canned prompt text.
`{context}` is filled with the string returned by `analyze()`.

**Environment variables this file reads (via `create_llm()`):**

| Variable | Required | Example | Notes |
|---|---|---|---|
| `AZURE_OPENAI_DEPLOYMENT` | Yes | `gpt-4o` | Your model deployment name |
| `OPENAI_API_VERSION` | Yes | `2025-04-01-preview` | Must use `api_version=`, not `openai_api_version=` |
| `AZURE_OPENAI_ENDPOINT` | Optional | `https://your-resource.openai.azure.com/` | Omit on Domino — proxy injects it |

**Authentication:** No API key. Uses Azure AD:
- **Locally:** `az login` once in your terminal. The app picks up your session.
- **On Domino:** Managed identity attached to the compute environment. Automatic.

**What you might change:**
- `HUMAN_TEMPLATE` — if you want to restructure how data is presented to the LLM
- `Context.temperature` default — raise from 0.0 if you want more varied language
- `Claim` Field descriptions — these are the instructions the LLM sees for what
  each claim field should contain

**What NOT to touch:**
- `MlflowConfigAgentContext` — cookbook pattern, copied verbatim
- `LangGraphResponsesAgent` — cookbook pattern for MLflow deployment
- The `mlflow.models.set_model(...)` call at the bottom

---

### `pipeline/tracking.py` — MLflow Audit Logging (Kill Switch Included)

**What it is:** The MLflow observability layer. Logs every `/upload` request
as an MLflow run for production audit and compliance. **It is OFF by default.**

**Why it's off by default:** The bank's MLflow runs on Databricks via the
internal `aice-mlflow-plugins` package. Until that access is provisioned for
your user, we don't want the app attempting to connect at startup. When your
access is ready, flip one environment variable and everything activates.

**How to turn it on:**

```bash
export KRONOS_MLFLOW_ENABLED=true
export MLFLOW_EXPERIMENT_NAME=kronos-dev    # or kronos-prod at deployment
pip install aice-mlflow-plugins==0.1.3       # internal bank package
```

Then restart the Flask app.

**What gets logged per request (when enabled):**

| What | Values |
|---|---|
| Tags | `kronos.mode` (e.g. portfolio-summary), `kronos.component` (upload) |
| Params | `user_prompt` (truncated), `file_name` |
| Metrics | `file_size_bytes`, `context_length`, `narrative_length`, `claims_count`, `verified_count`, `unverified_count`, `latency_ms` |
| Artifacts | `context_sent.txt` — the exact data string the LLM saw |
| Auto traces | Full LangChain invocation trace via `mlflow.langchain.autolog()` — prompt, response, token counts |

**Safety:** Every MLflow call is wrapped in try/except. If tracking fails mid-request,
the user still gets their analysis — the app logs a warning but does not 500.

**What you might change:**
- Add more logged fields to the `mlflow_run()` context manager in this file
- Adjust truncation limits on params

**What NOT to touch:**
- The kill-switch logic — that's the whole point of this file
- The lazy imports — they protect the app when mlflow isn't available

---

### `static/prompts.json` — The Canned Analysis Buttons

**What it is:** A simple list file that controls which quick analysis buttons
appear on the landing page.

**Why it matters:** This is the ONLY file you edit to add, remove, or rename
canned analysis buttons. You never touch HTML or JavaScript to change buttons.

**Structure:**

```json
[
  {
    "title": "Portfolio Summary",
    "desc": "Health overview & key indicators",
    "mode": "portfolio-summary",
    "prompt": "Provide an executive summary of the overall portfolio health..."
  }
]
```

- `title` — the button label
- `desc` — the subtitle shown under the title
- `mode` — the slug sent to the backend. Must match a key in `SCRIPT_MAP` in
  `pipeline/analyze.py` AND a key in `MODE_SYSTEM_PROMPTS` in `pipeline/prompts.py`
- `prompt` — the full question pre-filled into the textarea when this button is clicked

**To add a new mode:**
1. Add an entry to `prompts.json` with a new `mode` slug
2. Add a matching entry to `SCRIPT_MAP` in `pipeline/analyze.py`
3. Optionally add a matching entry to `MODE_SYSTEM_PROMPTS` in `pipeline/prompts.py`

That's it. The button appears automatically on next page load.

**To remove a button:** Delete its entry from `prompts.json`.

**To change a button's prompt text:** Edit the `"prompt"` field in `prompts.json`.

---

### `pipeline/__init__.py` — Python Package Marker

**What it is:** An empty file that tells Python "this directory is a package."

**Why it's there:** Without it, `from pipeline.analyze import analyze` would fail.

**What to do with it:** Nothing. Leave it alone.

---

### `requirements.txt` — Python Dependencies

**What it is:** The list of Python packages this project needs.

**Current contents:**

```
flask                           — web server
pandas                          — Excel/CSV reading
openpyxl                        — pandas Excel support
langchain==0.3.27               — LangChain framework
langchain-openai==0.3.33        — Azure OpenAI LangChain integration
langgraph==0.6.7                — LangGraph StateGraph (bank-standard agent pattern)
langgraph-checkpoint==2.1.1     — LangGraph state persistence (for future multi-turn)
azure-identity==1.25.0          — Azure AD authentication
mlflow[databricks]==3.7.0       — Production observability / compliance logging
```

**Not on PyPI — install separately in your bank Python environment:**
```
aice-mlflow-plugins==0.1.3      — internal bank package, enables MLflow routing
                                   to Databricks. Follow AICE onboarding docs.
                                   KRONOS runs without it (with a warning) so
                                   initial deployment isn't blocked.
```

**Important:** `langchain`, `langchain-openai`, `langgraph`, and `mlflow` are
pinned to the versions from the bank's AICE LangGraph cookbook. They are tested
as a set. Do not upgrade any one in isolation without testing — they ship
breaking API changes.

**On Domino:** Declare these in the project environment configuration, not
installed manually via pip.

---

### `app.sh` — Domino Entry Point

**What it is:** The shell script Domino calls to start the app.

**Contents:**
```bash
#!/usr/bin/env bash
python server.py
```

**What to do with it:** Nothing. Domino runs this automatically when the app is published.

---

### `static/index.html`, `static/styles.css`, `static/main.js`

**What they are:** The frontend UI — HTML structure, design system, and all
interactive behavior.

**When to touch them:**
- `index.html` — only if you need to change the page structure itself (rarely needed)
- `styles.css` — only if you need to change colors, fonts, or layout
- `main.js` — only if you need to change frontend behavior

**Key thing to remove from `main.js` when the app is live:**
Search for `TODO REMOVE ON MERGE` in main.js. There are 3 locations where
mock data fallback is used. Once your backend is running and returning real data,
delete those mock data calls.

---

## Step-by-Step Integration Guide

### Step 1 — Get the app running locally (placeholder mode)

```bash
pip install -r requirements.txt
az login                                    # authenticate with Azure AD
export AZURE_OPENAI_DEPLOYMENT=gpt-4o
export OPENAI_API_VERSION=2025-04-01-preview
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
python server.py
# open http://localhost:8888
```

Upload any Excel file, select any button, hit Run Analysis.
You should get a real AI response narrating the pandas summary of your file.

---

### Step 2 — Add your wrapper prompts

Open `pipeline/prompts.py`.

1. Replace the body of `SYSTEM_PROMPT` with your global wrapper prompt from Domino v1.
2. For each mode, replace the placeholder text in `MODE_SYSTEM_PROMPTS` with
   your mode-specific prompt.
3. Restart the server and test again.

---

### Step 3 — Wire in your real processor scripts

This is where your existing `processor.py` work comes in.

Your processor must:
1. Accept a `file_obj` (the Flask file upload object)
2. Run your deterministic analysis
3. Return `{ "context": str, "metrics": dict }`

The `context` string is what the LLM narrates from. Make it a readable prose
summary of the key figures — not raw JSON. Think of it as "what would I tell
a colleague in plain English about this portfolio?"

Once you have a `run(file_obj)` function, register it:

```python
# In pipeline/analyze.py, at the top:
from pipeline.scripts import portfolio_summary

# In SCRIPT_MAP:
SCRIPT_MAP = {
    "portfolio-summary": portfolio_summary.run,
    # ...
}
```

---

### Step 4 — Remove mock data fallback

In `static/main.js`, search for `TODO REMOVE ON MERGE`.
Delete the 3 marked locations (getMockResult function and its two call sites).

---

### Step 5 — Deploy to Domino

1. Push this branch to your Domino project
2. Set environment variables in Domino project settings:
   - `AZURE_OPENAI_DEPLOYMENT`
   - `OPENAI_API_VERSION`
   - `AZURE_OPENAI_ENDPOINT` (if needed — your Domino setup may inject this)
3. Publish via `app.sh`
4. Smoke test with a real file

---

### Step 6 — Turn on MLflow tracking (when Databricks access is ready)

MLflow is built in but DORMANT. It will not attempt any network calls until
the kill-switch env var is set. This lets you deploy before your bank AICE
access is provisioned.

When your Databricks / AICE access is ready:

1. Install the internal package in your environment:
   ```bash
   pip install aice-mlflow-plugins==0.1.3
   ```

2. Set the env vars (locally or in Domino project settings):
   ```bash
   export KRONOS_MLFLOW_ENABLED=true
   export MLFLOW_EXPERIMENT_NAME=kronos-dev
   ```
   (Switch to `kronos-prod` when you move from dev to prod.)

3. Restart the Flask app.

4. Submit a test request. Check that a new run appears in Databricks MLflow
   under your experiment. You should see tags, params, metrics, and an
   auto-captured LangChain trace.

If step 4 fails, check logs for the `MLflow activation failed` warning — this
tells you the activation error without crashing the app. Most common cause:
Domino compute environment can't reach your Databricks workspace. Talk to
your AICE admin.

---

## Frequently Asked Questions

**Q: Where do I put my wrapper prompt?**
A: `pipeline/prompts.py` — `SYSTEM_PROMPT` for the global one, `MODE_SYSTEM_PROMPTS`
for per-mode variants. Look for the `TODO: Replace` markers.

**Q: How do I add a new analysis button?**
A: Add one entry to `static/prompts.json`. The button appears automatically.
Then add the mode slug to `SCRIPT_MAP` in `pipeline/analyze.py` and optionally
to `MODE_SYSTEM_PROMPTS` in `pipeline/prompts.py`.

**Q: How do I change which model is used?**
A: Set the `AZURE_OPENAI_DEPLOYMENT` environment variable. Or change the default
in `pipeline/agent.py` on the `Context` class (look for
`model: Optional[str] = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")`).

**Q: Where did `pipeline/llm.py` go?**
A: It was removed during the bank-standard refactor. The Azure OpenAI client
is now built inside `pipeline/agent.py` via the `create_llm()` function — this
matches the AICE LangGraph cookbook pattern. Same behavior, same env vars, just
one less file to hop through.

**Q: How do I turn on MLflow tracking?**
A: Set `KRONOS_MLFLOW_ENABLED=true` and optionally `MLFLOW_EXPERIMENT_NAME=kronos-dev`.
Install `aice-mlflow-plugins==0.1.3` in your bank Python environment. Restart
the app. See Step 6 in the integration guide for details.

**Q: What if MLflow can't reach Databricks from Domino?**
A: The activation wraps everything in try/except — you'll get a warning log
(`MLflow activation failed`) at startup and the app will run without tracking.
Requests still succeed. Talk to your AICE admin to get the networking sorted.

**Q: Why is the tile panel showing "Field Averages (Placeholder)"?**
A: Your real processor isn't wired yet. The placeholder processor generates basic
pandas stats. Once you register your real processor in `SCRIPT_MAP`, it returns
your actual metrics and the placeholder tiles disappear.

**Q: The AI response feels generic / not specific enough.**
A: Two things to check:
1. `pipeline/prompts.py` — is your system prompt specific enough? It should
   include domain context, output format rules, and what to focus on.
2. `pipeline/analyze.py` — is `context` (the data string) rich enough?
   The LLM can only narrate what it's given. A thin summary = thin analysis.

**Q: I get an authentication error locally.**
A: Run `az login` in your terminal. DefaultAzureCredential will pick up your
session. Make sure you're logged into the correct Azure tenant.

**Q: Do I need to commit `.env` files?**
A: No — and don't. The `.gitignore` excludes `.env` files. Never commit credentials.

---

## File Map Summary

```
kronos/
  server.py                  ← START HERE — orchestrator, /upload route,
                                activates MLflow, wraps request in mlflow_run
  app.sh                     ← Domino entry point (don't touch)
  requirements.txt           ← Python dependencies
  EXPLAINME.md               ← This file
  claude.md                  ← AI assistant context (not for you to edit)
  aboutme.md                 ← Integration guide for AI handoffs

  pipeline/
    __init__.py              ← Python package marker (don't touch)
    prompts.py               ← EDIT THIS — wrapper prompts, per-mode prompts
    agent.py                 ← LangGraph + ResponsesAgent (bank-standard; rarely edit)
                                Also builds the Azure OpenAI client (create_llm)
    analyze.py               ← EDIT THIS — register your processor scripts here
    validate.py              ← Number cross-check for verification badge
    tracking.py              ← MLflow audit logging (off by default, kill-switch)

  static/
    index.html               ← UI structure
    styles.css               ← Design system
    main.js                  ← Frontend interactions
    prompts.json             ← EDIT THIS — add/remove/edit canned buttons
```

**The three files you will edit most:**
1. `pipeline/prompts.py` — your wrapper prompts
2. `pipeline/analyze.py` — your processor script registrations
3. `static/prompts.json` — your canned button definitions

**The file you'll edit once:** `pipeline/tracking.py` — only when you want to
change what gets logged to MLflow (add a metric, change truncation, etc.).
The kill-switch itself doesn't need editing — it's driven by env vars.
