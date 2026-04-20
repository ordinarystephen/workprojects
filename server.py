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

import base64
import io
import time
import traceback

from flask import Flask, send_from_directory, request, jsonify

from pipeline.analyze   import analyze
from pipeline.agent     import ask_agent
from pipeline.validate  import cross_check_numbers
from pipeline.tracking  import activate_mlflow, mlflow_run


# ── JSON-upload adapter ───────────────────────────────────────
# The Domino workspace proxy drops multipart/form-data POSTs, so the
# frontend sends the file inside a JSON body as base64. This tiny
# adapter exposes the same interface our processors expect from a
# Flask file object: .read(), .filename, .content_length.
class _Base64File:
    def __init__(self, data: bytes, name: str = ""):
        self._io = io.BytesIO(data)
        self.filename = name
        self.content_length = len(data)

    def read(self, *args, **kwargs):
        return self._io.read(*args, **kwargs)


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
    # Two transports supported:
    #   • application/json  — { file_name, file_b64, prompt, mode }
    #                         (Domino workspace proxy drops multipart,
    #                          so this is the default path from the UI)
    #   • multipart/form-data — file + prompt + mode as form fields
    #                         (kept for curl / local dev / future App deploys)
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        file_b64_str = payload.get('file_b64') or ''
        prompt    = (payload.get('prompt') or '').strip()
        mode      = (payload.get('mode') or '').strip()
        file_name = (payload.get('file_name') or '').strip()

        if not file_b64_str:
            return jsonify({"error": "No file uploaded."}), 400
        try:
            file_bytes = base64.b64decode(file_b64_str)
        except Exception:
            return jsonify({"error": "File payload is not valid base64."}), 400
        file = _Base64File(file_bytes, file_name)
        file_size = len(file_bytes)
    else:
        file   = request.files.get('file')
        prompt = request.form.get('prompt', '').strip()
        mode   = request.form.get('mode', '').strip()

        if not file:
            return jsonify({"error": "No file uploaded."}), 400
        file_name = file.filename or ""
        try:
            file_size = (
                file.content_length
                or (request.content_length or 0)
            )
        except Exception:
            file_size = 0

    # ── Step 2: Validate prompt ────────────────────────────────
    if not prompt:
        return jsonify({"error": "No prompt provided."}), 400

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
        t_analyze_start = time.perf_counter()
        try:
            result = analyze(file, mode)
        except Exception as e:
            traceback.print_exc()
            run.log("error_stage", "analyze")
            return jsonify({"error": f"Analysis failed: {str(e)}"}), 500
        t_analyze_ms = int((time.perf_counter() - t_analyze_start) * 1000)

        context = result.get("context", "")
        run.log("context_length", len(context))
        run.log("analyze_ms", t_analyze_ms)

        # ── Step 4: LLM narration ─────────────────────────────
        # ask_agent() invokes the compiled LangGraph, which calls
        # Azure OpenAI with structured output. When MLflow is active,
        # mlflow.langchain.autolog() captures the chain trace
        # automatically — no explicit logging needed here.
        t_llm_start = time.perf_counter()
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
        t_llm_ms = int((time.perf_counter() - t_llm_start) * 1000)

        narrative = agent_response.get("narrative", "")
        claims    = agent_response.get("claims", [])

        run.log("narrative_length", len(narrative))
        run.log("claims_count", len(claims))
        run.log("llm_ms", t_llm_ms)

        # ── Step 5: Number cross-check ────────────────────────
        # Extracts numeric tokens from the narrative and checks whether
        # each appears in the context string. Unverified = not in
        # source data (may be calculated). See pipeline/validate.py.
        t_verify_start = time.perf_counter()
        verification = cross_check_numbers(narrative, context)
        t_verify_ms = int((time.perf_counter() - t_verify_start) * 1000)

        run.log("verified_count", verification.get("verified_count", 0))
        run.log("unverified_count", verification.get("unverified_count", 0))
        run.log("verify_ms", t_verify_ms)

        # Log the context sent to the LLM as an artifact — critical
        # for audit / post-hoc review of what the model saw.
        if context:
            run.log_artifact_text(context, "context_sent.txt")

        # ── Step 6: Return full response ──────────────────────
        # timings_ms reports real server-side stage durations so the
        # frontend can replace its fake loading-animation delays with
        # honest numbers. See static/main.js runAnalysis().
        return jsonify({
            "narrative":    narrative,
            "metrics":      result.get("metrics", {}),
            "claims":       claims,
            "context_sent": context,
            "verification": verification,
            "timings_ms": {
                "analyze": t_analyze_ms,
                "llm":     t_llm_ms,
                "verify":  t_verify_ms,
            },
        })


if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
