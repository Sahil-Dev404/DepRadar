"""Export declared dependencies for versions of universe packages."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import yaml
from google.cloud import bigquery

from pipelines.common.bq import find_project_root, guarded_query_to_parquet


def build_version_deps_sql(latest_t_str: str) -> str:
    """Generate SQL for declared dependencies between universe candidates."""
    return f"""SELECT r.Name AS name, r.Version AS version, dep.Name AS dep_name, dep.Requirement AS requirement
FROM `bigquery-public-data.deps_dev_v1.NPMRequirements` AS r, UNNEST(r.Dependencies) AS dep
WHERE r.SnapshotAt = TIMESTAMP('{latest_t_str}') AND r.Name IN UNNEST(@names) AND dep.Name IN UNNEST(@names)"""


def main(argv: list[str] | None = None) -> dict:
    """Run dry-run or execute export for version_deps.parquet."""
    root = find_project_root()
    parser = argparse.ArgumentParser(description="Export version dependencies for universe candidates.")
    parser.add_argument("--config", default="ml/configs/data.yaml", help="Path to config YAML")
    parser.add_argument("--execute", action="store_true", help="Execute query")
    args = parser.parse_args(argv)

    cfg_file = Path(args.config)
    if not cfg_file.is_absolute():
        cfg_file = root / cfg_file

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    grid_rel = cfg.get("paths", {}).get("grid_out", "data/interim/snapshot_grid.parquet")
    cand_rel = cfg.get("paths", {}).get("universe_candidates", "data/interim/universe_candidates.parquet")
    out_path = root / "data" / "raw" / "deps_dev" / "version_deps.parquet"

    grid = pd.read_parquet(root / grid_rel)
    cands = pd.read_parquet(root / cand_rel)
    names = cands["package"].tolist()

    latest_t = grid.loc[grid["is_now"], "T"].iloc[0] if (grid["is_now"]).any() else grid["T"].max()
    t_str = latest_t.strftime("%Y-%m-%d %H:%M:%S.%f+00")

    sql = build_version_deps_sql(t_str)
    params = [bigquery.ArrayQueryParameter("names", "STRING", names)]

    return guarded_query_to_parquet(
        sql,
        out_path,
        params=params,
        execute=args.execute,
        label="export_version_deps",
    )


if __name__ == "__main__":
    main()
