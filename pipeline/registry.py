# ── KRONOS · pipeline/registry.py ─────────────────────────────
# YAML-driven mode registry.
#
# Replaces the old MODE_MAP dict that lived in pipeline/analyze.py.
# All mode definitions, parameter schemas, prompt template paths,
# and slicer wirings now live in config/modes.yaml.
#
# Three pieces work together:
#
#   1. ModeDefinition (pydantic, extra="forbid")
#      Schema for one entry in config/modes.yaml. Catches typos at
#      app import — a malformed YAML never reaches a /upload call.
#
#   2. @register_slicer("name", required_params=[...])
#      Decorator slicer modules use to publish themselves into the
#      module-level _SLICERS dict. The registry loader cross-checks
#      that every active mode points at a registered slicer and that
#      the slicer's declared required_params match the mode's
#      required parameters.
#
#   3. load_registry()
#      Called once at server.py import. Parses the YAML, validates
#      every entry, resolves prompt files on disk, and runs the
#      slicer cross-check. Raises RegistryError on any problem.
#      Idempotent — subsequent calls return the cached registry.
#
# Public API:
#   load_registry()                            -> Registry
#   get_mode(slug)                             -> ModeDefinition
#   list_active_modes()                        -> list[ModeDefinition]
#   list_modes_for_ui()                        -> list[ModeDefinition]
#       (active + placeholder, in YAML order — drives the UI grid)
#   resolve_parameter_options(mode, cube)      -> dict[str, list[str]]
#   validate_parameters(mode, params)          -> dict
#       (raises ParameterError on missing/unknown/invalid)
#   register_slicer(name, required_params=[])  -> decorator
#   get_slicer(name)                           -> dict | None
#   load_prompt(mode, parameters={})           -> str
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict


log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# ── Errors ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class RegistryError(RuntimeError):
    """Raised at startup when the registry can't be loaded cleanly."""


class ParameterError(ValueError):
    """Raised at request time when an /upload payload's parameters
    don't match the mode's parameter schema. server.py turns these
    into 400s with the message body."""


# ══════════════════════════════════════════════════════════════
# ── Pydantic schema ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class ParameterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["enum", "string", "integer", "number"]
    source: Optional[str] = None         # e.g. "cube.available_industries" or "cube.available_horizontals"
    values: Optional[list[str]] = None   # static enum
    required: bool = True
    default: Any = None
    display_label: Optional[str] = None


class ModeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    description: str
    user_prompt: str = ""                # default question text for the UI textarea
    parameters: list[ParameterDefinition] = []
    cube_slice: Optional[str] = None     # slicer name; None for pure placeholders
    prompt_template: Optional[str] = None
    status: Literal["active", "placeholder", "disabled"]


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modes: list[ModeDefinition]
    syntheses: list[Any] = []   # reserved
    lengths: list[Any] = []     # reserved


# ══════════════════════════════════════════════════════════════
# ── Slicer registration ───────────────────────────────────────
# ══════════════════════════════════════════════════════════════

# Populated by @register_slicer at import. Cross-checked against
# active mode entries during load_registry().
_SLICERS: dict[str, dict] = {}


def register_slicer(
    name: str, required_params: Optional[list[str]] = None
) -> Callable[[Callable], Callable]:
    """
    Register a slicer function under a stable name. The name is what
    the YAML's `cube_slice:` field references.

    `required_params` lists the kwargs the slicer expects. The
    registry loader compares this list to the mode definition's
    required parameters and raises RegistryError on any mismatch.
    Catches "added a parameter to the YAML but forgot the slicer"
    and "added a parameter to the slicer but forgot the YAML"
    bugs at startup, not at request time.
    """
    req = list(required_params or [])

    def deco(fn: Callable) -> Callable:
        if name in _SLICERS:
            log.warning(
                "Slicer '%s' is being re-registered — overwriting prior fn.", name
            )
        _SLICERS[name] = {"fn": fn, "required_params": req}
        return fn

    return deco


def get_slicer(name: str) -> Optional[dict]:
    return _SLICERS.get(name)


# ══════════════════════════════════════════════════════════════
# ── Loader ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

# Resolve config paths relative to the repo root (registry.py lives
# at pipeline/registry.py; the project root is its parent.parent).
_REPO_ROOT     = Path(__file__).resolve().parent.parent
_MODES_PATH    = _REPO_ROOT / "config" / "modes.yaml"
_PROMPTS_DIR   = _REPO_ROOT / "config" / "prompts"
_DEFAULT_PROMPT = "default.md"

_REGISTRY: Optional[Registry] = None
_BY_SLUG: dict[str, ModeDefinition] = {}


