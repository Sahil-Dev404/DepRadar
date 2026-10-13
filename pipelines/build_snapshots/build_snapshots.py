"""Build point-in-time snapshot graphs, features, and labels for all grid dates."""

from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

from ml.features.build_graph import build_snapshot_graph
from ml.features.build_features import build_snapshot_features
from ml.features.build_labels import build_snapshot_labels
from pipelines.common.bq import find_project_root


def run_build_snapshots(
    config_path: str = "ml/configs/data.yaml",
    horizon_months: int = 6,
) -> pd.DataFrame:
    """Build processed snapshot directories T=* with nodes, edges, features, labels."""
    root = find_project_root()
    cfg_file = Path(config_path)
    if not cfg_file.is_absolute():
        cfg_file = root / cfg_file

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    grid_rel = cfg.get("paths", {}).get("grid_out", "data/interim/snapshot_grid.parquet")
    u_rel = cfg.get("paths", {}).get("universe_by_T", "data/interim/universe_by_T.parquet")
    v_rel = "data/raw/deps_dev/package_versions.parquet"
    vd_rel = "data/raw/deps_dev/version_deps.parquet"
    adv_rel = "data/interim/advisories_osv.parquet"
    proc_dir = root / "data" / "processed"
    proc_dir.mkdir(parents=True, exist_ok=True)

    print("Loading interim and raw tables...")
    grid = pd.read_parquet(root / grid_rel)
    universe_by_T = pd.read_parquet(root / u_rel)
    package_versions = pd.read_parquet(root / v_rel)
    version_deps = pd.read_parquet(root / vd_rel)
    advisories = pd.read_parquet(root / adv_rel)

    # Sort package versions once for deterministic fast retrieval
    print(f"Sorting {len(package_versions):,} package versions...")
    package_versions = package_versions.sort_values(
        by=["name", "published_at", "ordinal"],
        ascending=[True, True, True],
        na_position="first",
    )

    # Pre-index dependents by snapshot target_month / T
    deps_by_snapshot = {}
    for (t_month, t_val), group in universe_by_T.groupby(["target_month", "T"]):
        deps_by_snapshot[t_month] = dict(zip(group["package"], group["dependents"]))

    # Track prior positives across snapshots for first-time positive counting
    all_prior_positives = set()

    summary_rows = []
    print("\nBeginning snapshot reconstruction across grid dates...")

    # Order grid by T ascending
    grid = grid.sort_values(by="T").reset_index(drop=True)

    for idx, row in grid.iterrows():
        t_val = row["T"]
        is_now = bool(row.get("is_now", False))
        t_month = row["target_month"]

        folder_name = "T=NOW" if is_now else f"T={t_val.strftime('%Y-%m-%d')}"
        target_dir = proc_dir / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # 1. Get universe packages for this snapshot
        u_t = universe_by_T[universe_by_T["target_month"] == t_month].sort_values(by="rank")
        pkg_list = u_t["package"].tolist()
        n = len(pkg_list)

        # 2. Filter visible versions at T (published_at <= T and is_release)
        vis_vers = package_versions[package_versions["published_at"] <= t_val]
        vis_rel = vis_vers[vis_vers["is_release"].fillna(True)]

        # 3. Build graph: nodes and edges
        nodes_df, edges_df = build_snapshot_graph(pkg_list, vis_rel, version_deps)
        m = len(edges_df)

        # Validation assertions
        assert (nodes_df["idx"].values == np.arange(n)).all(), f"Invalid node indices at {folder_name}"
        assert nodes_df["package"].is_unique, f"Duplicate packages in nodes at {folder_name}"
        if m > 0:
            assert ((edges_df["src"] >= 0) & (edges_df["src"] < n)).all(), f"Out of range src at {folder_name}"
            assert ((edges_df["dst"] >= 0) & (edges_df["dst"] < n)).all(), f"Out of range dst at {folder_name}"
            assert (edges_df["src"] != edges_df["dst"]).all(), f"Self loops found at {folder_name}"

        # 4. Build features
        # Find 3-months prior dependents dict if available
        t_minus_3m_dict = None
        if idx >= 3:
            prev_month = grid.iloc[idx - 3]["target_month"]
            t_minus_3m_dict = deps_by_snapshot.get(prev_month)

        features_df = build_snapshot_features(
            nodes_df=nodes_df,
            edges_df=edges_df,
            visible_versions=vis_vers,
            dependents_by_T=u_t,
            advisories_df=advisories,
            T=t_val,
            t_minus_3m_dependents=t_minus_3m_dict,
        )

        # 5. Build labels (for training snapshots)
        positives = 0
        first_time_pos = 0
        base_rate = 0.0

        if not is_now:
            labels_df = build_snapshot_labels(
                nodes_df=nodes_df,
                advisories_df=advisories,
                T=t_val,
                horizon_months=horizon_months,
            )
            assert len(labels_df) == n, f"Labels count mismatch at {folder_name}"
            assert (labels_df["idx"].values == nodes_df["idx"].values).all(), f"Labels idx mismatch at {folder_name}"
            assert (labels_df["package"].values == nodes_df["package"].values).all(), f"Labels pkg mismatch at {folder_name}"

            positives = int(labels_df["label"].sum())
            base_rate = positives / n if n > 0 else 0.0

            # First-time positives: label == 1 and package had no prior advisory
            first_time_mask = (labels_df["label"] == 1) & (~labels_df["had_prior_advisory"])
            first_time_pos = int(first_time_mask.sum())

            # Save labels
            labels_df.to_parquet(target_dir / "labels.parquet", index=False)

        # Save nodes, edges, features
        nodes_df.to_parquet(target_dir / "nodes.parquet", index=False)
        edges_df.to_parquet(target_dir / "edges.parquet", index=False)
        features_df.to_parquet(target_dir / "features.parquet", index=False)

        summary_rows.append({
            "folder": folder_name,
            "target_month": t_month,
            "T": str(t_val.date()),
            "nodes": n,
            "edges": m,
            "positives": positives,
            "first_time_positives": first_time_pos,
            "base_rate": base_rate,
        })

        flag_str = " (FAIL: <30 positives)" if (not is_now and positives < 30) else ""
        print(
            f"[{folder_name:12s}] nodes={n:5d} edges={m:6d} pos={positives:4d} "
            f"first_time={first_time_pos:4d} base_rate={base_rate:.4f}{flag_str}"
        )

    summary_df = pd.DataFrame(summary_rows)
    print("\nReconstruction complete across all snapshots.")
    return summary_df


def main():
    parser = argparse.ArgumentParser(description="Reconstruct point-in-time snapshot graphs, features, and labels.")
    parser.add_argument("--config", default="ml/configs/data.yaml", help="Path to config YAML")
    parser.add_argument("--horizon", type=int, default=6, help="Label window horizon in months")
    args = parser.parse_args()

    run_build_snapshots(config_path=args.config, horizon_months=args.horizon)


if __name__ == "__main__":
    main()
