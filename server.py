# ── KRONOS · server.py ────────────────────────────────────────
# Flask application entry point.
#
# This file orchestrates the full request lifecycle:
#   file upload → analyze() → ask_agent() → verify_claims() → JSON
#
# MLflow tracking wraps the /upload handler via tracking.mlflow_run().
# When KRONOS_MLFLOW_ENABLED=true, every request is logged as an
# MLflow run with tags, params, metrics, and auto-captured traces.
# When unset (default), the tracking wrapper is a zero-cost no-op.
#
# Mode definitions, prompt templates, and parameter schemas live in
# config/modes.yaml + config/prompts/*.md — loaded once at import via
# load_registry(). A malformed registry crashes the app at boot.
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
import os
import time
import traceback

from flask import Flask, send_from_directory, request, jsonify

from pipeline.analyze   import analyze, ModeNotImplementedError
from pipeline.agent     import ask_agent
from pipeline.cube.lending import compute_lending_cube
from pipeline.error_log import log_error, read_recent
from pipeline.loaders.classifier import classify
from pipeline.registry  import (
    ParameterError,
    RegistryError,
    get_mode,
    list_modes_for_ui,
    load_registry,
    resolve_parameter_options,
    validate_parameters,
)
from pipeline.tracking  import activate_mlflow, mlflow_run
from pipeline.validate  import verify_claims


# ── Session ID header ─────────────────────────────────────────
# Frontend generates a per-tab UUID (sessionStorage) and sends it
# in X-Kronos-Session. Used purely to correlate error records — no
# auth, no server-side state.
_SESSION_HEADER = "X-Kronos-Session"


def _session_id() -> str:
    return (request.headers.get(_SESSION_HEADER) or "").strip()[:64]


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


# ── Boot-time setup ───────────────────────────────────────────
# Both calls happen ONCE at import. load_registry() raises
# RegistryError on any malformed YAML / missing slicer / missing
# prompt — we let it propagate so a misconfigured app fails to
# start instead of failing per-request.
try:
    load_registry()
except RegistryError as e:
    # Re-raise with a friendlier banner. The original message is preserved.
    raise RuntimeError(
        "KRONOS failed to start: mode registry is invalid.\n"
        f"  {e}\n"
        "Fix config/modes.yaml or the corresponding slicer / prompt file, "
        "then restart."
    ) from e

activate_mlflow()


# ── Static file routes ────────────────────────────────────────
# Serves index.html, styles.css, main.js.
# (prompts.json removed — frontend now fetches /modes at runtime.)

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


# ══════════════════════════════════════════════════════════════
# ── GET /modes ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Returns mode definitions (active + placeholder, in YAML order)
# for the canned-prompt grid and the parameter-picker UI.
#
# Stateless — does NOT resolve `source: cube.<field>` enum
# parameters. The frontend calls POST /cube/parameter-options
# after a file is selected to populate runtime-driven enums.
#
# Response shape:
#   {
#     "modes": [
#       {
#         "slug":           "firm-level",
#         "display_name":   "Firm-Level View",
#         "description":    "...",
#         "user_prompt":    "Provide a firm-level...",
#         "parameters":     [],
#         "status":         "active"
#       },
#       {
#         "slug":           "portfolio-level",
#         "display_name":   "Portfolio-Level Analysis",
#         ...
#         "parameters":     [
#           { "name": "portfolio", "type": "enum",
#             "source": "cube.available_portfolios",
#             "required": true, "display_label": "Portfolio" }
#         ],
#         "status":         "placeholder"
#       }
#     ]
#   }

@app.route('/modes', methods=['GET'])
def list_modes():
    modes = list_modes_for_ui()
    return jsonify({
        "modes": [
            {
                "slug":         m.slug,
                "display_name": m.display_name,
                "description":  m.description.strip(),
                "user_prompt":  m.user_prompt.strip(),
                "parameters":   [p.model_dump(exclude_none=True) for p in m.parameters],
                "status":       m.status,
            }
            for m in modes
        ]
    })


