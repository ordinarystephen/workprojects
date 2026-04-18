# ── KRONOS · server.py ────────────────────────────────────────
# Flask application entry point. Replaces Streamlit's main.py.
# Serves the static frontend and hosts the /upload API route.
#
# ── HOW THIS MAPS TO YOUR EXISTING CODEBASE ──────────────────
# In your existing app, main.py:
#   - Receives the file upload and user instructions via Streamlit UI
#   - Calls ask_workbook_agent() from agent_client.py
#   - Stores the response in Streamlit session state
#   - Renders the UI
#
# In KRONOS, server.py takes over that orchestration role:
#   - Receives the file + prompt + mode via POST /upload (not Streamlit)
#   - Calls analyze(file, mode)     ← processor.py (your deterministic layer)
#   - Calls ask_workbook_agent()    ← agent_client.py (your LLM layer)
#   - Returns JSON to the frontend  ← replaces Streamlit session state + UI render
#
# The frontend (main.js) handles all UI rendering from the JSON response.
# No session state needed — the browser holds state between turns.
# ──────────────────────────────────────────────────────────────

from flask import Flask, send_from_directory, request, jsonify

# ── TODO (merge): Add these imports when wiring the pipeline ──
#
# from pipeline.analyze import analyze
#   └─ analyze.py is the dispatcher. Rename to processor.py or point
#      directly at your existing processor, then update this import.
#
# from your_path.agent_client import ask_workbook_agent
#   └─ ask_workbook_agent() is the function in agent_client.py that:
#        - turns analysis_result into prompt payloads (via prompts.py)
#        - builds custom_inputs and the final payload
#        - calls workbook_agent.py which calls Azure OpenAI via AzureChatOpenAI
#        - normalizes the response and returns a structured result
#      Update the import path to match where agent_client.py lives
#      in your merged codebase.
#
# import os  ← needed for Azure env vars if not already handled in agent_client.py

app = Flask(__name__, static_folder='static')


# ── Static file serving ───────────────────────────────────────
# Serves index.html, styles.css, main.js, prompts.json.
# No changes needed here after merge.

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


# ── TODO (merge): Implement /upload ───────────────────────────
# Uncomment and fill in once processor.py and agent_client.py are imported.
#
# What comes in from the frontend (multipart/form-data):
#   file   → the uploaded .xlsx / .xls / .csv (replaces Streamlit file uploader)
#   prompt → the user's question string       (replaces Streamlit instruction input)
#   mode   → slug identifying which processor script to run
#             e.g. "lending-risk", "traded-products", "portfolio-sampling"
#             Empty string if user typed a custom question with no button selected.
#
# What must go back to the frontend (JSON):
#   {
#     "narrative": "string",    ← normalized response text from ask_workbook_agent()
#     "metrics":   { ... }      ← structured part of analysis_result, formatted as
#                                  tile schema (see pipeline/analyze.py for shape)
#                                  Optional — omit key if not available for this mode.
#   }
#
# Wiring steps:
#   1. file   = request.files['file']
#   2. prompt = request.form.get('prompt', '')
#   3. mode   = request.form.get('mode', '')
#
#   4. result = analyze(file, mode)
#      └─ processor.py runs deterministic analysis
#         builds deterministic_narrative_payload  ← NEW primary LLM payload
#           scoped to user ask, smaller than raw_results,
#           richer and more faithful than commentary_facts
#         keeps commentary_facts for backward compat (no longer primary)
#         returns { context: deterministic_narrative_payload, metrics: ... }
#
#   5. agent_response = ask_workbook_agent(result['context'], prompt)
#      └─ agent_client.py builds prompt payloads (via prompts.py)
#         builds custom_inputs and final payload
#         workbook_agent.py prepends system prompt, calls Azure OpenAI
#         agent_client.py normalizes response and returns structured result
#
#   6. return jsonify({
#          "narrative": agent_response['narrative'],  ← or however your agent returns text
#          "metrics":   result['metrics']
#      })
#
# NOTE on follow-up turns:
#   The frontend sends the same file again on every follow-up question.
#   mode will be empty on follow-ups (no canned button is active).
#   You may want to skip the deterministic analysis step and call
#   ask_workbook_agent directly with the prior context + new question,
#   depending on how your agent handles conversation history.
#
# @app.route('/upload', methods=['POST'])
# def upload():
#     file   = request.files['file']
#     prompt = request.form.get('prompt', '')
#     mode   = request.form.get('mode', '')
#     result         = analyze(file, mode)
#     agent_response = ask_workbook_agent(result['context'], prompt)
#     return jsonify({
#         "narrative": agent_response['narrative'],
#         "metrics":   result['metrics']
#     })


if __name__ == '__main__':
    app.run(port=8888, debug=True)
