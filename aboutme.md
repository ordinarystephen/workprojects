# KRONOS — Integration Guide

## What This Is

KRONOS is an internal credit portfolio intelligence tool built as a **Flask web app** (not Streamlit). It accepts an Excel file upload, runs a deterministic Python analysis pipeline, passes the structured output to an LLM, and renders the narrative + metrics in a custom HTML/CSS/JS UI.

---

## Why Flask, Not Streamlit

Domino Data Lab supports both Streamlit and Flask as published app frameworks. KRONOS uses **Flask** because the UI requires full design control — custom layout, animations, multi-turn chat thread, drag-and-drop file attachment — none of which are achievable within Streamlit's component model. Flask serves the HTML/CSS/JS as static files and exposes a clean REST API for the frontend to call.

---

## How It Runs on Domino

Domino published apps work by calling a shell entry point. For KRONOS that is `app.sh`:

```bash
#!/usr/bin/env bash
python server.py
```

`server.py` starts Flask on **port 8888**, which is the port Domino expects. Domino proxies the URL and handles authentication — no login logic is needed inside the app.

### File Structure

```
/kronos
  app.sh              ← Domino entry point
  server.py           ← Flask app
  requirements.txt    ← Python dependencies
  /static
    index.html        ← UI (served by Flask)
    styles.css
    main.js
  /pipeline
    analyze.py        ← YOUR PIPELINE GOES HERE (see below)
```

---

## The API Contract

The frontend calls one endpoint:

```
POST /upload
Content-Type: multipart/form-data

Fields:
  file    → .xlsx / .xls / .csv
  prompt  → string (user's question)
```

Expected JSON response shape:

```json
{
  "narrative": "string — LLM-generated analysis text",
  "metrics": {
    "Section Name": [
      {
        "label": "Metric Label",
        "value": "12.4%",
        "delta": "+1.2pp",
        "sentiment": "positive"
      }
    ]
  }
}
```

`metrics` is optional — if omitted, the Data Snapshot panel stays as-is. `sentiment` controls card color: `"positive"` → green, `"negative"` → red, `"warning"` → amber, `"neutral"` → grey.

---

## Pipeline Architecture

KRONOS uses a **multi-script pipeline** where the analysis that runs depends on the mode the user selects. Each mode maps to a dedicated processor script. A shared agent layer narrates the output via Azure OpenAI.

### How Your Existing Code Maps to KRONOS

| Your existing file | Role in KRONOS | KRONOS working name |
|---|---|---|
| `processor.py` | Runs deterministic analysis, reduces to `commentary_facts`, builds `analysis_result` | `pipeline/analyze.py` (dispatcher) |
| `agent_client.py` | Turns `analysis_result` into prompt payloads, builds `custom_inputs`, normalizes response | `pipeline/agent.py` |
| `workbook_agent.py` | Prepends system prompt, calls Azure OpenAI via `AzureChatOpenAI` | Called internally by `agent_client.py` |
| `kronos/llm/prompts/prompts.py` | System + user prompt templates | Called internally by `agent_client.py` |
| `main.py` (Streamlit) | Orchestrates the flow, renders UI | `server.py` (Flask `/upload` route) |

### Full Flow (KRONOS version)

```
POST /upload  (file + prompt + mode)
      ↓
server.py  →  calls analyze(file, mode)
      ↓
processor.py  →  runs deterministic analysis on workbook
              →  reduces large JSON output to commentary_facts
              →  builds analysis_result package
      ↓
server.py  →  calls ask_workbook_agent(commentary_facts, prompt)
      ↓
agent_client.py  →  builds prompt payloads via prompts.py
                 →  builds custom_inputs + final payload
      ↓
workbook_agent.py  →  prepends system prompt
                   →  calls Azure OpenAI via AzureChatOpenAI
      ↓
agent_client.py  →  normalizes response, returns structured result
      ↓
server.py  →  returns { narrative, metrics } as JSON
      ↓
Frontend renders narrative + data snapshot tiles
```

### Key Concepts and Name Mappings

| Object | Role | Status |
|---|---|---|
| `raw_results` | Full deterministic JSON output from the processor script | Keep — survives as secondary payload |
| `commentary_facts` | Original reduced narrative subset | Keep for backward compatibility — no longer primary LLM input |
| `deterministic_narrative_payload` | **New primary LLM-facing payload** — scoped to the user ask, smaller than `raw_results`, richer and more faithful than `commentary_facts` | Build this — replaces `commentary_facts` as what `agent_client.py` receives |
| `analysis_result` | Package that contains the payload + metric data | Updated to carry `deterministic_narrative_payload` as primary |
| `ask_workbook_agent()` | Key integration function in `agent_client.py` | Updated to receive `deterministic_narrative_payload` instead of `commentary_facts` |

