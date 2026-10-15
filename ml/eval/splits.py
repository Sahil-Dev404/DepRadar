"""Evaluation split utilities for temporal splits with embargo."""
import pandas as pd

SPLIT_MONTHS = {
    "train": ("2023-01", "2024-01"),
    "val": ("2024-07", "2024-09"),
    "test_a": ("2025-03", "2025-05"),
    "test_b": ("2025-11", "2026-04")
}
HORIZON_MONTHS = 6

def assign_splits(grid, split_months=SPLIT_MONTHS):
    """Assign temporal split tags to the snapshot grid."""
    g = grid.copy()
    g["split"] = None
    for name, (a, b) in split_months.items():
        g.loc[(g.target_month >= a) & (g.target_month <= b), "split"] = name
    return g[g.split.notna()].reset_index(drop=True)

def apply_embargo(g, order=("train", "val", "test_a", "test_b"), horizon=HORIZON_MONTHS):
    """Enforce embargo buffer of `horizon` months between consecutive splits."""
    g = g.copy()
    dropped = []
    for prev, nxt in zip(order, order[1:]):
        if not (g.split == prev).any() or not (g.split == nxt).any():
            continue
        limit = (g.loc[g.split == prev, "T"].max() + pd.DateOffset(months=horizon)).date()
        while (g.split == nxt).any() and g.loc[g.split == nxt, "T"].min().date() <= limit:
            idx = g.loc[g.split == nxt, "T"].idxmin()
            dropped.append(g.loc[idx, "T"])
            g = g.drop(idx)
    return g.reset_index(drop=True), dropped
