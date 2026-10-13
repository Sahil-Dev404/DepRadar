"""Point-in-time dependency graph reconstruction for a snapshot date T."""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_snapshot_graph(
    universe_packages: list[str] | pd.Series,
    visible_versions: pd.DataFrame,
    version_deps: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconstruct point-in-time package nodes and edges for snapshot T.

    Parameters
    ----------
    universe_packages : list[str] | pd.Series
        Unique package names in universe_T. Order is preserved for node idx.
    visible_versions : pd.DataFrame
        Versions visible at T (published_at <= T and is_release),
        with columns: name, version, published_at, ordinal.
    version_deps : pd.DataFrame
        Declared dependencies with columns: name, version, dep_name.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (nodes_df, edges_df)
        nodes_df: columns ['package', 'idx'] where idx is 0..n-1.
        edges_df: columns ['src', 'dst'] where src depends_on dst.
    """
    # 1. Build nodes DataFrame
    pkg_list = list(universe_packages)
    n = len(pkg_list)
    nodes_df = pd.DataFrame({
        "package": pkg_list,
        "idx": np.arange(n, dtype=np.int64),
    })
    pkg_to_idx = dict(zip(pkg_list, nodes_df["idx"].values))

    # 2. Find latest visible version per package (by published_at, tie-break ordinal)
    # Filter visible versions to universe packages
    u_versions = visible_versions[visible_versions["name"].isin(pkg_to_idx)]
    if u_versions.empty:
        edges_df = pd.DataFrame(columns=["src", "dst"], dtype=np.int64)
        return nodes_df, edges_df

    # Sort by published_at ascending, ordinal ascending so last row is latest
    sorted_v = u_versions.sort_values(
        by=["published_at", "ordinal"],
        ascending=[True, True],
        na_position="first",
    )
    latest_versions = sorted_v.groupby("name", as_index=False).last()[["name", "version"]]

    # 3. Match latest version dependencies
    # Inner join with version_deps on (name, version)
    deps_match = pd.merge(
        latest_versions,
        version_deps[["name", "version", "dep_name"]],
        on=["name", "version"],
        how="inner",
    )

    # 4. Filter dep_name to universe
    deps_match = deps_match[deps_match["dep_name"].isin(pkg_to_idx)]

    if deps_match.empty:
        edges_df = pd.DataFrame(columns=["src", "dst"], dtype=np.int64)
        return nodes_df, edges_df

    # Map name -> src idx, dep_name -> dst idx
    src_indices = deps_match["name"].map(pkg_to_idx).astype(np.int64)
    dst_indices = deps_match["dep_name"].map(pkg_to_idx).astype(np.int64)

    edges_df = pd.DataFrame({"src": src_indices, "dst": dst_indices})

    # 5. Remove self-loops and drop duplicates
    edges_df = edges_df[edges_df["src"] != edges_df["dst"]]
    edges_df = edges_df.drop_duplicates(subset=["src", "dst"]).reset_index(drop=True)

    return nodes_df, edges_df
