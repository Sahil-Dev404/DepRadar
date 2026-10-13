"""Point-in-time ground truth risk label construction from OSV advisories."""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_snapshot_labels(
    nodes_df: pd.DataFrame,
    advisories_df: pd.DataFrame,
    T: pd.Timestamp,
    horizon_months: int = 6,
) -> pd.DataFrame:
    """Compute 6-month risk labels and prior advisory indicators for snapshot T.

    Parameters
    ----------
    nodes_df : pd.DataFrame
        Nodes DataFrame containing ['package', 'idx'].
    advisories_df : pd.DataFrame
        OSV advisories table with ['package', 'published', 'kind', 'withdrawn'].
    T : pd.Timestamp
        Snapshot timestamp (UTC).
    horizon_months : int
        Observation horizon (default 6 months).

    Returns
    -------
    pd.DataFrame
        Columns: ['package', 'idx', 'label', 'had_prior_advisory', 'n_adv_in_window']
    """
    # Filter to active vulnerabilities
    is_vuln = (
        (advisories_df["kind"] == "vulnerability")
        & advisories_df["withdrawn"].isna()
        & advisories_df["published"].notna()
    )
    v_adv = advisories_df[is_vuln]

    # Ensure T is UTC timestamp
    if T.tzinfo is None:
        T = T.tz_localize("UTC")

    window_end = T + pd.DateOffset(months=horizon_months)

    # Prior advisories: published <= T
    prior_adv = v_adv[v_adv["published"] <= T]
    prior_pkgs = set(prior_adv["package"])

    # Future window advisories: T < published <= window_end
    win_adv = v_adv[(v_adv["published"] > T) & (v_adv["published"] <= window_end)]
    win_counts = win_adv["package"].value_counts().to_dict()

    pkgs = nodes_df["package"].values
    labels = np.array([1 if p in win_counts else 0 for p in pkgs], dtype=np.int64)
    had_prior = np.array([p in prior_pkgs for p in pkgs], dtype=bool)
    n_in_win = np.array([win_counts.get(p, 0) for p in pkgs], dtype=np.int64)

    return pd.DataFrame({
        "package": pkgs,
        "idx": nodes_df["idx"].values,
        "label": labels,
        "had_prior_advisory": had_prior,
        "n_adv_in_window": n_in_win,
    })
