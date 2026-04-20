# ── KRONOS · pipeline/tracking.py ─────────────────────────────
# MLflow tracking layer — DORMANT BY DEFAULT.
#
# ── Why this file exists ──────────────────────────────────────
# Production deployment at the bank requires audit logging of
# every LLM call (prompt, response, latency, mode, user hash,
# tokens, verification outcome). The AICE platform provides this
# via MLflow + aice-mlflow-plugins routing to Databricks.
#
# Until you have Databricks / AICE access provisioned, we don't
# want startup errors or silent connection attempts. So everything
# here is GATED behind the KRONOS_MLFLOW_ENABLED env var.
#
# ── How to turn it on ─────────────────────────────────────────
# When your access is ready:
#
#   export KRONOS_MLFLOW_ENABLED=true
#   export MLFLOW_EXPERIMENT_NAME=kronos-dev      # or kronos-prod
#   pip install aice-mlflow-plugins==0.1.3         # internal package
#
# Restart the Flask app. `activate_mlflow()` is called once at
# startup from server.py — when the flag is on, it:
#   1. Sets tracking URI to "databricks"
#   2. Sets the experiment name
#   3. Calls mlflow.langchain.autolog() for auto trace capture
#
# When the flag is off (default), activate_mlflow() is a no-op
# and the mlflow_run() context manager yields without starting
# a real run. The app runs identically to if this file didn't exist.
#
# ── What gets logged (when enabled) ───────────────────────────
#   Tags:        mode, kronos_version
#   Params:      user_prompt (truncated), file_name, file_size
#   Metrics:     latency_ms, verified_count, unverified_count,
#                claims_count, narrative_length
#   Artifacts:   context_sent (the data string the LLM saw)
#   Auto-traces: every LangChain invocation via autolog()
# ──────────────────────────────────────────────────────────────

import os
import time
import logging
from contextlib import contextmanager
from typing import Optional

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# ── FLAG (the kill switch) ────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Read once at module import. Accepts "true" / "1" / "yes" (case
# insensitive). Anything else is treated as disabled.

def _is_enabled() -> bool:
    val = os.getenv("KRONOS_MLFLOW_ENABLED", "").strip().lower()
    return val in ("true", "1", "yes")


# Module-level state — set by activate_mlflow() once at startup
_ACTIVATED: bool = False


# ══════════════════════════════════════════════════════════════
# ── ACTIVATION (called once at app startup) ───────────────────
# ══════════════════════════════════════════════════════════════
#
# Must be called from server.py BEFORE app.run() so that:
#   1. autolog hooks are installed before any LangChain call
#   2. activation errors surface at startup, not mid-request

def activate_mlflow() -> None:
    """
    Activate MLflow tracking if KRONOS_MLFLOW_ENABLED is set.

    Safe to call when the flag is unset — becomes a no-op.
    Safe to call multiple times — subsequent calls are no-ops.
    Catches all activation errors and logs them without raising
    (we'd rather lose tracking than crash the app).
    """
    global _ACTIVATED

    if _ACTIVATED:
        return

    if not _is_enabled():
        log.info(
            "MLflow tracking is disabled "
            "(set KRONOS_MLFLOW_ENABLED=true to enable)."
        )
        return

    # Import mlflow lazily so disabled-mode environments don't need
    # the mlflow package to be importable on startup (defensive).
    try:
        import mlflow

        # Databricks-backed tracking store via aice-mlflow-plugins.
        # The plugin package is internal and handles host/user
        # routing automatically. If the plugin isn't installed, the
        # tracking call below may still work but will lack AICE
        # context — log a warning, don't crash.
        try:
            import aice_mlflow_plugins  # noqa: F401
        except ImportError:
            log.warning(
                "aice-mlflow-plugins not installed. MLflow will run "
                "but tracking routing to AICE Databricks may not work."
            )

        mlflow.set_tracking_uri("databricks")

        experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME", "kronos-dev")
        mlflow.set_experiment(experiment_name=experiment_name)

        # Automatic LangChain trace capture — every chain invocation
        # is logged with prompt, response, latency, token usage.
        mlflow.langchain.autolog()

        _ACTIVATED = True
        log.info(
            "MLflow tracking ACTIVE. Experiment: %s. Tracking URI: databricks.",
            experiment_name,
        )

    except Exception as e:
        # Broad except is intentional: we never want tracking setup
        # to take down the main Flask app.
        log.error("MLflow activation failed (%s). Tracking disabled.", e)


