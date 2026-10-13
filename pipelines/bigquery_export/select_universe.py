"""Select top N package universe per snapshot date and compute candidate union."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import yaml

from pipelines.common.bq import atomic_write_parquet, find_project_root


def select_universe(
    dependents_df: pd.DataFrame,
    grid: pd.DataFrame,
    top_n: int = 30000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select top_n packages by dependents at each T and compute union candidates."""
    training_grid = grid[~grid["is_now"]].reset_index(drop=True)
    universe_by_t_rows: list[dict] = []
    candidates_set: set[str] = set()

    for i, row in training_grid.iterrows():
        col_name = f"dependents_t{i:02d}"
        if col_name not in dependents_df.columns:
            continue
        valid = dependents_df[dependents_df[col_name] > 0]
        top = valid.sort_values(by=[col_name, "package"], ascending=[False, True]).head(top_n)
        for rank, (_, pkg_row) in enumerate(top.iterrows(), start=1):
            pkg = pkg_row["package"]
            candidates_set.add(pkg)
            universe_by_t_rows.append({
                "target_month": row["target_month"],
                "T": row["T"],
                "package": pkg,
                "dependents": int(pkg_row[col_name]),
                "rank": rank,
            })

    if "dependents_latest" in dependents_df.columns:
        valid_now = dependents_df[dependents_df["dependents_latest"] > 0]
        top_now = valid_now.sort_values(by=["dependents_latest", "package"], ascending=[False, True]).head(top_n)
        now_row = grid[grid["is_now"]]
        now_t = now_row["T"].iloc[0] if len(now_row) > 0 else grid["T"].max()
        for rank, (_, pkg_row) in enumerate(top_now.iterrows(), start=1):
            pkg = pkg_row["package"]
            candidates_set.add(pkg)
            universe_by_t_rows.append({
                "target_month": "NOW",
                "T": now_t,
                "package": pkg,
                "dependents": int(pkg_row["dependents_latest"]),
                "rank": rank,
            })

    universe_candidates = pd.DataFrame({"package": sorted(list(candidates_set))})
    universe_by_t = pd.DataFrame(universe_by_t_rows)
    return universe_candidates, universe_by_t


def main(argv: list[str] | None = None) -> None:
    """Load dependents, compute universe, and atomically write candidate parquets."""
    root = find_project_root()
    parser = argparse.ArgumentParser(description="Select top N packages universe.")
    parser.add_argument("--config", default="ml/configs/data.yaml", help="Path to config YAML")
    args = parser.parse_args(argv)

    cfg_file = Path(args.config)
    if not cfg_file.is_absolute():
        cfg_file = root / cfg_file

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    top_n = int(cfg.get("universe", {}).get("top_n", 30000))
    grid_rel = cfg.get("paths", {}).get("grid_out", "data/interim/snapshot_grid.parquet")
    deps_rel = cfg.get("paths", {}).get("dependents", "data/raw/deps_dev/dependents_by_T.parquet")
    cand_rel = cfg.get("paths", {}).get("universe_candidates", "data/interim/universe_candidates.parquet")
    by_t_rel = cfg.get("paths", {}).get("universe_by_T", "data/interim/universe_by_T.parquet")

    grid = pd.read_parquet(root / grid_rel)
    deps_df = pd.read_parquet(root / deps_rel)

    candidates, by_t = select_universe(deps_df, grid, top_n=top_n)

    atomic_write_parquet(candidates, root / cand_rel)
    atomic_write_parquet(by_t, root / by_t_rel)

    print(f"Selected universe candidates: {len(candidates):,} packages across {len(grid)} snapshots.")


if __name__ == "__main__":
    main()
