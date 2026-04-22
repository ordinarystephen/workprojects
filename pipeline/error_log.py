# ── KRONOS · pipeline/error_log.py ────────────────────────────
# Two-tier error logging for the agent runtime.
#
# ── Tier 1 — local JSONL (always on) ──────────────────────────
# Writes one JSON record per error event to:
#     <KRONOS_ERROR_LOG_DIR>/kronos-errors.jsonl
# Default dir is "logs/" relative to the working directory.
# Rotation triggers when the active file is ≥10 MB OR was last
# written on a previous date (whichever comes first).
#
# TODO — confirm the persistent path on Domino. /domino/datasets is
# the conventional location for files that must outlive a workspace
# restart, but it should be confirmed per-deployment before being
# pinned as the default. Leaving this gated on the env var lets the
# Domino app set it to /domino/datasets/kronos/errors at deploy time
# without a code change here.
#
# ── Tier 2 — MLflow (gated by KRONOS_MLFLOW_ENABLED) ──────────
# When MLflow is enabled AND there is an active run on the calling
# thread, we emit:
#     - tag       kronos.has_error = "true"
#     - metric    kronos_error_<event_type>_count = 1
#     - artifact  kronos-error-<timestamp>.json     (full record)
# When MLflow is dormant (default) this tier is a no-op.
#
# Both tiers are best-effort. A logging failure NEVER raises — we'd
# rather lose a log line than fail the request that provoked it.
#
# ── Public API ────────────────────────────────────────────────
#     log_error(event_type, *, error=None, mode="", parameters=None,
#               session_id="", context_snippet=None,
#               user_prompt=None, **additional)
#
#     read_recent(limit=50)   -> list[dict]
#         Returns the tail of the active JSONL file. Used by
#         GET /errors/recent (gated by KRONOS_ERRORS_ENDPOINT_ENABLED).
#
# ── Field policy ──────────────────────────────────────────────
# What we LOG:
#     timestamp, event_type, mode, parameters, session_id,
#     error_class, error_message, stack_trace,
#     context_snippet (first 500 chars only),
#     user_prompt    (first 250 chars only),
#     additional     (caller-supplied diagnostic crumbs)
#
# What we NEVER log:
#     - Full uploaded file contents
#     - Full LLM narratives
#     - Full user prompts beyond the bounded snippet
#     - Verifiable values (may include exposure dollar amounts)
# These can carry PII, position data, or counterparty fragments.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
import threading
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────
_DEFAULT_DIR           = "logs"
_LOG_FILENAME          = "kronos-errors.jsonl"
_MAX_BYTES             = 10 * 1024 * 1024   # 10 MB
_CONTEXT_SNIPPET_LIMIT = 500
_USER_PROMPT_LIMIT     = 250
_READ_RECENT_CAP       = 500                # absolute hard cap regardless of caller

# Module-level lock so concurrent Flask threads can't interleave writes
# or race on rotation rename.
_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════
# ── PATH / ROTATION HELPERS ───────────────────────────────────
# ══════════════════════════════════════════════════════════════

def _log_dir() -> Path:
    """Resolve and (if missing) create the log directory."""
    raw = (os.getenv("KRONOS_ERROR_LOG_DIR") or _DEFAULT_DIR).strip()
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_path() -> Path:
    return _log_dir() / _LOG_FILENAME


def _needs_rotation(path: Path) -> bool:
    """True if the active file is too large or stale (different date)."""
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size >= _MAX_BYTES:
        return True
    return datetime.fromtimestamp(st.st_mtime).date() != date.today()


def _rotate(path: Path) -> None:
    """Rename the active file to a dated archive. Skips on any OSError."""
    today = date.today().isoformat()
    n = 0
    while True:
        target = path.with_name(f"kronos-errors-{today}-{n:03d}.jsonl")
        if not target.exists():
            try:
                path.rename(target)
            except OSError as e:
                log.warning("error_log rotation rename failed: %s", e)
            return
        n += 1


