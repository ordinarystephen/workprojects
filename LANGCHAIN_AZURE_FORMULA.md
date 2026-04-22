# LangChain + LangGraph + Azure OpenAI — Reusable Formula

A drop-in recipe for any Python app that needs to call Azure OpenAI through LangChain, with optional structured output and MLflow-ready packaging.

---

## 1. Pinned dependencies

```
langchain==0.3.27
langchain-openai==0.3.33
langgraph==0.6.7
langgraph-checkpoint==2.1.1
azure-identity==1.25.0
mlflow[databricks]==3.7.0   # optional, for tracing/serving
```

> Pin as a set. Upgrading one in isolation breaks imports.

---

## 2. Auth — bearer token, no API key

```python
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureChatOpenAI

def create_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        api_version=os.environ["OPENAI_API_VERSION"],            # e.g. "2025-04-01-preview"
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",       # literal — do not change
        ),
        temperature=0.0,
        # azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),     # only set if your env needs it
    )
```

**Rules:**
- `api_version=` (not `openai_api_version=`)
- `DefaultAzureCredential` works locally via `az login`, in cloud via managed identity
- Omit `azure_endpoint` if your platform's proxy injects it

---

## 3. Graph shape — `StateGraph` with a single work node

```python
from typing import Annotated
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.utils.runnable import RunnableCallable

class State(BaseModel):
    messages: Annotated[list, add_messages]
    # add app-specific fields here:
    payload: str = ""
    mode: str = ""
    output: str = ""

class Context(BaseModel):
    model: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    temperature: float = 0.0
    api_version: str = os.environ.get("OPENAI_API_VERSION", "2025-04-01-preview")

def work_node(state: State, config) -> dict:
    llm = create_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"{state.messages[-1].content}\n---\n{state.payload}"),
    ]
    ai = llm.invoke(messages)
    return {"messages": [ai], "output": ai.content}

async def aWork_node(state, config) -> dict:
    ...  # mirror of work_node using ainvoke

def build_graph():
    g = StateGraph(state_schema=State, context_schema=Context)
    g.add_node("work", RunnableCallable(work_node, aWork_node))
    g.set_entry_point("work")
    g.add_edge("work", END)
    return g.compile()
```

Compile **once** at import, cache as a singleton, reuse across requests.

---

## 4. Structured output (optional but recommended)

```python
from pydantic import BaseModel, Field

class Citation(BaseModel):
    sentence: str = Field(description="Exact sentence containing the claim.")
    source: str   = Field(description="Field/column the claim came from.")
    value: str    = Field(description="The cited value.")

class Response(BaseModel):
    text: str
    citations: list[Citation] = []

# Two-attempt pattern — structured first, plain text fallback
try:
    structured = llm.with_structured_output(Response)
    result = structured.invoke(messages)            # Pydantic instance
    out = {"text": result.text, "citations": [c.model_dump() for c in result.citations]}
except Exception:
    ai = llm.invoke(messages)                        # plain text fallback
    out = {"text": ai.content, "citations": []}
```

> Field `description=` strings are passed to the model as instructions — write them like prompts.

---

## 5. Caller-facing entry point

```python
_graph = None
def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph

def run(user_prompt: str, payload: str = "", mode: str = "") -> dict:
    result = _get_graph().invoke({
        "messages": [HumanMessage(content=user_prompt)],
        "payload":  payload,
        "mode":     mode,
    })
    return {"output": result["output"]}
```

---

## 6. Prompt layout

Keep prompts in their own module:

```python
SYSTEM_PROMPT = "You are ..."

MODE_PROMPTS = {
    "summary":  "...",
    "analysis": "...",
}

def get_system_prompt(mode: str) -> str:
    return MODE_PROMPTS.get(mode, SYSTEM_PROMPT)
```

System message + human message pattern — never concatenate them into one string.

---

## 7. MLflow-serveable wrapper (optional, dormant until needed)

```python
import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest, ResponsesAgentResponse,
    to_chat_completions_input, output_to_responses_items_stream,
)

class GraphResponsesAgent(ResponsesAgent):
    def __init__(self, agent):
        self.agent = agent  # attribute name `agent` is mandatory

    def predict(self, request):
        outputs = [e.item for e in self.predict_stream(request)
                   if e.type == "response.output_item.done"]
        return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)

    def predict_stream(self, request):
        msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        custom = request.custom_inputs or {}
        state_in = {"messages": msgs, "payload": custom.get("payload", ""), "mode": custom.get("mode", "")}
        for _, events in self.agent.stream(state_in, context=request.metadata, stream_mode=["updates"]):
            for node_data in events.values():
                yield from output_to_responses_items_stream(node_data.get("messages", []))

mlflow.models.set_model(GraphResponsesAgent(_get_graph()))
```

This is a **no-op at runtime** unless MLflow is logging the file. Lets you `mlflow.pyfunc.log_model(python_model="agent.py")` later without rewriting anything.

---

## 8. Env vars (the universal four)

| Var | Required | Notes |
|---|---|---|
| `AZURE_OPENAI_DEPLOYMENT` | yes | Deployment name (e.g. `gpt-4o`) |
| `OPENAI_API_VERSION` | yes | e.g. `2025-04-01-preview` |
| `AZURE_OPENAI_ENDPOINT` | optional | Skip if your platform's proxy injects it |
| `MLFLOW_TRACKING_URI` | optional | `databricks` if using AICE-style tracking |

---

## The "formula" in 6 steps

1. **Auth** — `DefaultAzureCredential` + bearer token provider, scope `cognitiveservices.azure.com/.default`
2. **LLM factory** — single `create_llm()` function, all config from env vars
3. **State + Context** — pydantic models; `messages` uses `add_messages`
4. **One-node graph** — compile once, cache singleton
5. **Structured output** — `with_structured_output(PydanticModel)` with plain-text try/except fallback
6. **MLflow wrapper at the bottom** — `set_model(...)` so the file is deployable later with zero refactor

Same pattern scales: add nodes for retrieval, validation, tool calls, conditional routing — the auth/factory/state/wrapper scaffolding stays identical.