# ══════════════════════════════════════════════════════════════
# ── POST /cube/parameter-options ──────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Resolves enum parameters whose `source: cube.<field>` references
# runtime data from the uploaded workbook.
#
# Request shape (mirrors /upload's JSON body for the file):
#   { "slug": "portfolio-level",
#     "file_name": "...", "file_b64": "..." }
#
# Response shape:
#   {
#     "slug": "portfolio-level",
#     "options": {
#       "portfolio": ["Energy", "Manufacturing", "Technology", ...]
#     }
#   }
#
# Frontend flow:
#   1. User picks a parameterized mode button
#   2. User attaches file
#   3. Frontend POSTs file + slug here, gets the resolved option list
#   4. UI renders <select> with those options
#   5. User confirms → /upload with { slug, parameters }

@app.route('/cube/parameter-options', methods=['POST'])
def cube_parameter_options():
    payload = request.get_json(silent=True) or {}
    slug = (payload.get('slug') or '').strip()
    if not slug:
        return jsonify({"error": "slug is required"}), 400

    mode_def = get_mode(slug)
    if mode_def is None:
        return jsonify({"error": f"Unknown mode '{slug}'"}), 404

    # If there are no enum-from-cube parameters, no work to do — return empty.
    if not any(p.source for p in mode_def.parameters):
        return jsonify({"slug": slug, "options": {}})

    file_b64_str = payload.get('file_b64') or ''
    if not file_b64_str:
        return jsonify({"error": "file_b64 is required for parameterized modes"}), 400

    try:
        file_bytes = base64.b64decode(file_b64_str)
    except Exception as e:
        log_error(
            "upload_parse_failed",
            error=e,
            mode=slug,
            session_id=_session_id(),
            endpoint="/cube/parameter-options",
            stage="base64_decode",
        )
        return jsonify({"error": "file_b64 is not valid base64"}), 400

    file_obj = _Base64File(file_bytes, payload.get('file_name', ''))

    try:
        classified = classify(file_obj)
    except Exception as e:
        traceback.print_exc()
        log_error(
            "classification_failed",
            error=e,
            mode=slug,
            session_id=_session_id(),
            endpoint="/cube/parameter-options",
            file_name=payload.get('file_name', ''),
        )
        return jsonify({"error": f"Classification failed: {e}"}), 400

    if "lending" not in classified["classified"]:
        seen = [s["name"] for s in classified["metadata"]["sheets_seen"]]
        log_error(
            "classification_failed",
            mode=slug,
            session_id=_session_id(),
            endpoint="/cube/parameter-options",
            reason="no_lending_sheet",
            sheets_seen=seen,
        )
        return jsonify({
            "error": f"Mode '{slug}' requires a lending sheet. Sheets seen: {seen}"
        }), 400

    cube = compute_lending_cube(classified["classified"]["lending"])
    try:
        options = resolve_parameter_options(mode_def, cube)
    except ParameterError as e:
        log_error(
            "cube_parameter_options_failed",
            error=e,
            mode=slug,
            session_id=_session_id(),
        )
        return jsonify({"error": str(e)}), 400

    return jsonify({"slug": slug, "options": options})


# ══════════════════════════════════════════════════════════════
# ── POST /upload ──────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# What comes in (JSON or multipart):
#   file       — .xlsx, .xls, or .csv
#   prompt     — user's question or canned prompt text
#   mode       — analysis mode slug (e.g. "portfolio-summary")
#                Empty string if no canned button is selected
#   parameters — dict of mode-specific parameters (optional;
#                required for parameterized modes like portfolio-level)
#
# What goes back (JSON):
#   {
#     "narrative"    : str   — LLM-generated analysis text
#     "metrics"      : dict  — tile data for the Data Snapshot panel
#     "claims"       : list  — structured citations from the narrative
#     "context_sent" : str   — the exact data string the LLM received
#     "verification" : dict  — claim-based verification result
#                              { total, verified_count, unverified_count,
#                                mismatch_count, all_clear: bool,
#                                claim_results: [...], notes: [...] }
#     "timings_ms"   : dict  — analyze / llm / verify stage durations
#   }

