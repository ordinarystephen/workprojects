# ── KRONOS · pipeline/templates/lending.py ────────────────────
# Lending workbook template.
#
# Describes the Power BI Lending export: every column we recognize,
# its role, and any role-specific metadata (numerator denominators,
# horizontal portfolio triggers, hierarchy levels).
#
# Source of truth for what a "Lending" upload must look like.
# ──────────────────────────────────────────────────────────────

from .base import FieldSpec, Template


class LendingTemplate(Template):
    NAME = "lending"

    # ── Classifier signature ──────────────────────────────────
    # Smallest set of columns the classifier needs to recognize a
    # Lending sheet. Picked to be unique to this template — these
    # three together will never appear on a Traded Products export.
    SIGNATURE = {
        "Facility ID",
        "Weighted Average PD Numerator",
        "Committed Exposure",
    }

    # ── Hierarchy ─────────────────────────────────────────────
    # Top → bottom. Attribution rolls contributions UP this chain;
    # row identity is the LAST entry.
    HIERARCHY = [
        "Ultimate Parent Code",  # 0 — top of relationship
        "Partner Code",          # 1 — borrowing entity
        "Facility ID",           # 2 — row key
    ]

    # ── Field specs ───────────────────────────────────────────
    FIELDS: dict[str, FieldSpec] = {

        # ── Period ────────────────────────────────────────────
        "Month End": FieldSpec(role="period"),

        # ── Hierarchy / entities ──────────────────────────────
        "Ultimate Parent Code": FieldSpec(role="entity_id", hierarchy_level=0),
        "Ultimate Parent Name": FieldSpec(role="borrower_name"),
        "Partner Code":         FieldSpec(role="entity_id", hierarchy_level=1),
        "Partner Name":         FieldSpec(role="passthrough"),
        "Facility ID":          FieldSpec(role="entity_id", hierarchy_level=2),
        "Facility Name":        FieldSpec(role="passthrough"),

        # ── Ratings ───────────────────────────────────────────
        # PD Rating is on the internal C-scale (C00..CDF). Comparison /
        # MoM-direction logic lives in pipeline/scales/pd_scale.py.
        "PD Rating": FieldSpec(role="rating", scale="pd_scale"),

        # Regulatory rating uses the OCC scale, can be split (e.g.
        # "SS - 18%, D - 42%, L - 40%"). Parser lives in
        # pipeline/parsers/regulatory_rating.py.
        "Regulatory Rating": FieldSpec(role="rating", scale="reg_scale"),

        # ── Categorical dimensions (bucketable) ───────────────
        "Risk Assessment Industry":     FieldSpec(role="categorical_dim"),
        "Portfolio Segment Description":FieldSpec(role="categorical_dim"),
        "UBS Branch Name":              FieldSpec(role="categorical_dim"),

        # ── Pass-through industry / metadata ──────────────────
        "Reporting Sector Industry Name": FieldSpec(role="passthrough"),
        "Subsector Industry Name":        FieldSpec(role="passthrough"),
        "NACE Code":                      FieldSpec(role="passthrough"),
        "Letter of Credit Fronting Flag": FieldSpec(role="passthrough"),
        "Credit Officer":                 FieldSpec(role="passthrough"),
        "Current Approval ID":            FieldSpec(role="passthrough"),
        "Approval Date":                  FieldSpec(role="passthrough"),
        "Maturity Date":                  FieldSpec(role="passthrough"),

        # ── Per-row LGD (used for facility-level MoM detection) ──
        "Loss Given Default (LGD)": FieldSpec(role="passthrough", coerce_numeric=True),

        # ── Horizontal portfolios ─────────────────────────────
        # Trigger value = the cell value that means "in the portfolio".
        # Watchlist is intentionally NOT a horizontal — it's surfaced as
        # a firm-level aggregate tile only.
        "Credit Watch List Flag": FieldSpec(role="passthrough"),
        "Leveraged Finance Flag": FieldSpec(
            role="horizontal_flag",
            trigger_value="Y",
            portfolio_name="Leveraged Finance",
        ),
        "Global Recovery Management Flag": FieldSpec(
            role="horizontal_flag",
            trigger_value="Directly Managed",
            portfolio_name="Global Recovery Management",
        ),

        # ── Stock numerics (sum within period, latest across periods) ──
        "Approved Limit":                  FieldSpec(role="stock", coerce_numeric=True),
        "Committed Exposure":              FieldSpec(role="stock", coerce_numeric=True),
        "Outstanding Exposure":            FieldSpec(role="stock", coerce_numeric=True),
        "Temporary Exposure":              FieldSpec(role="stock", coerce_numeric=True),
        "Take & Hold Exposure":            FieldSpec(role="stock", coerce_numeric=True),
        "Pass Rated Exposure":             FieldSpec(role="stock", coerce_numeric=True),
        "Special Mention Rated Exposure":  FieldSpec(role="stock", coerce_numeric=True),
        "Substandard Rated Exposure":      FieldSpec(role="stock", coerce_numeric=True),
        "Doubtful Rated Exposure":         FieldSpec(role="stock", coerce_numeric=True),
        "Loss Rated Exposure":             FieldSpec(role="stock", coerce_numeric=True),
        "No Regulatory Rating Exposure":   FieldSpec(role="stock", coerce_numeric=True),

        # ── Weighted-average numerators ───────────────────────
        # Sum these within any grouping, divide by the declared denominator
        # at presentation time. PD numerator → maps through pd_scale to a
        # rating code; LGD numerator → displayed as XX.XX%.
        "Weighted Average PD Numerator": FieldSpec(
            role="numerator",
            coerce_numeric=True,
            denominator="Committed Exposure",
            scale="pd_scale",
            display="rating_code",
        ),
        "Weighted Average LGD Numerator": FieldSpec(
            role="numerator",
            coerce_numeric=True,
            denominator="Committed Exposure",
            scale=None,
            display="percent_2dp",
        ),
    }

    REQUIRED = set(FIELDS.keys())


# Module-level alias for convenient registration with the classifier.
TEMPLATE = LendingTemplate