def load_registry() -> Registry:
    """Parse config/modes.yaml, validate, cross-check slicers + prompt
    files. Raises RegistryError on any failure. Idempotent."""
    global _REGISTRY, _BY_SLUG

    if _REGISTRY is not None:
        return _REGISTRY

    # ── Trigger slicer-decorator side effects ──
    # Importing the slicer modules causes their @register_slicer
    # decorators to run, populating _SLICERS. Done lazily inside
    # this function so the registry can be reloaded in tests
    # without import-order surprises.
    import pipeline.processors.lending.firm_level                 # noqa: F401
    import pipeline.processors.lending.portfolio_summary          # noqa: F401
    import pipeline.processors.lending.industry_portfolio_level   # noqa: F401
    import pipeline.processors.lending.horizontal_portfolio_level # noqa: F401

    if not _MODES_PATH.exists():
        raise RegistryError(f"config/modes.yaml not found at {_MODES_PATH}")

    try:
        raw = yaml.safe_load(_MODES_PATH.read_text())
    except yaml.YAMLError as e:
        raise RegistryError(f"config/modes.yaml is not valid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise RegistryError(
            "config/modes.yaml top-level must be a mapping with a 'modes' key."
        )

    try:
        registry = Registry.model_validate(raw)
    except Exception as e:
        raise RegistryError(f"config/modes.yaml failed schema validation: {e}") from e

    # Slug uniqueness.
    seen: set[str] = set()
    for m in registry.modes:
        if m.slug in seen:
            raise RegistryError(f"Duplicate mode slug in config/modes.yaml: '{m.slug}'")
        seen.add(m.slug)

    # Per-mode validation.
    for m in registry.modes:
        _validate_mode(m)

    _REGISTRY = registry
    _BY_SLUG = {m.slug: m for m in registry.modes}
    log.info(
        "Mode registry loaded: %d modes (%d active, %d placeholder).",
        len(registry.modes),
        sum(1 for m in registry.modes if m.status == "active"),
        sum(1 for m in registry.modes if m.status == "placeholder"),
    )
    return registry


def _validate_mode(mode: ModeDefinition) -> None:
    """Per-mode startup check. Raises RegistryError on any problem."""

    # Parameter sanity: source vs values mutual exclusion.
    for p in mode.parameters:
        if p.type == "enum" and not (p.source or p.values):
            raise RegistryError(
                f"Mode '{mode.slug}' parameter '{p.name}' is enum but has neither "
                "`source` nor `values`."
            )
        if p.source and p.values:
            raise RegistryError(
                f"Mode '{mode.slug}' parameter '{p.name}' has both `source` and "
                "`values` — pick one."
            )
        if p.source and not p.source.startswith("cube."):
            raise RegistryError(
                f"Mode '{mode.slug}' parameter '{p.name}' has unsupported source "
                f"'{p.source}' — only 'cube.<field>' is recognized in V1."
            )
        if not p.required and p.default is None and p.type != "enum":
            # Optional non-enum without a default is suspicious but not fatal.
            log.warning(
                "Mode '%s' parameter '%s' is optional with no default.",
                mode.slug, p.name,
            )

    if mode.status != "active":
        # Placeholders / disabled modes don't need the slicer + prompt
        # cross-checks — they exist only to surface the button in the UI.
        return

    # Active modes must point at a registered slicer.
    if not mode.cube_slice:
        raise RegistryError(
            f"Active mode '{mode.slug}' has no cube_slice."
        )
    slicer = _SLICERS.get(mode.cube_slice)
    if slicer is None:
        raise RegistryError(
            f"Active mode '{mode.slug}' references slicer '{mode.cube_slice}' "
            f"which is not registered. Registered: {sorted(_SLICERS.keys())}"
        )
    # Required-param symmetry.
    mode_required = sorted(p.name for p in mode.parameters if p.required)
    slicer_required = sorted(slicer["required_params"])
    if mode_required != slicer_required:
        raise RegistryError(
            f"Mode '{mode.slug}' required parameters {mode_required} do not "
            f"match slicer '{mode.cube_slice}' required_params {slicer_required}."
        )

    # Active modes must point at a prompt file that actually exists.
    if not mode.prompt_template:
        raise RegistryError(
            f"Active mode '{mode.slug}' has no prompt_template."
        )
    prompt_path = _PROMPTS_DIR / mode.prompt_template
    if not prompt_path.exists():
        raise RegistryError(
            f"Active mode '{mode.slug}' references prompt '{mode.prompt_template}' "
            f"which does not exist at {prompt_path}."
        )


# ══════════════════════════════════════════════════════════════
# ── Public lookup API ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def get_mode(slug: str) -> Optional[ModeDefinition]:
    if _REGISTRY is None:
        load_registry()
    return _BY_SLUG.get(slug)


def list_active_modes() -> list[ModeDefinition]:
    if _REGISTRY is None:
        load_registry()
    return [m for m in _REGISTRY.modes if m.status == "active"]


def list_modes_for_ui() -> list[ModeDefinition]:
    """Modes the frontend should render as buttons. Includes placeholders
    so users see future capability; the /upload route returns a clear
    `mode_not_implemented` for those."""
    if _REGISTRY is None:
        load_registry()
    return [m for m in _REGISTRY.modes if m.status in ("active", "placeholder")]


