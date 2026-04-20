# ── KRONOS · pipeline/templates/base.py ───────────────────────
# Template abstraction.
#
# A Template describes the SHAPE of one kind of upload (e.g. a
# Lending workbook export from Power BI, a Traded Products export).
# It owns:
#   - SIGNATURE: minimal distinctive columns the classifier uses
#                to recognize the sheet.
#   - REQUIRED:  full set of columns that must be present once a
#                sheet has been claimed by this template.
#   - FIELDS:    per-column metadata — role tag + extra metadata
#                that drives downstream calculations.
#   - validate(df) -> DataFrame: enforces required columns, coerces
#                dtypes, returns a normalized DataFrame.
#
# Templates do NOT read files. The classifier reads the workbook,
# matches sheets to templates by SIGNATURE, then hands each matched
# sheet's DataFrame to the template's validate() method.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Optional

import pandas as pd


# ── Field roles ───────────────────────────────────────────────
# Every column the cube/calculations care about gets a role.
# Untagged columns survive the validation pass but are ignored by
# the cube computer (they're available downstream as passthrough).
Role = Literal[
    "entity_id",        # stable row key (or hierarchy level above the row key)
    "period",           # snapshot date column (one period per file or many)
    "rating",           # ordinal rating column (PD, regulatory, etc.)
    "borrower_name",    # human display label for an entity
    "categorical_dim",  # bucketable dimension (industry, segment, branch)
    "horizontal_flag",  # boolean-ish flag that defines a horizontal portfolio
    "stock",            # numeric, point-in-time balance — sum within period
    "numerator",        # numeric, contribution to a weighted average
    "passthrough",      # keep but don't aggregate
]


@dataclass(frozen=True)
class FieldSpec:
    """
    Per-column metadata. Attached to a Template's FIELDS dict keyed
    by column name. Unused fields stay at their defaults.
    """
    role: Role

    # Numeric coercion: if True, run pd.to_numeric(errors='coerce').fillna(0).
    coerce_numeric: bool = False

    # ── numerator-only fields ─────────────────────────────────
    denominator: Optional[str] = None
        # Column name that divides this numerator at presentation time.
        # E.g. "Weighted Average PD Numerator" → denominator="Committed Exposure".

    scale: Optional[str] = None
        # Name of a lookup scale used to translate the divided value back
        # to a rating code. Currently supported: "pd_scale", or None.

    display: Optional[str] = None
        # How to render the divided value: "rating_code", "percent_2dp",
        # "decimal_2dp", or None for raw passthrough.

    # ── horizontal_flag-only fields ───────────────────────────
    trigger_value: Optional[str] = None
        # Cell value that means "this row is in the horizontal portfolio".
        # E.g. "Y" for Leveraged Finance Flag, "Directly Managed" for GRM.

    portfolio_name: Optional[str] = None
        # Human-readable name for the horizontal portfolio this flag defines.
        # Surfaces in cube keys (e.g. by_horizontal["Leveraged Finance"]).

    # ── entity_id-only fields ─────────────────────────────────
    hierarchy_level: Optional[int] = None
        # 0 = top of the hierarchy (e.g. Ultimate Parent),
        # increasing numbers go down to the row key (e.g. Facility ID).
        # Used by attribution code to roll contributors up to parent level.


@dataclass(frozen=True)
class ValidationWarning:
    """Non-fatal data-quality observation surfaced alongside the validated frame."""
    code: str
    message: str


class Template:
    """
    Base class for all upload templates. Subclasses set the class-level
    constants and may override validate() if they need custom coercion.

    Subclass responsibilities:
        NAME       — short slug, used in cube keys and metadata
        SIGNATURE  — minimal distinctive column set (used by the classifier)
        REQUIRED   — full set of columns that must be present
        FIELDS     — { column_name: FieldSpec(...) }
        HIERARCHY  — ordered list of entity_id columns (parent → row)
                     Used by attribution and distinct-entity counts.
    """

    NAME: ClassVar[str] = ""
    SIGNATURE: ClassVar[set[str]] = set()
    REQUIRED: ClassVar[set[str]] = set()
    FIELDS: ClassVar[dict[str, FieldSpec]] = {}
    HIERARCHY: ClassVar[list[str]] = []

    # ── Convenience accessors ─────────────────────────────────

    @classmethod
    def fields_with_role(cls, role: Role) -> list[str]:
        """All column names tagged with the given role, in declaration order."""
        return [c for c, spec in cls.FIELDS.items() if spec.role == role]

    @classmethod
    def period_column(cls) -> str:
        cols = cls.fields_with_role("period")
        if len(cols) != 1:
            raise ValueError(
                f"{cls.NAME}: expected exactly one 'period' column, got {cols}"
            )
        return cols[0]

    @classmethod
    def row_key(cls) -> str:
        """The lowest level of the hierarchy — used as the row identity."""
        if not cls.HIERARCHY:
            raise ValueError(f"{cls.NAME}: HIERARCHY is empty")
        return cls.HIERARCHY[-1]

    # ── Validation ────────────────────────────────────────────

    @classmethod
    def validate(cls, df: pd.DataFrame) -> tuple[pd.DataFrame, list[ValidationWarning]]:
        """
        Verify required columns are present and coerce dtypes per FieldSpec.
        Returns the normalized DataFrame plus any non-fatal warnings.

        Raises ValueError on missing required columns or unparseable period.
        """
        warnings: list[ValidationWarning] = []
        cols = set(df.columns)
        missing = cls.REQUIRED - cols
        if missing:
            raise ValueError(
                f"{cls.NAME}: workbook is missing required columns: "
                + ", ".join(sorted(missing))
            )

        out = df.copy()

        # Numeric coercion for any field flagged for it.
        for col, spec in cls.FIELDS.items():
            if spec.coerce_numeric and col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

        # Period parsing.
        period_col = cls.period_column()
        try:
            out[period_col] = pd.to_datetime(out[period_col], errors="raise")
        except Exception as e:
            raise ValueError(
                f"{cls.NAME}: period column '{period_col}' could not be parsed as dates: {e}"
            )

        return out, warnings
