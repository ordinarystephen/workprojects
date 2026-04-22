# ── KRONOS · pipeline/agent.py ────────────────────────────────
# The LangGraph + Azure OpenAI agent layer.
#
# ── Bank-standard pattern ─────────────────────────────────────
# This file follows the AICE LangGraph + MLflow cookbook:
#   - LangGraph StateGraph for the agent flow
#   - ResponsesAgent wrapper for MLflow-compatible serving
#   - Model-from-code registration via mlflow.models.set_model()
#   - Runtime params via Context (pydantic) with env var defaults
#   - AzureChatOpenAI with DefaultAzureCredential bearer token auth
#
# Structured output (Pydantic NarrativeResponse with claims list)
# is layered inside the narrate node — this is the KRONOS-specific
# addition that powers the Claims tab in the UI.
#
# ── Two entry points ──────────────────────────────────────────
# 1. ask_agent(context, user_prompt, mode)
#    Flask-facing wrapper. server.py calls this on every /upload.
#    Invokes the compiled LangGraph directly (no MLflow overhead).
#    Returns { narrative, claims }.
#
# 2. LangGraphResponsesAgent (registered via mlflow.models.set_model)
#    MLflow-compatible entry point for future AICE Studio deployment.
#    Not used at runtime by Flask today — present so this file can
#    be logged as an MLflow model (mlflow.pyfunc.log_model) later.
#
# ── Data flow (runtime) ───────────────────────────────────────
#   server.py
#     └─ ask_agent(context, prompt, mode)
#          └─ _get_graph()          → CompiledStateGraph (cached)
#          └─ graph.invoke({ messages, context_data, mode })
#               └─ narrate() node
#                    └─ get_system_prompt(mode)  → pipeline/prompts.py
#                    └─ create_llm()             → AzureChatOpenAI
#                    └─ llm.with_structured_output(NarrativeResponse)
#                                                → structured output
#                                                OR plain text fallback
#          └─ returns { narrative, claims }
# ──────────────────────────────────────────────────────────────

import os
import logging
from typing import Annotated, Generator, List, Optional

import mlflow
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import get_runtime
from langgraph.utils.runnable import RunnableCallable
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)
from pydantic import BaseModel, Field

from pipeline.registry import get_mode, load_prompt

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# ── STRUCTURED OUTPUT SCHEMA ──────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Pydantic schema for the Claims tab in the UI.
# Field descriptions are passed to the LLM as instructions —
# they tell it what to put in each field.
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Adjust Field descriptions if the claims the LLM      │
# │  produces don't match what you want. For calculated         │
# │  metrics (weighted avg PD, etc.), source_field already      │
# │  tells the LLM to list input fields separated by commas.    │
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
# ── MLFLOW CONFIG MIXIN (bank-standard pattern) ───────────────
# ══════════════════════════════════════════════════════════════
#
# Pulls runtime params from the MLflow ModelConfig when this file
# is loaded as an MLflow model. At normal Flask runtime (no
# ModelConfig present), silently falls back to env var defaults
# on the Context class below.
#
# Copied verbatim from the AICE cookbook — do not modify.

class MlflowConfigAgentContext(BaseModel):
    try:
        __mlflow_model_config = mlflow.models.ModelConfig().to_dict()
    except FileNotFoundError:
        __mlflow_model_config = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, value in self.__mlflow_model_config.items():
            if key in self.model_fields:
                object.__setattr__(self, key, value)


# ══════════════════════════════════════════════════════════════
# ── RUNTIME CONTEXT ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Runtime parameters for the LLM. Defaults come from environment
# variables. At MLflow serving time, callers can override these
# via the `context` parameter of `graph.invoke()`.

class Context(MlflowConfigAgentContext):
    model: Optional[str] = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    temperature: float = 0.0
    api_version: Optional[str] = os.environ.get(
        "OPENAI_API_VERSION", "2025-04-01-preview"
    )


# ══════════════════════════════════════════════════════════════
# ── GRAPH STATE ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# State threaded through the graph. `messages` uses the
# add_messages reducer (standard LangGraph chat pattern). The
# other fields are KRONOS-specific — populated before invoke
# and read back after the graph finishes.
#
# Future multi-turn: when follow-ups are wired, `messages` holds
# the full conversation history and `context_data` / `mode`
# persist for context-aware responses.