# ══════════════════════════════════════════════════════════════
# ── Parameter resolution ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def _resolve_cube_field(cube: Any, dotted: str) -> Any:
    """Walk dotted attribute path from a cube. e.g. 'cube.available_industries'."""
    if not dotted.startswith("cube."):
        raise ParameterError(f"Unsupported parameter source: {dotted}")
    rest = dotted[len("cube."):]
    obj: Any = cube
    for part in rest.split("."):
        if hasattr(obj, part):
            obj = getattr(obj, part)
        elif isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            raise ParameterError(
                f"Cube does not expose '{dotted}' (failed at '{part}')."
            )
    return obj


def resolve_parameter_options(mode: ModeDefinition, cube: Any) -> dict[str, list[str]]:
    """For each enum parameter, return its allowed values, resolving
    `source: cube.<field>` against the supplied cube. Static `values:`
    enums are returned as-is. Non-enum params are skipped."""
    out: dict[str, list[str]] = {}
    for p in mode.parameters:
        if p.type != "enum":
            continue
        if p.values is not None:
            out[p.name] = list(p.values)
            continue
        if p.source:
            resolved = _resolve_cube_field(cube, p.source)
            if hasattr(resolved, "keys"):
                resolved = list(resolved.keys())
            out[p.name] = sorted(str(v) for v in resolved)
    return out


def validate_parameters(
    mode: ModeDefinition,
    params: dict,
    cube: Optional[Any] = None,
) -> dict:
    """Validate caller-supplied parameters against the mode definition.
    Returns the cleaned dict (defaults applied, types coerced). Raises
    ParameterError on any problem.

    If `cube` is supplied, enum parameters with `source: cube.<field>`
    are checked against the resolved option list. If `cube` is None,
    enum membership is skipped (used for prompt rendering when the
    cube isn't yet computed)."""
    if not isinstance(params, dict):
        raise ParameterError("`parameters` must be an object.")

    declared = {p.name for p in mode.parameters}
    unknown = set(params.keys()) - declared
    if unknown:
        raise ParameterError(
            f"Mode '{mode.slug}' does not accept parameters: {sorted(unknown)}"
        )

    cleaned: dict = {}
    for p in mode.parameters:
        if p.name not in params or params[p.name] in (None, ""):
            if p.required:
                raise ParameterError(
                    f"Mode '{mode.slug}' requires parameter '{p.name}'."
                )
            if p.default is not None:
                cleaned[p.name] = p.default
            continue

        raw = params[p.name]

        # Type coercion + enum membership.
        if p.type == "string":
            cleaned[p.name] = str(raw)
        elif p.type == "integer":
            try:
                cleaned[p.name] = int(raw)
            except (TypeError, ValueError) as e:
                raise ParameterError(
                    f"Parameter '{p.name}' must be an integer (got {raw!r})."
                ) from e
        elif p.type == "number":
            try:
                cleaned[p.name] = float(raw)
            except (TypeError, ValueError) as e:
                raise ParameterError(
                    f"Parameter '{p.name}' must be a number (got {raw!r})."
                ) from e
        elif p.type == "enum":
            value = str(raw)
            allowed: Optional[list[str]] = None
            if p.values is not None:
                allowed = [str(v) for v in p.values]
            elif p.source and cube is not None:
                allowed = resolve_parameter_options(mode, cube).get(p.name, [])
            if allowed is not None and value not in allowed:
                raise ParameterError(
                    f"Parameter '{p.name}' value {value!r} is not one of the "
                    f"allowed options for this upload."
                )
            cleaned[p.name] = value
        else:
            cleaned[p.name] = raw  # unreachable given Literal type, defensive

    return cleaned


# ══════════════════════════════════════════════════════════════
# ── Prompt loading + parameter substitution ───────────────────
# ══════════════════════════════════════════════════════════════

def load_prompt(mode: Optional[ModeDefinition], parameters: Optional[dict] = None) -> str:
    """Read the prompt template for a mode and substitute parameter values.

    If `mode` is None or has no `prompt_template`, falls back to
    config/prompts/default.md. Substitution is plain `{{name}}` →
    `value` text replacement, no Jinja, no expressions."""
    if _REGISTRY is None:
        load_registry()

    template_name: str
    if mode is not None and mode.prompt_template:
        template_name = mode.prompt_template
    else:
        template_name = _DEFAULT_PROMPT

    path = _PROMPTS_DIR / template_name
    if not path.exists():
        # Active modes are pre-validated; only reachable for a placeholder
        # mode that points at a missing prompt. Fall back rather than crash.
        log.warning("Prompt template '%s' not found; using default.", template_name)
        path = _PROMPTS_DIR / _DEFAULT_PROMPT

    text = path.read_text()
    for k, v in (parameters or {}).items():
        text = text.replace("{{" + k + "}}", str(v))
    return text
