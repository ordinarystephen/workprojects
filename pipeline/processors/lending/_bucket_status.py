# ── KRONOS · pipeline/processors/lending/_bucket_status.py ────
# Bucket lifecycle helpers shared across Lending slicers.
#
# After the `_grouping_history` pinning fix (pipeline/cube/lending.py),
# a GroupingHistory can carry three distinct lifecycle shapes:
#
#   1. Active:   current.period == latest AND current.totals.committed
#                may be non-zero. history[-1].period == latest.
#   2. Exited:   current.period == latest, current.totals.committed == 0,
#                but history is non-empty with earlier-period blocks.
#                history[-1].period != latest.
#   3. New:      current.period == latest with real exposure, and
#                history contains only the latest-period block. Only
#                meaningful when the upload has ≥ 2 periods.
#
# Slicers use these helpers to (a) decorate rendered labels with
# "(exited)" / "(new this period)" markers and (b) sort exposure-ranked
# lists with exited buckets pushed to the bottom. The markers live in
# rendered context and metrics only — verifiable_values keys stay plain
# per the Option-A design (display decoration, not data).
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date

from pipeline.cube.models import GroupingHistory


def is_exited(grouping: GroupingHistory) -> bool:
    """True iff the bucket had rows in a prior period but none in latest.

    Detected via period mismatch between the last actual-data block in
    ``history`` and the pinned ``current``. Committed-is-zero alone is
    not a sufficient signal — an active bucket can legitimately have
    zero committed exposure (e.g. all facilities paid down).
    """
    if not grouping.history:
        return False
    return grouping.history[-1].period != grouping.current.period


def is_new(grouping: GroupingHistory, cube_periods: list[date]) -> bool:
    """True iff the bucket appears for the first time in the latest period.

    Per the item-5 refinement: guard against the reappearance edge case
    (a bucket that existed in P1, was absent in P2, returned in P3)
    being misclassified as new. The condition ``cube_periods[-2] not in
    history periods`` is logically implied by ``len(history) == 1 AND
    history[0].period == latest_period`` under the current history
    assembly (which appends a block per period that has rows), but it's
    kept explicitly so a future change to ``_grouping_history`` can't
    silently break the classification.

    Returns False on single-period uploads — every bucket would look
    new otherwise.
    """
    if len(cube_periods) < 2:
        return False
    if not grouping.history:
        return False
    latest_period = cube_periods[-1]
    if grouping.history[-1].period != latest_period:
        return False
    if len(grouping.history) != 1:
        return False
    if cube_periods[-2] in [block.period for block in grouping.history]:
        return False
    return True


def status_marker(grouping: GroupingHistory, cube_periods: list[date]) -> str:
    """Suffix to append to a bucket's rendered name. Empty for active buckets.

    Exits take priority over "new" (the two conditions are mutually
    exclusive by construction — exit requires history[-1].period !=
    latest, new requires history[-1].period == latest — but the
    priority is spelled out here so a future logic change can't produce
    both markers on the same bucket).
    """
    if is_exited(grouping):
        return " (exited)"
    if is_new(grouping, cube_periods):
        return " (new this period)"
    return ""


def decorate(name: str, grouping: GroupingHistory, cube_periods: list[date]) -> str:
    """Return the bucket name with the appropriate lifecycle suffix."""
    return f"{name}{status_marker(grouping, cube_periods)}"


def sort_key(name: str, grouping: GroupingHistory) -> tuple:
    """Sort key for exposure-ranked bucket lists.

    Ordering:
      1. Active and new buckets interleave by descending committed.
      2. Exited buckets sink to the bottom as a group.
      3. Ties break by bucket name ascending.

    "New this period" buckets are NOT floated to the top (per item-7
    refinement) — a new $50M bucket is less analytically important than
    an active $500M one. The marker makes newness visible; ranking
    reflects magnitude.
    """
    exited_tier = 1 if is_exited(grouping) else 0
    committed = grouping.current.totals.committed
    return (exited_tier, -committed, name)