class State(BaseModel):
    messages: Annotated[list, add_messages]
    context_data: str = ""     # portfolio data string from analyze.py
    mode: str = ""             # mode slug (e.g. "portfolio-summary")
    parameters: dict = Field(default_factory=dict)
        # validated mode parameters; substituted into the prompt template
    prior_narrative: str = ""  # previous narrative text from the same session.
        # Empty on the first turn; populated on follow-ups so the LLM sees
        # what it said before. Plain text only — no structured claims or
        # verification metadata, matching how real prior conversation looks.
    narrative: str = ""        # final narrative text (set by narrate node)
    claims: list = Field(default_factory=list)  # final claims list


# ══════════════════════════════════════════════════════════════
# ── LLM FACTORY ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Single source of truth for the Azure OpenAI client.
# Uses DefaultAzureCredential + bearer token provider — NO API key.
#
# Env vars:
#   AZURE_OPENAI_DEPLOYMENT  — deployment name (default: gpt-4o)
#   OPENAI_API_VERSION       — API version (default: 2025-04-01-preview)
#   AZURE_OPENAI_ENDPOINT    — optional; Domino proxy injects on Domino

def create_llm(config: RunnableConfig) -> AzureChatOpenAI:
    """
    Build the LangChain-compatible Azure OpenAI client.

    Called from inside the narrate node. Reads runtime params from
    the LangGraph Context (env var defaults). Token acquisition is
    handled by the bearer token provider — no API key is used.
    """

    # Runtime context (env var defaults if no override passed in)
    ctx = get_runtime().context or Context()

    # Optional endpoint — Domino injects via proxy when unset
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

    kwargs = dict(
        azure_deployment=ctx.model,
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
            # ^ Scope for Azure Cognitive Services (covers Azure OpenAI).
            #   Do not change this string.
        ),
        api_version=ctx.api_version,
        temperature=ctx.temperature,
    )
    if endpoint:
        kwargs["azure_endpoint"] = endpoint

    return AzureChatOpenAI(**kwargs)


# ══════════════════════════════════════════════════════════════
# ── HUMAN-TURN TEMPLATE ───────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Controls how the user question + portfolio data are presented
# to the LLM in the "human" turn of the conversation.
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Adjust if you want to change the data presentation.  │
# │  You can add sections like "Prior Period:" or output        │
# │  format instructions here.                                  │
# └─────────────────────────────────────────────────────────────┘

HUMAN_TEMPLATE = """{user_question}

---
Portfolio Data:
{context}
"""


# ══════════════════════════════════════════════════════════════
# ── NARRATE NODE (the actual work) ────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Two attempts per call:
#   1. Structured output via with_structured_output(NarrativeResponse)
#      → narrative + claims list (populates the Claims tab)
#   2. Plain text fallback if structured parsing fails
#      → narrative only, empty claims list
#
# Both paths return the same State field shape, so downstream
# (Flask or ResponsesAgent) doesn't care which one ran.

def _build_message_sequence(state: State) -> list:
    """Assemble the message list passed to the LLM.

    Shape:
      system  : mode prompt (parameter-substituted)
      human   : deterministic context block + the user's question
      (ai     : prior narrative — only on follow-ups)
      (human  : the follow-up question)

    On a first-turn request, state.prior_narrative is empty — we build a
    single human turn using HUMAN_TEMPLATE (context + question together).

    On follow-ups, we split that apart: the context goes in one human turn,
    the prior narrative becomes an assistant turn (text only — no structured
    claims or verification metadata, matching what real prior conversation
    would look like), and the new question becomes a second human turn.

    OUT-OF-SCOPE BREADCRUMB: a follow-up whose question is truly outside
    the inherited mode (e.g. firm-level → "how is Health Care doing") is
    answered from the inherited slice here — degraded quality rather than
    a reroute. Re-slicing / multi-mode plans belong in a future plan-node
    layered above this one.
    """
    mode_def = get_mode(state.mode) if state.mode else None
    system_prompt = load_prompt(mode_def, state.parameters)
    user_question = state.messages[-1].content if state.messages else ""

    if state.prior_narrative:
        context_block = f"Portfolio Data:\n{state.context_data}"
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=context_block),
            AIMessage(content=state.prior_narrative),
            HumanMessage(content=user_question),
        ]

    human_content = HUMAN_TEMPLATE.format(
        user_question=user_question,
        context=state.context_data,
    )
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]


