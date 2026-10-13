"""Export declared dependents count per package across all grid snapshot dates."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import yaml

from pipelines.common.bq import find_project_root, guarded_query_to_parquet


def build_dependents_sql(grid: pd.DataFrame) -> str:
    """Generate BigQuery SQL for dependents per package at each snapshot date."""
    training_grid = grid[~grid["is_now"]].reset_index(drop=True)
    now_row = grid[grid["is_now"]]
    latest_t = now_row["T"].iloc[0] if len(now_row) > 0 else grid["T"].max()
    t_latest_str = latest_t.strftime("%Y-%m-%d %H:%M:%S.%f+00")

    countif_lines = []
    for i, row in training_grid.iterrows():
        t_str = row["T"].strftime("%Y-%m-%d %H:%M:%S.%f+00")
        col_name = f"dependents_t{i:02d}"
        countif_lines.append(f"       COUNTIF(first_declared <= TIMESTAMP('{t_str}')) AS {col_name}")

    countif_block = ",\n".join(countif_lines)
    if countif_block:
        countif_block = ",\n" + countif_block

    return f"""WITH pairs AS (
  SELECT dep.Name AS dep_name, r.Name AS dependent, MIN(pv.UpstreamPublishedAt) AS first_declared
  FROM `bigquery-public-data.deps_dev_v1.NPMRequirements` AS r
  JOIN `bigquery-public-data.deps_dev_v1.PackageVersions` AS pv
    ON pv.Name = r.Name AND pv.Version = r.Version
  CROSS JOIN UNNEST(r.Dependencies) AS dep
  WHERE r.SnapshotAt  = TIMESTAMP('{t_latest_str}')
    AND pv.SnapshotAt = TIMESTAMP('{t_latest_str}')
    AND pv.System = 'NPM'
  GROUP BY dep_name, dependent
)
SELECT dep_name AS package,
       COUNT(*) AS dependents_latest{countif_block}
FROM pairs
GROUP BY dep_name
HAVING COUNT(*) >= 3"""


def main(argv: list[str] | None = None) -> dict:
    """Load grid, build SQL, dry-run or execute query to dependents_by_T.parquet."""
    root = find_project_root()
    parser = argparse.ArgumentParser(description="Export dependents count per package per snapshot date.")
    parser.add_argument("--config", default="ml/configs/data.yaml", help="Path to data config YAML")
    parser.add_argument("--execute", action="store_true", help="Execute query (default: dry run only)")
    args = parser.parse_args(argv)

    cfg_file = Path(args.config)
    if not cfg_file.is_absolute():
        cfg_file = root / cfg_file

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    grid_rel = cfg.get("paths", {}).get("grid_out", "data/interim/snapshot_grid.parquet")
    out_rel = cfg.get("paths", {}).get("dependents", "data/raw/deps_dev/dependents_by_T.parquet")

    grid = pd.read_parquet(root / grid_rel)
    sql = build_dependents_sql(grid)
    out_path = root / out_rel

    return guarded_query_to_parquet(
        sql,
        out_path,
        execute=args.execute,
        label="export_dependents_by_T",
    )


if __name__ == "__main__":
    main()