### The Payload Architecture Change

The current problem: `commentary_facts` is too lossy — it reduces the deterministic output aggressively and the LLM narrates from a thin shadow of the real data. `raw_results` is too large to pass directly. Neither is right.

The fix: build `deterministic_narrative_payload` in `processor.py` as a third object that sits between the two:

```
raw_results                        ← full deterministic JSON, kept as-is
    ↓
deterministic_narrative_payload    ← NEW: scoped to the user's ask/mode
                                      smaller than raw_results
                                      richer and more faithful than commentary_facts
                                      PRIMARY object passed to the LLM
    ↓
commentary_facts                   ← kept for backward compatibility, no longer primary
```

**Changes required in your existing codebase:**

**`processor.py`:**
- Add a new reduction step that produces `deterministic_narrative_payload` from `raw_results`
- This step should be mode-aware — scope the payload to what is relevant for the specific user ask
- Keep the existing `commentary_facts` reduction step (backward compat)
- Package `deterministic_narrative_payload` as the primary field in `analysis_result`

**`agent_client.py`:**
- Update to read `deterministic_narrative_payload` from `analysis_result` instead of `commentary_facts`
- Use it as the primary content passed to the prompt builder (`prompts.py`) and into `custom_inputs`
- `commentary_facts` can remain available on `analysis_result` but should not drive the LLM call

**What KRONOS's `server.py` receives (unchanged):**
The `/upload` route still calls `ask_workbook_agent(context, prompt)` and gets back `{ narrative, metrics }`. The internal payload upgrade is transparent to `server.py` — it just benefits from a better narrative in the response.

### Processor Script Contract

Every mode-specific processor script must return this shape so `server.py` can package the response:

```python
def run(file_obj) -> dict:
    # run deterministic analysis
    # reduce to commentary_facts
    return {
        "context": "commentary_facts string — passed to ask_workbook_agent()",
        "metrics": {
            "Section Name": [
                { "label": "...", "value": "...", "delta": "...", "sentiment": "positive|negative|warning|neutral" }
            ]
        }
    }
```

### Adding a New Mode

1. Build your processor script and register it in `SCRIPT_MAP` in `pipeline/analyze.py`
2. Add a corresponding entry to `static/prompts.json` with a matching `mode` slug
3. The frontend and `server.py` route it automatically — no other changes needed

---

## Wiring `server.py` — The Integration Point

`server.py` replaces `main.py`. The `/upload` route is the only thing to implement.
Azure OpenAI config lives in `agent_client.py` / `workbook_agent.py` — no need to re-wire it here.

```python
# In server.py — once pipeline imports are ready:

from pipeline.analyze import analyze           # or processor.py once renamed
from your_path.agent_client import ask_workbook_agent

@app.route('/upload', methods=['POST'])
def upload():
    file   = request.files['file']
    prompt = request.form.get('prompt', '')
    mode   = request.form.get('mode', '')      # routes to correct processor script

    result         = analyze(file, mode)       # processor.py: deterministic → commentary_facts + metrics
    agent_response = ask_workbook_agent(result['context'], prompt)  # agent_client.py → workbook_agent.py

    return jsonify({
        "narrative": agent_response['narrative'],   # adjust key to match what ask_workbook_agent returns
        "metrics":   result['metrics']
    })
```

