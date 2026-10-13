"""Point-in-time feature extraction for universe packages at snapshot T."""

from __future__ import annotations

import numpy as np
import pandas as pd


SEVERITY_ORD_MAP = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MODERATE": 2,
    "MEDIUM": 2,
    "LOW": 1,
}


def build_snapshot_features(
    nodes_df: pd.DataFrame,
    edges_df: pd.DataFrame,
    visible_versions: pd.DataFrame,
    dependents_by_T: pd.DataFrame | None,
    advisories_df: pd.DataFrame,
    T: pd.Timestamp,
    t_minus_3m_dependents: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Compute feature vectors for all packages in nodes_df at snapshot T.

    Parameters
    ----------
    nodes_df : pd.DataFrame
        Columns ['package', 'idx'].
    edges_df : pd.DataFrame
        Columns ['src', 'dst'].
    visible_versions : pd.DataFrame
        All versions visible at T (published_at <= T).
    dependents_by_T : pd.DataFrame | None
        Current dependents mapping for packages at T.
    advisories_df : pd.DataFrame
        OSV table with ['package', 'published', 'kind', 'withdrawn', 'severity'].
    T : pd.Timestamp
        Snapshot timestamp.
    t_minus_3m_dependents : dict[str, int] | None
        Optional dictionary of dependents counts from 3 months prior.

    Returns
    -------
    pd.DataFrame
        One row per package with idx and all feature columns.
    """
    n = len(nodes_df)
    pkg_list = nodes_df["package"].tolist()
    pkg_to_idx = dict(zip(pkg_list, nodes_df["idx"].values))

    if T.tzinfo is None:
        T = T.tz_localize("UTC")

    t_ts = T.timestamp()

    # -------------------------------------------------------------
    # 1. Package version features
    # -------------------------------------------------------------
    u_versions = visible_versions[visible_versions["name"].isin(pkg_to_idx)].copy()

    # Default arrays
    age_days = np.full(n, np.nan, dtype=np.float64)
    n_versions = np.zeros(n, dtype=np.float64)
    n_release_versions = np.zeros(n, dtype=np.float64)
    days_since_last_release = np.full(n, np.nan, dtype=np.float64)
    releases_90d = np.zeros(n, dtype=np.float64)
    releases_365d = np.zeros(n, dtype=np.float64)
    median_gap_days_last20 = np.full(n, np.nan, dtype=np.float64)

    if not u_versions.empty:
        # Convert published_at to epoch seconds for fast numeric operations
        u_versions["pub_sec"] = u_versions["published_at"].astype("int64") // 10**9
        t_90_sec = t_ts - (90 * 86400)
        t_365_sec = t_ts - (365 * 86400)

        # Sort for gap calculation and latest release identification
        u_versions = u_versions.sort_values(by=["name", "pub_sec", "ordinal"], ascending=True)

        for name, group in u_versions.groupby("name"):
            idx = pkg_to_idx[name]
            sec_vals = group["pub_sec"].values
            is_rel_vals = group["is_release"].fillna(True).values

            n_versions[idx] = len(sec_vals)
            n_release_versions[idx] = int(is_rel_vals.sum())
            first_sec = sec_vals[0]
            last_sec = sec_vals[-1]
            age_days[idx] = max(0.0, (t_ts - first_sec) / 86400.0)
            days_since_last_release[idx] = max(0.0, (t_ts - last_sec) / 86400.0)

            releases_90d[idx] = int((sec_vals > t_90_sec).sum())
            releases_365d[idx] = int((sec_vals > t_365_sec).sum())

            # Median gap between consecutive releases for last 20 releases
            rel_sec = sec_vals[is_rel_vals] if is_rel_vals.any() else sec_vals
            if len(rel_sec) >= 2:
                last_20 = rel_sec[-20:]
                gaps = np.diff(last_20) / 86400.0
                median_gap_days_last20[idx] = float(np.median(gaps))

    # -------------------------------------------------------------
    # 2. Graph degree features
    # -------------------------------------------------------------
    out_deg = np.zeros(n, dtype=np.float64)
    in_deg = np.zeros(n, dtype=np.float64)
    if not edges_df.empty:
        src_arr = edges_df["src"].values
        dst_arr = edges_df["dst"].values
        out_counts = np.bincount(src_arr, minlength=n)
        in_counts = np.bincount(dst_arr, minlength=n)
        out_deg = out_counts.astype(np.float64)
        in_deg = in_counts.astype(np.float64)

    n_deps_prod = out_deg.copy()
    n_deps_prod_raw = out_deg.copy()

    # -------------------------------------------------------------
    # 3. Popularity features
    # -------------------------------------------------------------
    dep_dict = {}
    if dependents_by_T is not None and not dependents_by_T.empty:
        if "package" in dependents_by_T.columns and "dependents" in dependents_by_T.columns:
            dep_dict = dict(zip(dependents_by_T["package"], dependents_by_T["dependents"]))

    dependents_arr = np.array([dep_dict.get(p, 0) for p in pkg_list], dtype=np.float64)
    log_dependents = np.log1p(dependents_arr)

    dependents_delta_3m = np.full(n, np.nan, dtype=np.float64)
    if t_minus_3m_dependents is not None:
        for p, idx in pkg_to_idx.items():
            if p in t_minus_3m_dependents:
                dependents_delta_3m[idx] = dep_dict.get(p, 0) - t_minus_3m_dependents[p]

    # -------------------------------------------------------------
    # 4. Advisory history features
    # -------------------------------------------------------------
    n_prior_adv = np.zeros(n, dtype=np.float64)
    n_prior_adv_365d = np.zeros(n, dtype=np.float64)
    days_since_last_adv = np.full(n, np.nan, dtype=np.float64)
    has_prior_adv = np.zeros(n, dtype=np.float64)
    max_severity_ord = np.zeros(n, dtype=np.float64)
    n_prior_critical_high = np.zeros(n, dtype=np.float64)

    is_vuln = (
        (advisories_df["kind"] == "vulnerability")
        & advisories_df["withdrawn"].isna()
        & advisories_df["published"].notna()
    )
    v_adv = advisories_df[is_vuln]
    prior_adv = v_adv[v_adv["published"] <= T].copy()

    if not prior_adv.empty:
        prior_adv = prior_adv[prior_adv["package"].isin(pkg_to_idx)]
        if not prior_adv.empty:
            prior_adv["pub_sec"] = prior_adv["published"].astype("int64") // 10**9
            t_365_sec = t_ts - (365 * 86400)
            prior_adv["sev_ord"] = (
                prior_adv["severity"].str.upper().map(SEVERITY_ORD_MAP).fillna(0).astype(int)
            )

            for name, group in prior_adv.groupby("package"):
                idx = pkg_to_idx[name]
                p_secs = group["pub_sec"].values
                n_cnt = len(p_secs)
                n_prior_adv[idx] = n_cnt
                has_prior_adv[idx] = 1.0
                n_prior_adv_365d[idx] = int((p_secs > t_365_sec).sum())
                days_since_last_adv[idx] = max(0.0, (t_ts - np.max(p_secs)) / 86400.0)
                max_severity_ord[idx] = int(group["sev_ord"].max())
                n_prior_critical_high[idx] = int((group["sev_ord"] >= 3).sum())

    # -------------------------------------------------------------
    # 5. Repository features (defaults when Projects table unavailable)
    # -------------------------------------------------------------
    has_repo = np.zeros(n, dtype=np.float64)
    log_stars = np.zeros(n, dtype=np.float64)
    log_forks = np.zeros(n, dtype=np.float64)
    log_open_issues = np.zeros(n, dtype=np.float64)

    # -------------------------------------------------------------
    # 6. Graph neighbor aggregates (M3 features)
    # -------------------------------------------------------------
    nb_out_mean_prior_adv = np.zeros(n, dtype=np.float64)
    nb_out_max_prior_adv = np.zeros(n, dtype=np.float64)
    nb_out_frac_prior_adv = np.zeros(n, dtype=np.float64)
    nb_in_mean_prior_adv = np.zeros(n, dtype=np.float64)
    nb_in_max_prior_adv = np.zeros(n, dtype=np.float64)
    nb_in_frac_prior_adv = np.zeros(n, dtype=np.float64)
    nb_out_mean_log_dependents = np.zeros(n, dtype=np.float64)
    nb_in_mean_log_dependents = np.zeros(n, dtype=np.float64)

    if not edges_df.empty:
        src = edges_df["src"].values
        dst = edges_df["dst"].values

        # Out-neighbor aggregates (src depends on dst)
        dst_prior = n_prior_adv[dst]
        dst_has_prior = has_prior_adv[dst]
        dst_log_deps = log_dependents[dst]

        out_sum_prior = np.bincount(src, weights=dst_prior, minlength=n)
        out_sum_has_prior = np.bincount(src, weights=dst_has_prior, minlength=n)
        out_sum_log_deps = np.bincount(src, weights=dst_log_deps, minlength=n)

        # Vectorized safe division
        mask_out = out_deg > 0
        nb_out_mean_prior_adv[mask_out] = out_sum_prior[mask_out] / out_deg[mask_out]
        nb_out_frac_prior_adv[mask_out] = out_sum_has_prior[mask_out] / out_deg[mask_out]
        nb_out_mean_log_dependents[mask_out] = out_sum_log_deps[mask_out] / out_deg[mask_out]

        # Max over out-neighbors
        df_out_max = pd.DataFrame({"src": src, "val": dst_prior}).groupby("src")["val"].max()
        nb_out_max_prior_adv[df_out_max.index.values] = df_out_max.values

        # In-neighbor aggregates (dst depended on by src)
        src_prior = n_prior_adv[src]
        src_has_prior = has_prior_adv[src]
        src_log_deps = log_dependents[src]

        in_sum_prior = np.bincount(dst, weights=src_prior, minlength=n)
        in_sum_has_prior = np.bincount(dst, weights=src_has_prior, minlength=n)
        in_sum_log_deps = np.bincount(dst, weights=src_log_deps, minlength=n)

        mask_in = in_deg > 0
        nb_in_mean_prior_adv[mask_in] = in_sum_prior[mask_in] / in_deg[mask_in]
        nb_in_frac_prior_adv[mask_in] = in_sum_has_prior[mask_in] / in_deg[mask_in]
        nb_in_mean_log_dependents[mask_in] = in_sum_log_deps[mask_in] / in_deg[mask_in]

        df_in_max = pd.DataFrame({"dst": dst, "val": src_prior}).groupby("dst")["val"].max()
        nb_in_max_prior_adv[df_in_max.index.values] = df_in_max.values

    # Assemble final DataFrame
    features_df = pd.DataFrame({
        "package": pkg_list,
        "idx": nodes_df["idx"].values,
        # Package group
        "age_days": age_days,
        "n_versions": n_versions,
        "n_release_versions": n_release_versions,
        "days_since_last_release": days_since_last_release,
        "releases_90d": releases_90d,
        "releases_365d": releases_365d,
        "median_gap_days_last20": median_gap_days_last20,
        "n_deps_prod": n_deps_prod,
        "n_deps_prod_raw": n_deps_prod_raw,
        # Popularity group
        "log_dependents": log_dependents,
        "dependents_delta_3m": dependents_delta_3m,
        # Advisory history group
        "n_prior_adv": n_prior_adv,
        "n_prior_adv_365d": n_prior_adv_365d,
        "days_since_last_adv": days_since_last_adv,
        "has_prior_adv": has_prior_adv,
        "max_severity_ord": max_severity_ord,
        "n_prior_critical_high": n_prior_critical_high,
        # Repository group
        "has_repo": has_repo,
        "log_stars": log_stars,
        "log_forks": log_forks,
        "log_open_issues": log_open_issues,
        # Graph group
        "out_deg": out_deg,
        "in_deg": in_deg,
        "nb_out_mean_prior_adv": nb_out_mean_prior_adv,
        "nb_out_max_prior_adv": nb_out_max_prior_adv,
        "nb_out_frac_prior_adv": nb_out_frac_prior_adv,
        "nb_in_mean_prior_adv": nb_in_mean_prior_adv,
        "nb_in_max_prior_adv": nb_in_max_prior_adv,
        "nb_in_frac_prior_adv": nb_in_frac_prior_adv,
        "nb_out_mean_log_dependents": nb_out_mean_log_dependents,
        "nb_in_mean_log_dependents": nb_in_mean_log_dependents,
    })

    return features_df
