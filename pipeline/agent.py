# ── KRONOS · pipeline/agent.py ────────────────────────────────
# The LangChain agent layer. Takes the structured data context
# from the processor (analyze.py) and the user's question, builds
# a prompt, calls Azure OpenAI via build_llm(), and returns the
# narrative text plus optional structured claims.
#
# ── Two output modes ──────────────────────────────────────────
#
#   PRIMARY: Structured output
#     Uses LangChain's with_structured_output() + Pydantic model.
#     Forces the LLM to return:
#       { narrative: str, claims: [{ sentence, source_field, cited_value }] }
#     The claims list is what populates the "Claims" tab in the UI.
#
#   FALLBACK: Plain text
#     If structured output fails (parse error, model non-compliance),
#     falls back to a plain StrOutputParser chain.
#     Returns: { narrative: str, claims: [] }
#
#   server.py always gets the same dict shape regardless of which
#   path ran — callers don't need to handle the difference.
#
# ── Data flow ─────────────────────────────────────────────────
#
#   server.py
#     └─ ask_agent(context, prompt, mode)   → THIS FILE
#          └─ get_system_prompt(mode)       → pipeline/prompts.py
#          └─ build_llm()                  → pipeline/llm.py
#          └─ chain.invoke(...)            → Azure OpenAI
#          └─ returns { narrative, claims }
#     └─ cross_check_numbers(...)          → pipeline/validate.py
#     └─ returns { narrative, claims, context_sent, verification }
#
# ──────────────────────────────────────────────────────────────

import logging
from typing import List, Optional

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from pipeline.llm import build_llm
from pipeline.prompts import get_system_prompt

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# ── STRUCTURED OUTPUT SCHEMA ──────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# These Pydantic models define the shape the LLM must return when
# using structured output mode (with_structured_output).
#
# The Field `description` strings are passed to the LLM as
# instructions — they tell it what to put in each field.
# Write these carefully: they are part of your prompt.
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Adjust Field descriptions if the claims the LLM      │
# │  produces don't match what you want. For example, if you    │
# │  want it to cite component inputs for calculated metrics     │
# │  (like weighted average PD = sum of X / Y), add that to     │
# │  the source_field description below.                        │
# └─────────────────────────────────────────────────────────────┘

class Claim(BaseModel):
    """A single factual claim made in the narrative."""

    sentence: str = Field(
        description=(
            "The exact sentence or phrase from the narrative that contains "
            "a specific factual claim, figure, or metric. Quote it precisely."
        )
    )
    source_field: str = Field(
        description=(
            "The field name, column name, or metric label from the Portfolio Data "
            "section that this claim is based on. For calculated metrics (e.g. "
            "weighted average PD, year-over-year delta), list the input fields "
            "used in the calculation separated by commas."
        )
    )
    cited_value: str = Field(
        description=(
            "The specific number, percentage, or value cited in this claim. "
            "Use the exact format from the source data (e.g. '4.1%', '$4.82B'). "
            "For calculated results, write the result and the formula "
            "(e.g. '2.3x = reserves / projected NCO')."
        )
    )


class NarrativeResponse(BaseModel):
    """The full structured response from the LLM."""

    narrative: str = Field(
        description=(
            "The complete analysis narrative text. Write this for a risk management "
            "audience. Do not abbreviate — the full narrative goes here."
        )
    )
    claims: List[Claim] = Field(
        default_factory=list,
        description=(
            "A list of every specific factual claim made in the narrative. "
            "Include all cited figures, percentages, and calculated metrics. "
            "For calculated metrics, explain the source inputs in source_field."
        )
    )


# ══════════════════════════════════════════════════════════════
# ── PROMPT TEMPLATES ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

# Human-turn template — used by both structured and fallback chains.
# {user_question} and {context} are filled in at invoke time.
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Adjust if you want to change how data is presented   │
# │  to the LLM. You can add sections like "Prior Period:",     │
# │  "Key Metrics Summary:", or output format instructions.     │
# └─────────────────────────────────────────────────────────────┘
HUMAN_TEMPLATE = """{user_question}

---
Portfolio Data:
{context}
"""


# ══════════════════════════════════════════════════════════════
# ── MAIN FUNCTION ─────────────────────────════════════════════
# ══════════════════════════════════════════════════════════════

def ask_agent(context: str, user_prompt: str, mode: str = "") -> dict:
    """
    Builds and invokes a LangChain chain to generate a narrative response.

    Tries structured output first (returns narrative + claims list).
    Falls back to plain text output if structured parsing fails.

    Args:
        context     : The data payload string from analyze.py.
                      Passed to the LLM as the "Portfolio Data" section.

        user_prompt : The user's question or canned prompt text.

        mode        : Mode slug (e.g. "portfolio-summary").
                      Selects the system prompt from pipeline/prompts.py.
                      Empty string for custom questions.

    Returns:
        dict:
            "narrative" : str — the LLM's generated analysis text.
            "claims"    : list — structured claims from the narrative.
                          Each item: { sentence, source_field, cited_value }
                          Empty list if structured output failed or
                          if the LLM returned no claims.
    """

    system_prompt = get_system_prompt(mode)
    llm = build_llm()

    # ── Attempt 1: Structured output ──────────────────────────
    # with_structured_output() instruments the LLM to return JSON
    # matching the NarrativeResponse Pydantic schema.
    # Field descriptions in the schema serve as LLM instructions.
    try:
        structured_llm = llm.with_structured_output(NarrativeResponse)

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", HUMAN_TEMPLATE),
        ])

        chain = prompt_template | structured_llm
        result: NarrativeResponse = chain.invoke({
            "user_question": user_prompt,
            "context": context,
        })

        log.info("Structured output succeeded. Claims extracted: %d", len(result.claims))

        return {
            "narrative": result.narrative,
            "claims": [
                {
                    "sentence":     c.sentence,
                    "source_field": c.source_field,
                    "cited_value":  c.cited_value,
                }
                for c in result.claims
            ],
        }

    except Exception as e:
        # ── Attempt 2: Plain text fallback ────────────────────
        # If the LLM returns non-compliant JSON or Pydantic validation
        # fails, fall back to a plain StrOutputParser chain.
        # Returns an empty claims list — the narrative still renders
        # correctly, the Claims tab just won't have content.
        log.warning("Structured output failed (%s). Falling back to plain text.", str(e))

    try:
        plain_prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", HUMAN_TEMPLATE),
        ])
        plain_chain = plain_prompt | llm | StrOutputParser()
        narrative = plain_chain.invoke({
            "user_question": user_prompt,
            "context": context,
        })
        return {"narrative": narrative, "claims": []}

    except Exception as e:
        log.error("Plain text fallback also failed: %s", str(e))
        raise
