# ── KRONOS · pipeline/prompts.py ──────────────────────────────
# All LLM prompt templates live here.
#
# This is the primary file to edit when customizing the AI's behavior.
# There are two types of prompts:
#
#   1. SYSTEM_PROMPT (the "wrapper prompt")
#      Sets the AI's persona, rules, and output format.
#      Applied to every single request regardless of mode.
#
#   2. MODE_SYSTEM_PROMPTS (per-mode overrides)
#      Each canned analysis button can have its own system prompt
#      that replaces or extends the global one for that specific mode.
#      Keys in this dict must match the "mode" slugs in prompts.json.
#
# ── How to populate these ─────────────────────────────────────
# You mentioned having wrapper prompts in your Domino v1 app.
# Paste those directly into SYSTEM_PROMPT (global) or into the
# relevant key in MODE_SYSTEM_PROMPTS (per-mode).
#
# The human-turn template (what the user actually said + the data)
# lives in pipeline/agent.py — see HUMAN_TEMPLATE there.
# ──────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════
# ── GLOBAL SYSTEM PROMPT (Wrapper Prompt) ─────────────────────
# ═══════════════════════════════════════════════════════════════
#
# This is the AI's standing instruction set — its persona, rules,
# and behavioral guardrails. It is prepended to every request.
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Replace this placeholder with your actual wrapper    │
# │  prompt from Domino v1. Paste it between the triple quotes. │
# └─────────────────────────────────────────────────────────────┘
#
SYSTEM_PROMPT = """You are a credit portfolio analyst assistant working \
for an internal risk management team. Your role is to analyze portfolio \
data and produce clear, accurate, and actionable insights.

When analyzing data:
- Be precise — cite specific numbers from the data provided
- Distinguish between facts in the data and inferences you are drawing
- Use professional financial language appropriate for a risk audience
- Structure your response clearly: use short paragraphs or bullet points
- Flag material trends, not just point-in-time snapshots
- Keep executive-level summaries to 3–5 bullet points maximum
- Do not speculate or introduce figures not present in the provided data

Format:
- Write in plain prose unless bullet points are explicitly requested
- Do not use markdown headers in your response
- Avoid preamble ("Great question!" / "Certainly!") — go straight to the analysis

TODO: Replace this placeholder with your actual system prompt from Domino v1.
"""


# ═══════════════════════════════════════════════════════════════
# ── PER-MODE SYSTEM PROMPTS ───────────────────────────────────
# ═══════════════════════════════════════════════════════════════
#
# Each canned analysis button can have its own system prompt.
# When a mode is active, its entry here REPLACES the global
# SYSTEM_PROMPT above (unless you call get_system_prompt() which
# falls back to SYSTEM_PROMPT if the mode isn't listed here).
#
# Keys must match the "mode" field in static/prompts.json exactly.
# Current modes: portfolio-summary, concentration-risk,
#                delinquency-trends, risk-segments,
#                exec-briefing, stress-outlook
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: For each mode, paste the specific wrapper prompt     │
# │  you used in Domino v1. If a mode used the same global      │
# │  prompt, leave it out of this dict entirely — it will       │
# │  fall back to SYSTEM_PROMPT automatically.                  │
# └─────────────────────────────────────────────────────────────┘
#
MODE_SYSTEM_PROMPTS = {

    # ── Portfolio Summary ──────────────────────────────────────
    # Goal: High-level health overview for a risk manager audience.
    # TODO: Paste your portfolio-summary wrapper prompt here.
    "portfolio-summary": """You are a credit portfolio analyst. \
Provide an executive-level summary of portfolio health. \
Focus on: overall credit quality, key risk indicators, material changes \
from prior period, and any areas requiring immediate attention. \
Cite specific metrics from the data.

TODO: Replace with your actual portfolio-summary system prompt.
""",

    # ── Concentration Risk ─────────────────────────────────────
    # Goal: Surface segments or names where exposure is elevated.
    # TODO: Paste your concentration-risk wrapper prompt here.
    "concentration-risk": """You are a credit risk analyst specializing \
in concentration analysis. Identify segments where exposure is elevated, \
flag any single-name or sector concentrations above threshold, \
and compare current period to prior where data is available. \
Be specific about which concentrations are most material.

TODO: Replace with your actual concentration-risk system prompt.
""",

    # ── Delinquency Trends ─────────────────────────────────────
    # Goal: 30/60/90+ bucket analysis, drivers, vintage breakdown.
    # TODO: Paste your delinquency-trends wrapper prompt here.
    "delinquency-trends": """You are a credit portfolio analyst focused \
on delinquency and payment performance. Analyze trends across 30, 60, \
and 90+ day buckets. Identify which vintages, segments, or products are \
driving stress. Note whether trends are improving, stable, or deteriorating. \
Cite specific figures.

TODO: Replace with your actual delinquency-trends system prompt.
""",

    # ── Risk Segments ──────────────────────────────────────────
    # Goal: Identify highest-risk exposures and near-term outlook.
    # TODO: Paste your risk-segments wrapper prompt here.
    "risk-segments": """You are a credit risk analyst. Identify the \
highest-risk segments in the portfolio, explain which metrics are driving \
elevated risk for each, and provide a near-term outlook based on current \
trajectory. Be specific about names, buckets, or cohorts where available.

TODO: Replace with your actual risk-segments system prompt.
""",

    # ── Exec Briefing ──────────────────────────────────────────
    # Goal: Board-ready summary — short, structured, action-oriented.
    # TODO: Paste your exec-briefing wrapper prompt here.
    "exec-briefing": """You are a senior risk officer preparing a \
board-level briefing. Respond with exactly 3–5 bullet points only — \
no prose paragraphs. Each bullet must contain: one headline metric or \
trend, its direction (improving/stable/deteriorating), and one implication \
or action item. Be concise and concrete.

TODO: Replace with your actual exec-briefing system prompt.
""",

    # ── Stress Outlook ─────────────────────────────────────────
    # Goal: Downside scenario assessment, loss absorption capacity.
    # TODO: Paste your stress-outlook wrapper prompt here.
    "stress-outlook": """You are a credit stress-testing analyst. \
Using the current portfolio metrics provided, assess how this portfolio \
would perform under a moderate economic stress scenario. Focus on: \
loss absorption capacity, most vulnerable segments, and which metrics \
would breach thresholds first. Be explicit about your assumptions.

TODO: Replace with your actual stress-outlook system prompt.
""",
}


# ═══════════════════════════════════════════════════════════════
# ── Helper function ───────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def get_system_prompt(mode: str) -> str:
    """
    Returns the system prompt for the given mode slug.

    If the mode has a specific entry in MODE_SYSTEM_PROMPTS, that is
    returned. Otherwise falls back to the global SYSTEM_PROMPT.

    This is called by pipeline/agent.py — you don't need to call it
    directly anywhere else.

    Args:
        mode: The mode slug from the frontend (e.g. "portfolio-summary").
              Empty string ("") for custom / free-form questions.

    Returns:
        str — the system prompt string to pass to the LLM.
    """
    return MODE_SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPT)