# ══════════════════════════════════════════════════════════════
# ── PER-REQUEST CONTEXT MANAGER ───────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Usage in server.py:
#
#   with mlflow_run(mode=mode, file=file) as run:
#       ...
#       run.log("latency_ms", elapsed)
#       run.log("claims_count", len(claims))
#
# When disabled: yields a no-op Run shim — .log() calls do nothing.
# When enabled:  wraps mlflow.start_run(), logs tags/params upfront,
#                and the shim writes metrics on .log() calls.

class _NoOpRun:
    """Stand-in Run object when MLflow is disabled."""
    def log(self, key: str, value) -> None:  # noqa: ANN001
        pass

    def log_artifact_text(self, text: str, artifact_name: str) -> None:
        pass


class _ActiveRun:
    """Real Run object — writes to MLflow when enabled."""

    def __init__(self, mlflow_module):
        self._mlflow = mlflow_module

    def log(self, key: str, value) -> None:  # noqa: ANN001
        try:
            if isinstance(value, (int, float)):
                self._mlflow.log_metric(key, value)
            else:
                self._mlflow.log_param(key, str(value)[:250])
        except Exception as e:
            log.warning("MLflow log failed for %s: %s", key, e)

    def log_artifact_text(self, text: str, artifact_name: str) -> None:
        try:
            self._mlflow.log_text(text, artifact_name)
        except Exception as e:
            log.warning("MLflow artifact log failed for %s: %s", artifact_name, e)


@contextmanager
def mlflow_run(
    mode: str = "",
    file_name: str = "",
    file_size: int = 0,
    user_prompt: str = "",
):
    """
    Wrap a single /upload handler with MLflow tracking.

    When KRONOS_MLFLOW_ENABLED is unset, yields a no-op run and
    introduces zero overhead beyond a context-manager wrap.

    When enabled, starts an MLflow run, sets tags and params,
    yields an _ActiveRun for metric/artifact logging inside the
    request, and ends the run on exit (success or error).

    Args:
        mode        : KRONOS mode slug (tag value).
        file_name   : Uploaded file name (param value).
        file_size   : Uploaded file size in bytes (metric value).
        user_prompt : The user's prompt text (param, truncated).
    """
    if not _ACTIVATED:
        yield _NoOpRun()
        return

    try:
        import mlflow
    except Exception as e:
        log.warning("MLflow import failed at runtime (%s). Skipping run.", e)
        yield _NoOpRun()
        return

    start = time.perf_counter()

    try:
        with mlflow.start_run() as run:
            try:
                mlflow.set_tag("kronos.mode", mode or "custom")
                mlflow.set_tag("kronos.component", "upload")
                mlflow.log_param("user_prompt", (user_prompt or "")[:250])
                mlflow.log_param("file_name", file_name or "")
                if file_size:
                    mlflow.log_metric("file_size_bytes", float(file_size))
            except Exception as e:
                log.warning("MLflow pre-run log failed: %s", e)

            active = _ActiveRun(mlflow)
            try:
                yield active
            finally:
                # Always log request latency, even on error paths.
                try:
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    mlflow.log_metric("latency_ms", elapsed_ms)
                except Exception as e:
                    log.warning("MLflow latency log failed: %s", e)

    except Exception as e:
        # start_run() itself failed — fall back to no-op.
        log.error("MLflow start_run failed (%s). Yielding no-op run.", e)
        yield _NoOpRun()
