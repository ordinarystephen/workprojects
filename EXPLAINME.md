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
server.py              ← orchestrator, calls the two pipeline steps
  ├─ analyze(file, mode)         → pipeline/analyze.py
  │    └─ reads Excel with pandas
  │    └─ runs deterministic calculations
  │    └─ returns:
  │         context  = text summary of the data  (goes to the LLM)
  │         metrics  = numbers for the tile panel (goes to the UI)
  │
  └─ ask_agent(context, prompt, mode) → pipeline/agent.py
       └─ gets the system prompt for this mode  → pipeline/prompts.py
       └─ builds a LangChain chain
       └─ calls Azure OpenAI via pipeline/llm.py
       └─ returns: narrative = AI-written analysis text
  ↓
server.py returns JSON: { narrative, metrics }
  ↓
Browser renders narrative + tiles
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

### `pipeline/agent.py` — The LangChain Chain

**What it is:** Builds the LLM call and runs it. You rarely need to edit this file.

**What it does:**
1. Gets the system prompt for the current mode (from `prompts.py`)
2. Builds a LangChain `ChatPromptTemplate` with system + human messages
3. Builds the LLM client (from `llm.py`)
4. Assembles the chain: `prompt | llm | output_parser`
5. Calls `chain.invoke()` with the user question and data context
6. Returns `{ "narrative": "..." }`

**The human-turn template (`HUMAN_TEMPLATE`):**

This controls how the user question and data are presented to the LLM in the
"human" part of the conversation (as opposed to the "system" instructions):

```python
HUMAN_TEMPLATE = """{user_question}

---
Portfolio Data:
{context}
"""
```

`{user_question}` is filled with the user's question or canned prompt text.
`{context}` is filled with the string returned by `analyze()`.

**To customize:**
- Edit `HUMAN_TEMPLATE` if you want to restructure how data is presented
  (e.g. add a "Prior Period Comparison:" section, or request JSON output format)
- Generally do not change anything else in this file

---

### `pipeline/llm.py` — The Azure OpenAI Connection

**What it is:** Builds and returns the Azure OpenAI LLM client. This is the only
file that handles authentication and model configuration.

**How authentication works:**

No API key is required. The app uses Azure AD (Active Directory) managed identity:

- **Locally:** Runs `az login` in your terminal once, then the app picks up your
  credentials automatically every time. No code change needed.
- **On Domino:** The compute environment has a managed identity attached.
  The app detects it automatically. No code change needed.

**Environment variables this file reads:**

| Variable | Required | Example | Notes |
|---|---|---|---|
| `AZURE_OPENAI_DEPLOYMENT` | Yes | `gpt-4o` | Your model deployment name |
| `OPENAI_API_VERSION` | Yes | `2025-04-01-preview` | Must use `api_version=`, not `openai_api_version=` |
| `AZURE_OPENAI_ENDPOINT` | Optional | `https://your-resource.openai.azure.com/` | Omit on Domino — proxy injects it |

**Setting env vars locally:**

Option A — in your shell before running:
```bash
export AZURE_OPENAI_DEPLOYMENT=gpt-4o
export OPENAI_API_VERSION=2025-04-01-preview
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
python server.py
```

Option B — create a `.env` file (add `python-dotenv` to requirements.txt and
load it at the top of `server.py`):
```
AZURE_OPENAI_DEPLOYMENT=gpt-4o
OPENAI_API_VERSION=2025-04-01-preview
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
```

**What you might change:**
- `temperature=0` on line ~70. Raise to 0.3–0.7 if you want more varied language.
  0 = maximally consistent/deterministic output.

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
flask               — web server
pandas              — Excel/CSV reading
openpyxl            — pandas Excel support
langchain==0.3.27   — LangChain framework
langchain-openai==0.3.33  — Azure OpenAI LangChain integration
azure-identity==1.25.0    — Azure AD authentication
```

**Important:** `langchain` and `langchain-openai` are pinned to specific versions
because they change APIs frequently. Do not upgrade them without testing.

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
in `pipeline/llm.py` line: `deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")`.

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
  server.py                  ← START HERE — orchestrator, /upload route
  app.sh                     ← Domino entry point (don't touch)
  requirements.txt           ← Python dependencies
  EXPLAINME.md               ← This file
  CLAUDE.md                  ← AI assistant context (not for you to edit)
  aboutme.md                 ← Integration guide for AI handoffs

  pipeline/
    __init__.py              ← Python package marker (don't touch)
    llm.py                   ← Azure OpenAI connection + auth
    prompts.py               ← EDIT THIS — wrapper prompts, per-mode prompts
    agent.py                 ← LangChain chain (rarely needs editing)
    analyze.py               ← EDIT THIS — register your processor scripts here

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