**Environment variables** — already configured for your Azure deployment. Confirm they are set in Domino's project settings:
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_KEY`
- `AZURE_OPENAI_DEPLOYMENT`

---

## Demo → Production Checklist

Everything below needs to be completed to go from the working mock demo to a live, pipeline-connected app.

### Backend

- [ ] **Port `processor.py` into KRONOS** — bring your existing processor into `pipeline/analyze.py` (or rename that file to `processor.py` and update the import in `server.py`). Register each analysis mode in `SCRIPT_MAP`. Each mode's script must return `{ context: commentary_facts, metrics: {...} }`.
- [ ] **Port `agent_client.py` and `workbook_agent.py`** — bring these into the merged codebase. No changes needed to their internal logic. `server.py` calls `ask_workbook_agent()` from `agent_client.py`.
- [ ] **Port `kronos/llm/prompts/prompts.py`** — keep as-is, referenced internally by `agent_client.py`.
- [ ] **Implement `POST /upload` in `server.py`** — uncomment the stub. Wire: `analyze(file, mode)` → `ask_workbook_agent(commentary_facts, prompt)` → `jsonify({ narrative, metrics })`.
- [ ] **Confirm environment variables in Domino** — `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` (likely already set for your existing app)
- [ ] **Build `deterministic_narrative_payload` in `processor.py`** — new primary LLM-facing payload, mode-scoped, sits between `raw_results` (too large) and `commentary_facts` (too lossy). Replaces `commentary_facts` as what `agent_client.py` receives. Keep `commentary_facts` for backward compat. See "The Payload Architecture Change" in Pipeline Architecture above.
- [ ] **Update `agent_client.py`** — read `deterministic_narrative_payload` from `analysis_result` instead of `commentary_facts` when building prompt payloads and `custom_inputs`.

### Frontend

- [ ] **Remove mock fallback in `main.js`** — find the `TODO: Remove getMockResult()` comment and delete the `|| getMockResult()` fallback once real data flows
- [ ] **Define your real metric schema** — decide which sections and metrics your pipeline will always return, so the Data Snapshot tiles render correctly (see "Data Snapshot Tiles" section below — this is the next major task)
- [ ] **Update `prompts.json`** — replace or extend the placeholder quick-analysis prompts with ones tuned to your actual data and use case

### Testing

- [ ] **Test with a real `.xlsx` file end-to-end** — verify file parses, metrics render, narrative returns
- [ ] **Test follow-up turns** — confirm second prompt reuses the same file and updates the Data Snapshot panel when new metrics are returned
- [ ] **Deploy and smoke test on Domino** — run via `app.sh`, confirm port 8888, verify env vars are picked up

---

## Data Snapshot Tiles — Next Task

The Data Snapshot panel on the right side of the results screen is already fully dynamic. The frontend renders whatever `metrics` object comes back in the API response — no hardcoded tile definitions exist in the HTML or JS.

**The schema the frontend expects:**

```json
"metrics": {
  "Section Title": [
    {
      "label": "Metric Name",
      "value": "4.1%",
      "delta": "+0.4pp",        
      "sentiment": "negative"   
    }
  ]
}
```

- `delta` is optional — omit if not applicable
- `sentiment` controls tile color: `"positive"` → green, `"negative"` → red, `"warning"` → amber, `"neutral"` → grey
- Multiple sections are supported — each becomes a labeled group in the grid
- Tile count and sections can vary per prompt — if a follow-up returns different metrics, the panel re-renders

**What needs to be decided:**

1. Which metrics does your Python pipeline always calculate deterministically (these should always appear in the tile grid)?
2. Which metrics are prompt-dependent (optional tiles that only appear for certain analyses)?
3. What are your section groupings (e.g. Portfolio Overview, Credit Quality, Loss Metrics)?

Once your `analyze()` function is built, map its output fields to this schema and the tiles will render automatically.

---

## Prompt for Work AI — Integration Handoff

Use this prompt when handing off to another AI assistant to integrate your existing pipeline with KRONOS:

---

> I have a Flask web app called KRONOS running on Domino Data Lab (port 8888, entry point `app.sh`). The UI is fully built in vanilla HTML/CSS/JS. I need to wire it to my existing Python analysis pipeline.
>
> **The only API endpoint the frontend calls is:**
> ```
> POST /upload
> Content-Type: multipart/form-data
> Fields: file (.xlsx/.xls/.csv), prompt (string)
> ```
>
> **The frontend expects this exact JSON shape back:**
> ```json
> {
>   "narrative": "string — LLM-generated text",
>   "metrics": {
>     "Section Name": [
>       { "label": "string", "value": "string", "delta": "string", "sentiment": "positive|negative|warning|neutral" }
>     ]
>   }
> }
> ```
> `metrics` is optional. `sentiment` controls card color in the UI.
>
> **The LLM is Azure OpenAI** — not Anthropic. Use the `AzureOpenAI` client from the `openai` SDK. Credentials come from environment variables `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, and `AZURE_OPENAI_DEPLOYMENT`.
>
> **My existing pipeline:** [paste your current app.py or describe your analyze logic here]
>
> Help me: (1) extract the core analysis logic into a standalone `analyze(file_obj) -> dict` function in `pipeline/analyze.py`, (2) wire `server.py` to call it and format the output into the metrics schema, and (3) call Azure OpenAI to generate the narrative. The `aboutme.md` file in my project has full integration context.

---

## Local Development

```bash
pip install -r requirements.txt
python server.py
# open http://localhost:8888
```

`main.js` falls back to mock data automatically when the backend is unreachable, so the UI runs and demos without a live API key.
