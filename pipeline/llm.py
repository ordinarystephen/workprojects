# ── KRONOS · pipeline/llm.py ──────────────────────────────────
# Builds and returns the Azure OpenAI language model client.
#
# This is the ONE place in the codebase that touches authentication
# and model configuration. All other files import build_llm() from
# here — nothing else creates an LLM directly.
#
# ── Auth strategy ─────────────────────────────────────────────
# Uses DefaultAzureCredential + a bearer token provider.
# No API key is required or used.
#
# How DefaultAzureCredential works:
#   - Locally: picks up your `az login` session automatically
#   - On Domino: uses the managed identity attached to the workspace
#   - No code change needed between environments — it just works
#
# ── Environment variables ─────────────────────────────────────
# Required:
#   AZURE_OPENAI_DEPLOYMENT  — deployment name, e.g. "gpt-4o"
#   OPENAI_API_VERSION       — API version, e.g. "2025-04-01-preview"
#
# Optional:
#   AZURE_OPENAI_ENDPOINT    — e.g. "https://your-resource.openai.azure.com/"
#                              Omit on Domino — the proxy injects it automatically.
#                              Set locally or in non-Domino environments.
#
# Set these in Domino's project settings under "Environment Variables".
# Locally, create a .env file and load with python-dotenv, or export
# them in your shell before running `python server.py`.
# ──────────────────────────────────────────────────────────────

import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureChatOpenAI


def build_llm() -> AzureChatOpenAI:
    """
    Constructs and returns an AzureChatOpenAI LangChain LLM client.

    Called once per request in pipeline/agent.py.
    All configuration comes from environment variables — nothing is
    hardcoded here.

    Returns:
        AzureChatOpenAI — a LangChain-compatible LLM object ready
        to be used in a chain (prompt_template | llm | parser).

    Raises:
        azure.core.exceptions.ClientAuthenticationError — if no valid
        Azure credential is found (e.g. not logged in with `az login` locally).
    """

    # ── Step 1: Get Azure credentials ─────────────────────────
    # DefaultAzureCredential tries multiple auth methods in order:
    #   1. Environment variables (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, etc.)
    #   2. Workload identity (for k8s / Domino pods)
    #   3. Managed identity (Domino compute environments)
    #   4. Azure CLI (`az login` — what you use locally)
    #   5. Visual Studio / VS Code credentials
    # The first one that works is used. No configuration needed.
    credential = DefaultAzureCredential()

    # ── Step 2: Build a bearer token provider ─────────────────
    # This wraps the credential into a callable that LangChain can
    # call each time it needs a fresh token. Tokens expire; this
    # provider handles automatic renewal transparently.
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default"
        # ^ This scope tells Azure AD which resource we're authenticating
        #   to — Azure Cognitive Services (which covers Azure OpenAI).
        #   Do not change this string.
    )

    # ── Step 3: Read config from environment ──────────────────
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    # ^ CUSTOMIZE: Change the default ("gpt-4o") if your deployment
    #   uses a different model name. Or just set the env var.

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    # ^ Optional. If not set, the Domino proxy supplies it.
    #   Locally: export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/

    api_version = os.getenv("OPENAI_API_VERSION", "2025-04-01-preview")
    # ^ IMPORTANT: Use `api_version=` — NOT `openai_api_version=`
    #   (langchain-openai 0.3.33 changed the parameter name).

    # ── Step 4: Build keyword arguments for AzureChatOpenAI ───
    # We conditionally add azure_endpoint only when it's set,
    # because passing endpoint=None would cause a validation error.
    kwargs = dict(
        azure_deployment=deployment,
        azure_ad_token_provider=token_provider,  # bearer token auth (not API key)
        api_version=api_version,
        temperature=0,  # 0 = deterministic, consistent outputs.
                        # Raise to 0.3–0.7 if you want more varied language.
    )

    if endpoint:
        kwargs["azure_endpoint"] = endpoint

    # ── Step 5: Return the LLM ────────────────────────────────
    # AzureChatOpenAI is LangChain's wrapper around Azure OpenAI.
    # It implements the standard LangChain Runnable interface, so it
    # can be dropped directly into a chain: prompt | llm | parser
    return AzureChatOpenAI(**kwargs)