def narrate(state: State, config: RunnableConfig) -> dict:
    """Sync narrate node — builds the prompt and invokes the LLM."""

    llm = create_llm(config=config)
    messages = _build_message_sequence(state)

    # ── Attempt 1: structured output ──────────────────────────
    try:
        structured_llm = llm.with_structured_output(NarrativeResponse)
        result: NarrativeResponse = structured_llm.invoke(messages)
        claims = [c.model_dump() for c in result.claims]
        log.info("Structured output succeeded. Claims: %d", len(claims))
        return {
            "messages":  [AIMessage(content=result.narrative)],
            "narrative": result.narrative,
            "claims":    claims,
        }
    except Exception as e:
        log.warning(
            "Structured output failed (%s). Falling back to plain text.", e
        )

    # ── Attempt 2: plain text fallback ────────────────────────
    ai_message = llm.invoke(messages)
    return {
        "messages":  [ai_message],
        "narrative": ai_message.content,
        "claims":    [],
    }


async def anarrate(state: State, config: RunnableConfig) -> dict:
    """Async variant — same logic via ainvoke. Required by RunnableCallable."""

    llm = create_llm(config=config)
    messages = _build_message_sequence(state)

    try:
        structured_llm = llm.with_structured_output(NarrativeResponse)
        result: NarrativeResponse = await structured_llm.ainvoke(messages)
        claims = [c.model_dump() for c in result.claims]
        return {
            "messages":  [AIMessage(content=result.narrative)],
            "narrative": result.narrative,
            "claims":    claims,
        }
    except Exception as e:
        log.warning(
            "Structured output failed (%s). Falling back to plain text.", e
        )

    ai_message = await llm.ainvoke(messages)
    return {
        "messages":  [ai_message],
        "narrative": ai_message.content,
        "claims":    [],
    }


# ══════════════════════════════════════════════════════════════
# ── GRAPH BUILDER ═════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#
# Single-node graph today: (entry) → narrate → END
#
# Future extension points:
#   - Separate `extract_claims` node (2nd LLM pass for claims)
#   - `validate` node calling cross_check_numbers; conditional
#     loop-back if too many unverified numbers
#   - `tools` node for tool-calling (see AICE
#     langgraph_agent_with_tools.ipynb)

def load_graph() -> CompiledStateGraph:
    """Builds and compiles the LangGraph StateGraph."""
    builder = StateGraph(state_schema=State, context_schema=Context)
    builder.add_node(
        "narrate",
        RunnableCallable(narrate, anarrate),
        input_schema=State,
    )
    builder.set_entry_point("narrate")
    builder.add_edge("narrate", END)
    return builder.compile()


# Compiled graph singleton — built once per process, reused across
# Flask requests. Avoids re-compiling on every /upload call.
_compiled_graph: Optional[CompiledStateGraph] = None


def _get_graph() -> CompiledStateGraph:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = load_graph()
    return _compiled_graph


# ══════════════════════════════════════════════════════════════
# ── FLASK-FACING ENTRY POINT ──────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# server.py calls this. Wraps graph.invoke() with the right
# State shape and unpacks the result.

