# ── KRONOS · server.py ────────────────────────────────────────
# Flask application entry point. Replaces Streamlit's main.py.
#
# This file does three things:
#   1. Serves the static frontend files (index.html, styles.css,
#      main.js, prompts.json) from the /static directory
#   2. Hosts the POST /upload API route — the only endpoint the
#      frontend calls
#   3. Orchestrates the pipeline: file → analyze() → ask_agent()
#      → JSON response back to the browser
#
# ── How to run ────────────────────────────────────────────────
# Locally:       python server.py        (runs on port 8888)
# On Domino:     app.sh calls this file automatically
#
# ── Environment variables needed ──────────────────────────────
# Set these before running (locally in your shell, or in Domino
# project settings under "Environment Variables"):
#
#   AZURE_OPENAI_DEPLOYMENT  = gpt-4o          (or your deployment name)
#   OPENAI_API_VERSION       = 2025-04-01-preview
#   AZURE_OPENAI_ENDPOINT    = https://your-resource.openai.azure.com/
#                              (optional on Domino — proxy handles it)
#
# Auth: no API key needed. Uses `az login` locally, managed identity
# on Domino. See pipeline/llm.py for details.
# ──────────────────────────────────────────────────────────────

import traceback

from flask import Flask, send_from_directory, request, jsonify

# ── Pipeline imports ──────────────────────────────────────────
# analyze()   — reads the uploaded file, runs deterministic analysis,
#               returns { context: str, metrics: dict }
#               Lives in pipeline/analyze.py
#
# ask_agent() — takes the context string + user question + mode,
#               builds a LangChain chain, calls Azure OpenAI,
#               returns { narrative: str }
#               Lives in pipeline/agent.py
from pipeline.analyze import analyze
from pipeline.agent import ask_agent


# ── Flask app ─────────────────────────────────────────────────
# static_folder='static' tells Flask where to find index.html,
# styles.css, main.js, and prompts.json.
app = Flask(__name__, static_folder='static')


# ══════════════════════════════════════════════════════════════
# ── STATIC FILE ROUTES ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
# These serve the frontend. No changes needed here.

@app.route('/')
def index():
    """Serves the main UI (index.html)."""
    return send_from_directory('static', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """
    Serves any other static file by path.
    Handles: styles.css, main.js, prompts.json, and any other
    asset the browser requests from the frontend.
    """
    return send_from_directory('static', path)


# ══════════════════════════════════════════════════════════════
# ── API ROUTE: /upload ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route('/upload', methods=['POST'])
def upload():
    """
    The single API endpoint the frontend calls.

    What comes in (multipart/form-data):
        file   — the uploaded .xlsx, .xls, or .csv file
        prompt — the user's question (from textarea or canned button)
        mode   — the analysis mode slug (e.g. "portfolio-summary")
                 Empty string "" if no canned button is selected

    What goes back (JSON):
        {
            "narrative": "LLM-generated analysis text",
            "metrics":   { "Section": [{ label, value, sentiment }] }
        }

    On error, returns:
        { "error": "description" }  with HTTP 400 or 500
    """

    # ── Step 1: Extract inputs from the request ────────────────
    file   = request.files.get('file')
    prompt = request.form.get('prompt', '').strip()
    mode   = request.form.get('mode', '').strip()

    # ── Step 2: Validate inputs ────────────────────────────────
    if not file:
        return jsonify({"error": "No file uploaded. Please attach an Excel or CSV file."}), 400
    if not prompt:
        return jsonify({"error": "No prompt provided. Please enter a question or select an analysis type."}), 400

    # ── Step 3: Run deterministic analysis ────────────────────
    # analyze() reads the file, runs the processor script for this
    # mode (or the placeholder if the mode isn't registered yet),
    # and returns a structured payload.
    #
    # result["context"]  → the data string the LLM will analyze
    # result["metrics"]  → the tile data for the Data Snapshot panel
    #
    # When your real processor scripts are wired in (pipeline/analyze.py
    # SCRIPT_MAP), this call will run your deterministic_narrative_payload
    # construction instead of the pandas placeholder.
    try:
        result = analyze(file, mode)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500

    # ── Step 4: Call the LLM agent ────────────────────────────
    # ask_agent() builds a LangChain chain with the system prompt
    # for this mode, injects the context and user question, calls
    # Azure OpenAI, and returns { "narrative": "..." }.
    #
    # See pipeline/agent.py for chain details.
    # See pipeline/prompts.py to customize the system/wrapper prompts.
    try:
        agent_response = ask_agent(
            context=result["context"],
            user_prompt=prompt,
            mode=mode,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"LLM call failed: {str(e)}"}), 500

    # ── Step 5: Return the response ───────────────────────────
    # The frontend (main.js) reads:
    #   response.narrative → renders in the left column
    #   response.metrics   → renders as tiles in the Data Snapshot panel
    #
    # metrics is optional — if analyze() returns no metrics or an
    # empty dict, the Data Snapshot panel stays as-is (won't crash).
    return jsonify({
        "narrative": agent_response["narrative"],
        "metrics":   result.get("metrics", {}),
    })


# ══════════════════════════════════════════════════════════════
# ── ENTRY POINT ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # debug=True enables auto-reload on file changes during local dev.
    # On Domino, app.sh calls `python server.py` directly and Domino
    # manages the process — debug mode is fine to leave on.
    app.run(port=8888, debug=True)