@app.route('/upload', methods=['POST'])
def upload():

    # ── Step 1: Extract inputs ─────────────────────────────────
    # prior_narrative is optional and only set on follow-ups. When present,
    # the LLM receives it as a prior AI turn so it knows what it "said" in
    # the previous round of the conversation. The slicer still re-runs
    # against the same file + mode + parameters so verifiable_values
    # matches the fresh context the LLM sees.
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        file_b64_str = payload.get('file_b64') or ''
        prompt    = (payload.get('prompt') or '').strip()
        mode      = (payload.get('mode') or '').strip()
        file_name = (payload.get('file_name') or '').strip()
        parameters = payload.get('parameters') or {}
        prior_narrative = (payload.get('prior_narrative') or '').strip()

        if not file_b64_str:
            return jsonify({"error": "No file uploaded."}), 400
        try:
            file_bytes = base64.b64decode(file_b64_str)
        except Exception as e:
            log_error(
                "upload_parse_failed",
                error=e,
                mode=mode,
                session_id=_session_id(),
                stage="base64_decode",
                transport="json",
            )
            return jsonify({"error": "File payload is not valid base64."}), 400
        file = _Base64File(file_bytes, file_name)
        file_size = len(file_bytes)
    else:
        file   = request.files.get('file')
        prompt = request.form.get('prompt', '').strip()
        mode   = request.form.get('mode', '').strip()
        prior_narrative = request.form.get('prior_narrative', '').strip()
        # Multipart parameters arrive as a JSON-encoded string in a
        # single form field — the form-data spec doesn't support
        # nested objects natively.
        parameters_raw = request.form.get('parameters', '').strip()
        try:
            import json as _json
            parameters = _json.loads(parameters_raw) if parameters_raw else {}
        except Exception as e:
            log_error(
                "upload_parse_failed",
                error=e,
                mode=mode,
                session_id=_session_id(),
                stage="parameters_json",
                transport="multipart",
            )
            return jsonify({"error": "parameters form field is not valid JSON."}), 400

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

    if not isinstance(parameters, dict):
        return jsonify({"error": "parameters must be an object."}), 400

    # ── Step 2: Validate prompt ────────────────────────────────
    if not prompt:
        return jsonify({"error": "No prompt provided."}), 400

    # ── Step 3: Pre-validate parameters (cube-agnostic) ────────
    # Catches missing required / unknown parameter keys before we
    # bother classifying the workbook. Cube-aware membership check
    # happens inside analyze() once the cube is built.
    if mode:
        mode_def = get_mode(mode)
        if mode_def is not None:
            try:
                validate_parameters(mode_def, parameters, cube=None)
            except ParameterError as e:
                log_error(
                    "parameter_validation_failed",
                    error=e,
                    mode=mode,
                    parameters=parameters,
                    session_id=_session_id(),
                    stage="pre_validate",
                )
                return jsonify({"error": str(e)}), 400

    # ── MLflow run wrapper ─────────────────────────────────────
    with mlflow_run(
        mode=mode,
        file_name=file_name,
        file_size=file_size,
        user_prompt=prompt,
    ) as run:

        # ── Step 4: Deterministic analysis ────────────────────
        # analyze() runs classification + cube compute + slicer. Errors
        # are broken out by type so the JSONL trail can distinguish
        # classification / validation / slicer failures later.
        t_analyze_start = time.perf_counter()
        sess = _session_id()
        try:
            result = analyze(file, mode, parameters)
        except ModeNotImplementedError as e:
            run.log("error_stage", "mode_not_implemented")
            log_error(
                "mode_not_implemented",
                error=e,
                mode=mode,
                parameters=parameters,
                session_id=sess,
                slug=e.slug,
            )
            return jsonify({
                "error": str(e),
                "code":  "mode_not_implemented",
                "slug":  e.slug,
            }), 501
        except ParameterError as e:
            run.log("error_stage", "parameter_validation")
            log_error(
                "parameter_validation_failed",
                error=e,
                mode=mode,
                parameters=parameters,
                session_id=sess,
                stage="cube_aware",
            )
            return jsonify({"error": str(e)}), 400
        except ValueError as e:
            # ValueError from analyze() is overwhelmingly classification
            # (missing template sheet, bad signature). Keep it separate
            # from generic slicer exceptions in the event stream.
            traceback.print_exc()
            run.log("error_stage", "classification")
            log_error(
                "classification_failed",
                error=e,
                mode=mode,
                parameters=parameters,
                session_id=sess,
                file_name=file_name,
                file_size=file_size,
            )
            return jsonify({"error": f"Analysis failed: {str(e)}"}), 500
        except Exception as e:
            traceback.print_exc()
            run.log("error_stage", "analyze")
            log_error(
                "slicer_failed",
                error=e,
                mode=mode,
                parameters=parameters,
                session_id=sess,
                file_name=file_name,
                file_size=file_size,
                user_prompt=prompt,
            )
            return jsonify({"error": f"Analysis failed: {str(e)}"}), 500
        t_analyze_ms = int((time.perf_counter() - t_analyze_start) * 1000)

        context           = result.get("context", "")
        verifiable_values = result.get("verifiable_values", {}) or {}
        run.log("context_length", len(context))
        run.log("verifiable_count", len(verifiable_values))
        run.log("analyze_ms", t_analyze_ms)

        # ── Step 5: LLM narration ─────────────────────────────
        t_llm_start = time.perf_counter()
        try:
            agent_response = ask_agent(
                context=context,
                user_prompt=prompt,
                mode=mode,
                parameters=parameters,
                prior_narrative=prior_narrative,
            )
        except Exception as e:
            traceback.print_exc()
            run.log("error_stage", "ask_agent")
            log_error(
                "llm_failed",
                error=e,
                mode=mode,
                parameters=parameters,
                session_id=sess,
                user_prompt=prompt,
                context_snippet=context,
                had_prior_narrative=bool(prior_narrative),
            )
            return jsonify({"error": f"LLM call failed: {str(e)}"}), 500
        t_llm_ms = int((time.perf_counter() - t_llm_start) * 1000)

        narrative = agent_response.get("narrative", "")
        claims    = agent_response.get("claims", [])

        run.log("narrative_length", len(narrative))
        run.log("claims_count", len(claims))
        run.log("llm_ms", t_llm_ms)

        # ── Step 6: Claim-based verification ──────────────────
        t_verify_start = time.perf_counter()
        verification_model = verify_claims(claims, verifiable_values)
        verification = verification_model.model_dump()
        t_verify_ms = int((time.perf_counter() - t_verify_start) * 1000)

        run.log("verified_count",    verification.get("verified_count",   0))
        run.log("unverified_count",  verification.get("unverified_count", 0))
        run.log("mismatch_count",    verification.get("mismatch_count",   0))
        run.log("verify_ms", t_verify_ms)

        # Verification MISMATCH — the LLM cited a known field but the wrong
        # value. This is the dangerous case worth logging (unverified /
        # field_not_found is transparency, not an error). We capture the
        # offending claims so triage can correlate with the narrative.
        mismatch_count = int(verification.get("mismatch_count", 0) or 0)
        if mismatch_count > 0:
            mismatched = [
                r for r in verification.get("claim_results", [])
                if r.get("status") == "mismatch"
            ]
            log_error(
                "verification_mismatch",
                mode=mode,
                parameters=parameters,
                session_id=sess,
                user_prompt=prompt,
                mismatch_count=mismatch_count,
                total_claims=verification.get("total", 0),
                mismatched_claims=mismatched[:10],
            )

        if context:
            run.log_artifact_text(context, "context_sent.txt")

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


# ══════════════════════════════════════════════════════════════
# ── GET /errors/recent ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Read-only tail of the local JSONL error log. Gated behind
# KRONOS_ERRORS_ENDPOINT_ENABLED so it is never exposed by default —
# the endpoint reveals stack traces and upload metadata, which belongs
# on triage surfaces only.
#
# Not an auth boundary — this is a kill switch. Any real deployment
# should sit behind the Domino proxy's auth. We return 404 (not 403)
# when disabled so the endpoint is indistinguishable from a missing
# route.
#
# MLflow-backed aggregation (error counts by mode, trend charts, etc.)
# is a separate future feature — out of scope here.

def _errors_endpoint_enabled() -> bool:
    return (os.getenv("KRONOS_ERRORS_ENDPOINT_ENABLED") or "").strip().lower() in (
        "true", "1", "yes",
    )


@app.route('/errors/recent', methods=['GET'])
def errors_recent():
    if not _errors_endpoint_enabled():
        return jsonify({"error": "Not found."}), 404

    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50

    return jsonify({"records": read_recent(limit)})


if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