def ask_agent(
    context: str,
    user_prompt: str,
    mode: str = "",
    parameters: dict | None = None,
    prior_narrative: str = "",
) -> dict:
    """
    Run the agent graph for a single /upload request.

    Args:
        context         : Portfolio data string from pipeline/analyze.py.
                          Becomes state.context_data — the data the LLM narrates.
        user_prompt     : The user's question or canned prompt text.
                          Becomes the HumanMessage content.
        mode            : Mode slug (e.g. "portfolio-summary"). Resolves to a
                          prompt template via pipeline/registry.load_prompt.
                          Empty string for custom free-form questions (uses
                          config/prompts/default.md).
        parameters      : Validated mode parameters. Substituted into the
                          prompt template via simple {{name}} replacement.
        prior_narrative : Previous narrative from the same session, if any.
                          When non-empty, the message sequence becomes
                          system → human(context) → ai(prior_narrative) →
                          human(user_prompt) — a true multi-turn shape.

    Returns:
        dict:
            "narrative" : str — the LLM's generated analysis text.
            "claims"    : list — structured claims from the narrative.
                          Each: { sentence, source_field, cited_value }
                          Empty if structured output failed.

    # FUTURE — slice-result caching.
    # Today every follow-up re-runs the slicer to rebuild `context` and the
    # matching `verifiable_values`. Cheap (~200–400ms) and trivially correct
    # since the inputs are the same. If we ever optimize, the cache key would
    # be (session_id, file_hash, mode, parameters) and the cache entry must
    # preserve BOTH the slicer context AND verifiable_values side-by-side —
    # the verifier depends on the latter being identical to what the slicer
    # produced when context was built. Breadcrumb only, not a TODO.
    """
    graph = _get_graph()
    state_in = {
        "messages":        [HumanMessage(content=user_prompt)],
        "context_data":    context,
        "mode":            mode,
        "parameters":      parameters or {},
        "prior_narrative": prior_narrative or "",
    }
    result = graph.invoke(state_in)
    return {
        "narrative": result.get("narrative", ""),
        "claims":    result.get("claims", []),
    }


# ══════════════════════════════════════════════════════════════
# ── MLFLOW RESPONSES AGENT WRAPPER ────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Bank-standard wrapper for model-from-code logging. Today this
# class is DEFINED but not USED at Flask runtime — it's here so
# this file can be logged to MLflow as a deployable model via:
#
#   mlflow.pyfunc.log_model(
#       name="kronos-agent",
#       python_model="pipeline/agent.py",
#       input_example={"input": [...], "metadata": {...}},
#       streamable=True,
#   )
#
# When the time comes to register KRONOS as an AICE Studio served
# model, nothing in this class needs to change — the deployment
# pipeline picks up the instance registered via set_model() at
# the bottom of this file.

class LangGraphResponsesAgent(ResponsesAgent):
    """MLflow-compatible ResponsesAgent wrapping the KRONOS LangGraph."""

    def __init__(self, agent: CompiledStateGraph):
        self.agent = agent   # ! mandatory — ResponsesAgent expects `agent` attr

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        outputs = [
            event.item
            for event in self.predict_stream(request)
            if event.type == "response.output_item.done"
        ]
        return ResponsesAgentResponse(
            output=outputs,
            custom_outputs=request.custom_inputs,
        )

    def predict_stream(
        self,
        request: ResponsesAgentRequest,
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        cc_msgs = to_chat_completions_input(
            [i.model_dump() for i in request.input]
        )

        # custom_inputs carries the KRONOS extras (context_data, mode)
        # so they reach the narrate node. Defaults handle the case
        # where a caller doesn't pass them.
        custom = request.custom_inputs or {}
        state_in = {
            "messages":     cc_msgs,
            "context_data": custom.get("context_data", ""),
            "mode":         custom.get("mode", ""),
        }

        for _, events in self.agent.stream(
            state_in,
            context=request.metadata,
            stream_mode=["updates"],
        ):
            for node_data in events.values():
                yield from output_to_responses_items_stream(
                    node_data.get("messages", [])
                )


# ══════════════════════════════════════════════════════════════
# ── MODEL-FROM-CODE REGISTRATION ──────────────────────────════
# ══════════════════════════════════════════════════════════════
#
# This call registers the LangGraphResponsesAgent instance as the
# "model" that mlflow.pyfunc.log_model() picks up when this file
# is referenced by path.
#
# It is a no-op unless MLflow is actively logging this file as
# a model artifact. At normal Flask runtime, Python imports this
# module, the instance is constructed, set_model() records a
# reference in MLflow's internal state, and nothing else happens.
# No network calls, no tracking activation.
#
# NOTE: We intentionally do NOT call mlflow.langchain.autolog()
# here. That activates LangChain trace capture globally, and we
# want it gated by the KRONOS_MLFLOW_ENABLED env var. See
# pipeline/tracking.py → activate_mlflow() for the gated call.

mlflow.models.set_model(LangGraphResponsesAgent(_get_graph()))