# ══════════════════════════════════════════════════════════════
# ── FIELD SANITIZERS ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def _truncate(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    s = str(value)
    if len(s) <= limit:
        return s
    return s[:limit] + "…"


def _format_stack(error: Optional[BaseException]) -> Optional[str]:
    if error is None:
        return None
    try:
        return "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    except Exception:  # noqa: BLE001 — truly defensive
        return None


# ══════════════════════════════════════════════════════════════
# ── PUBLIC: log_error ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def log_error(
    event_type: str,
    *,
    error: Optional[BaseException] = None,
    mode: str = "",
    parameters: Optional[dict] = None,
    session_id: str = "",
    context_snippet: Optional[str] = None,
    user_prompt: Optional[str] = None,
    **additional: Any,
) -> None:
    """
    Record one error event to JSONL (always) and MLflow (when active).

    `event_type` is a short slug used for grouping and MLflow metric
    naming — keep it stable. Examples in use today:
        "llm_failed", "slicer_failed", "classification_failed",
        "verification_mismatch", "upload_parse_failed",
        "parameter_validation_failed", "mode_not_implemented",
        "cube_parameter_options_failed".

    `additional` is a free-form dict for caller-supplied crumbs. It is
    written verbatim to the JSONL record under "additional" — only put
    diagnosis-relevant scalars in it.
    """
    record = {
        "timestamp":       datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        "event_type":      event_type,
        "mode":            mode or "",
        "parameters":      parameters or {},
        "session_id":      session_id or "",
        "error_class":     type(error).__name__ if error else None,
        "error_message":   str(error) if error else None,
        "context_snippet": _truncate(context_snippet, _CONTEXT_SNIPPET_LIMIT),
        "user_prompt":     _truncate(user_prompt, _USER_PROMPT_LIMIT),
        "stack_trace":     _format_stack(error),
        "additional":      additional or {},
    }

    _write_jsonl(record)
    _emit_mlflow(record)


# ══════════════════════════════════════════════════════════════
# ── TIER 1 — JSONL WRITE ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def _write_jsonl(record: dict) -> None:
    try:
        path = _log_path()
        with _lock:
            if _needs_rotation(path):
                _rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 — never raise from logging
        log.warning("error_log JSONL write failed: %s", e)


# ══════════════════════════════════════════════════════════════
# ── TIER 2 — MLFLOW EMIT ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def _mlflow_enabled() -> bool:
    return (os.getenv("KRONOS_MLFLOW_ENABLED") or "").strip().lower() in (
        "true", "1", "yes",
    )


def _emit_mlflow(record: dict) -> None:
    if not _mlflow_enabled():
        return
    try:
        import mlflow

        active = mlflow.active_run()
        if active is None:
            # Outside any run — no-op. mlflow_run() in tracking.py wraps
            # the upload handler, so request-time errors will see one.
            return

        event_type = (record.get("event_type") or "unknown")
        mlflow.set_tag("kronos.has_error", "true")
        mlflow.log_metric(f"kronos_error_{event_type}_count", 1)

        # Artifact name uses the timestamp (colon-stripped for FS friendliness).
        ts_safe = (record.get("timestamp") or "unknown").replace(":", "-")
        mlflow.log_text(
            json.dumps(record, indent=2, default=str),
            f"kronos-error-{ts_safe}.json",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("error_log MLflow emit failed: %s", e)


# ══════════════════════════════════════════════════════════════
# ── PUBLIC: read_recent (for /errors/recent endpoint) ─────────
# ══════════════════════════════════════════════════════════════

def read_recent(limit: int = 50) -> list[dict]:
    """
    Return the most recent N records from the active JSONL file.

    Reads ONLY the active file — rotated archives are not paged through.
    Cap of _READ_RECENT_CAP applies regardless of the caller's limit so
    a misconfigured endpoint can't be used to dump the whole file.
    """
    try:
        n = max(1, min(int(limit), _READ_RECENT_CAP))
    except (TypeError, ValueError):
        n = 50

    path = _log_path()
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception as e:  # noqa: BLE001
        log.warning("error_log read_recent failed: %s", e)
        return []

    out: list[dict] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
