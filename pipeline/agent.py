# ── KRONOS · pipeline/agent.py ────────────────────────────────
# The LangChain agent layer. Takes the structured data context
# from the processor (analyze.py) and the user's question, builds
# a prompt, calls Azure OpenAI via build_llm(), and returns the
# narrative text.
#
# ── How this fits in the flow ─────────────────────────────────
#
#   server.py
#     └─ calls analyze(file, mode)    → pipeline/analyze.py
#     └─ calls ask_agent(ctx, prompt, mode) → THIS FILE
#                                        └─ build_llm()  → pipeline/llm.py
#                                        └─ get_system_prompt() → pipeline/prompts.py
#                                        └─ Azure OpenAI
#     └─ returns { narrative, metrics } to frontend
#
# ── What is a LangChain "chain"? ─────────────────────────────
# A chain is a pipeline of components connected with the `|` operator:
#
#   prompt_template | llm | output_parser
#
# Each component takes input, transforms it, and passes it on.
# chain.invoke({ ... }) runs the full pipeline and returns the result.
#
# ──────────────────────────────────────────────────────────────

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from pipeline.llm import build_llm
from pipeline.prompts import get_system_prompt


# ── Human-turn message template ───────────────────────────────
# This is the structure of the "human" side of the conversation.
# It has two variable slots (filled in by chain.invoke()):
#
#   {user_question}  ← the analyst's question / canned prompt text
#   {context}        ← the deterministic data payload from analyze.py
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Adjust this template if you want to change how the   │
# │  data and question are presented to the LLM.                │
# │                                                             │
# │  For example, you could add:                                │
# │    - Instructions on output format ("respond in JSON")      │
# │    - A prior-period comparison section                      │
# │    - A "key metrics summary" section before the full data   │
# └─────────────────────────────────────────────────────────────┘
#
HUMAN_TEMPLATE = """{user_question}

---
Portfolio Data:
{context}
"""


def ask_agent(context: str, user_prompt: str, mode: str = "") -> dict:
    """
    Builds and invokes a LangChain chain to generate a narrative response.

    This is the single function server.py calls to talk to the LLM.
    It handles prompt construction, model invocation, and response parsing.

    Args:
        context     : The data payload string from analyze.py.
                      In the full pipeline, this will be
                      deterministic_narrative_payload — the mode-scoped
                      reduction of your processor's JSON output.
                      In the placeholder, it's a pandas summary string.

        user_prompt : The question the user typed or selected.
                      Comes from the frontend's textarea / canned button.

        mode        : The mode slug (e.g. "portfolio-summary").
                      Used to select the right system prompt.
                      Empty string for custom / free-form questions.

    Returns:
        dict with key:
            "narrative" : str — the LLM's generated analysis text.

    Raises:
        Propagates any exceptions from LangChain / Azure OpenAI.
        server.py catches these and returns a 500 error to the frontend.
    """

    # ── Step 1: Get the system prompt for this mode ────────────
    # get_system_prompt() returns the mode-specific prompt if one
    # exists in MODE_SYSTEM_PROMPTS, otherwise falls back to
    # SYSTEM_PROMPT. Both live in pipeline/prompts.py.
    system_prompt = get_system_prompt(mode)

    # ── Step 2: Build the prompt template ─────────────────────
    # ChatPromptTemplate.from_messages() creates a structured prompt
    # with a "system" message (instructions) and a "human" message
    # (the actual question + data).
    #
    # The ("system", ...) tuple sets the AI's persona and rules.
    # The ("human", HUMAN_TEMPLATE) tuple is the user's turn, with
    # {user_question} and {context} filled in at invoke time.
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", HUMAN_TEMPLATE),
    ])

    # ── Step 3: Build the LLM ─────────────────────────────────
    # build_llm() creates the AzureChatOpenAI client with bearer
    # token auth. See pipeline/llm.py for auth details.
    llm = build_llm()

    # ── Step 4: Build the output parser ───────────────────────
    # StrOutputParser() extracts the plain text string from the
    # LLM's response object. Without it, you'd get a raw
    # AIMessage object with metadata attached.
    output_parser = StrOutputParser()

    # ── Step 5: Assemble the chain ────────────────────────────
    # The pipe operator `|` chains the three components together.
    # Data flows left to right:
    #   prompt_template → formats the messages dict into a prompt
    #   llm             → sends the prompt to Azure OpenAI, gets AIMessage back
    #   output_parser   → extracts the text string from AIMessage
    chain = prompt_template | llm | output_parser

    # ── Step 6: Run the chain ─────────────────────────────────
    # chain.invoke() executes the full pipeline synchronously.
    # The dict keys must match the {variable} names in the templates.
    narrative = chain.invoke({
        "user_question": user_prompt,
        "context": context,
    })

    # ── Step 7: Return structured result ──────────────────────
    # server.py expects a dict with a "narrative" key.
    # If you later want to return structured metrics from the LLM
    # (not just the processor), add them here.
    return {"narrative": narrative}
