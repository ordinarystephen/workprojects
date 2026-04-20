# ── KRONOS · server.py ────────────────────────────────────────
# Flask application entry point.
#
# This file orchestrates the full request lifecycle:
#   file upload → analyze() → ask_agent() → cross_check_numbers() → JSON
#
# MLflow tracking wraps the /upload handler via tracking.mlflow_run().
# When KRONOS_MLFLOW_ENABLED=true, every request is logged as an
# MLflow run with tags, params, metrics, and auto-captured traces.
# When unset (default), the tracking wrapper is a zero-cost no-op.
#
# ── Environment variables ──────────────────────────────────────
# Required for LLM calls:
#   AZURE_OPENAI_DEPLOYMENT  = gpt-4o
#   OPENAI_API_VERSION       = 2025-04-01-preview
#   AZURE_OPENAI_ENDPOINT    = https://your-resource.openai.azure.com/
#                              (optional on Domino — proxy handles it)
#
# Optional — production tracking (off by default):
#   KRONOS_MLFLOW_ENABLED    = true            # flip to enable
#   MLFLOW_EXPERIMENT_NAME   = kronos-dev      # or kronos-prod
# ──────────────────────────────────────────────────────────────

import traceback

from flask import Flask, send_from_directory, request, jsonify

from pipeline.analyze   import analyze
from pipeline.agent     import ask_agent
from pipeline.validate  import cross_check_numbers
from pipeline.tracking  import activate_mlflow, mlflow_run


app = Flask(__name__, static_folder='static')


# ── MLflow activation ─────────────────────────────────────────
# Called ONCE at app import. Gated by KRONOS_MLFLOW_ENABLED.
# No-op when disabled — safe to leave in for all environments.
# See pipeline/tracking.py for full activation logic.
activate_mlflow()


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

    # File metadata for MLflow tagging. content_length may be None
    # when the stream has been consumed — we read it here before
    # analyze() consumes the stream.
    file_name = file.filename or ""
    try:
        file_size = (
            file.content_length
            or (request.content_length or 0)
        )
    except Exception:
        file_size = 0

    # ── MLflow run wrapper ─────────────────────────────────────
    # When enabled: starts an MLflow run for this request, logs
    # tags + params upfront, yields a handle for metric/artifact
    # logging inside the with-block, ends the run on exit.
    # When disabled: yields a no-op handle. Zero overhead.
    with mlflow_run(
        mode=mode,
        file_name=file_name,
        file_size=file_size,
        user_prompt=prompt,
    ) as run:

        # ── Step 3: Deterministic analysis ────────────────────
        # analyze() routes to the correct processor script based on mode.
        # Returns: { "context": str, "metrics": dict }
        try:
            result = analyze(file, mode)
        except Exception as e:
            traceback.print_exc()
            run.log("error_stage", "analyze")
            return jsonify({"error": f"Analysis failed: {str(e)}"}), 500

        context = result.get("context", "")
        run.log("context_length", len(context))

        # ── Step 4: LLM narration ─────────────────────────────
        # ask_agent() invokes the compiled LangGraph, which calls
        # Azure OpenAI with structured output. When MLflow is active,
        # mlflow.langchain.autolog() captures the chain trace
        # automatically — no explicit logging needed here.
        try:
            agent_response = ask_agent(
                context=context,
                user_prompt=prompt,
                mode=mode,
            )
        except Exception as e:
            traceback.print_exc()
            run.log("error_stage", "ask_agent")
            return jsonify({"error": f"LLM call failed: {str(e)}"}), 500

        narrative = agent_response.get("narrative", "")
        claims    = agent_response.get("claims", [])

        run.log("narrative_length", len(narrative))
        run.log("claims_count", len(claims))

        # ── Step 5: Number cross-check ────────────────────────
        # Extracts numeric tokens from the narrative and checks whether
        # each appears in the context string. Unverified = not in
        # source data (may be calculated). See pipeline/validate.py.
        verification = cross_check_numbers(narrative, context)

        run.log("verified_count", verification.get("verified_count", 0))
        run.log("unverified_count", verification.get("unverified_count", 0))

        # Log the context sent to the LLM as an artifact — critical
        # for audit / post-hoc review of what the model saw.
        if context:
            run.log_artifact_text(context, "context_sent.txt")

        # ── Step 6: Return full response ──────────────────────
        return jsonify({
            "narrative":    narrative,
            "metrics":      result.get("metrics", {}),
            "claims":       claims,
            "context_sent": context,
            "verification": verification,
        })


if __name__ == '__main__':
    app.run(port=8888, debug=True)
