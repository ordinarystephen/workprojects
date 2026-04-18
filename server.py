# ── KRONOS · server.py ────────────────────────────────────────
# Flask application entry point.
#
# This file orchestrates the full request lifecycle:
#   file upload → analyze() → ask_agent() → cross_check_numbers() → JSON
#
# ── Environment variables ──────────────────────────────────────
# Set before running (locally in shell, or in Domino project settings):
#   AZURE_OPENAI_DEPLOYMENT  = gpt-4o
#   OPENAI_API_VERSION       = 2025-04-01-preview
#   AZURE_OPENAI_ENDPOINT    = https://your-resource.openai.azure.com/
#                              (optional on Domino — proxy handles it)
# ──────────────────────────────────────────────────────────────

import traceback

from flask import Flask, send_from_directory, request, jsonify

from pipeline.analyze   import analyze
from pipeline.agent     import ask_agent
from pipeline.validate  import cross_check_numbers


app = Flask(__name__, static_folder='static')


# ── Static file routes ────────────────────────────────────────
# Serves index.html, styles.css, main.js, prompts.json.
# No changes needed here.

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


# ── POST /upload ───────────────────────────────────────────────
# The single API endpoint the frontend calls.
#
# What comes in (multipart/form-data):
#   file   — .xlsx, .xls, or .csv
#   prompt — user's question or canned prompt text
#   mode   — analysis mode slug (e.g. "portfolio-summary")
#             Empty string if no canned button is selected
#
# What goes back (JSON):
#   {
#     "narrative"    : str   — LLM-generated analysis text
#     "metrics"      : dict  — tile data for the Data Snapshot panel
#     "claims"       : list  — structured citations from the narrative
#                              Each item: { sentence, source_field, cited_value }
#                              Empty list if structured output wasn't available
#     "context_sent" : str   — the exact data string the LLM received
#                              Rendered in the "View source data" expandable panel
#     "verification" : dict  — number cross-check results
#                              { total, verified_count, unverified_count,
#                                unverified: [], all_clear: bool }
#   }

@app.route('/upload', methods=['POST'])
def upload():

    # ── Step 1: Extract inputs ─────────────────────────────────
    file   = request.files.get('file')
    prompt = request.form.get('prompt', '').strip()
    mode   = request.form.get('mode', '').strip()

    # ── Step 2: Validate inputs ────────────────────────────────
    if not file:
        return jsonify({"error": "No file uploaded."}), 400
    if not prompt:
        return jsonify({"error": "No prompt provided."}), 400

    # ── Step 3: Deterministic analysis ────────────────────────
    # analyze() routes to the correct processor script based on mode.
    # Returns: { "context": str, "metrics": dict }
    #
    # context = the data payload the LLM will analyze
    #           (deterministic_narrative_payload once your real
    #           processor scripts are registered in SCRIPT_MAP)
    # metrics = tile data for the Data Snapshot panel
    try:
        result = analyze(file, mode)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500

    context = result.get("context", "")

    # ── Step 4: LLM narration ─────────────────────────────────
    # ask_agent() builds a LangChain chain, calls Azure OpenAI,
    # and returns { "narrative": str, "claims": list }.
    #
    # Tries structured output first (returns narrative + claims).
    # Falls back to plain text (returns narrative, claims=[]).
    # See pipeline/agent.py for details.
    try:
        agent_response = ask_agent(
            context=context,
            user_prompt=prompt,
            mode=mode,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"LLM call failed: {str(e)}"}), 500

    narrative = agent_response.get("narrative", "")
    claims    = agent_response.get("claims", [])

    # ── Step 5: Number cross-check ────────────────────────────
    # Extracts numeric tokens from the narrative and checks whether
    # each appears in the context string.
    # "Unverified" = not in source data (may be calculated or inferred).
    # See pipeline/validate.py for details.
    verification = cross_check_numbers(narrative, context)

    # ── Step 6: Return full response ──────────────────────────
    # All four fields are optional from the frontend's perspective —
    # main.js checks each with optional chaining before using them.
    return jsonify({
        "narrative":    narrative,
        "metrics":      result.get("metrics", {}),
        "claims":       claims,
        "context_sent": context,
        "verification": verification,
    })


if __name__ == '__main__':
    app.run(port=8888, debug=True)
