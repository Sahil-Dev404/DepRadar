"""Export package versions for universe candidates at latest snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import yaml
from google.cloud import bigquery

from pipelines.common.bq import find_project_root, guarded_query_to_parquet


def build_package_versions_sql(latest_t_str: str) -> str:
    """Generate SQL for package versions in the universe candidates."""
    return f"""SELECT Name AS name, Version AS version, UpstreamPublishedAt AS published_at,
       VersionInfo.IsRelease AS is_release, VersionInfo.Ordinal AS ordinal
FROM `bigquery-public-data.deps_dev_v1.PackageVersions`
WHERE SnapshotAt = TIMESTAMP('{latest_t_str}') AND System = 'NPM' AND Name IN UNNEST(@names)"""


def main(argv: list[str] | None = None) -> dict:
    """Run dry-run or execute export for package_versions.parquet."""
    root = find_project_root()
    parser = argparse.ArgumentParser(description="Export package versions for universe candidates.")
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
    out_path = root / "data" / "raw" / "deps_dev" / "package_versions.parquet"

    grid = pd.read_parquet(root / grid_rel)
    cands = pd.read_parquet(root / cand_rel)
    names = cands["package"].tolist()

    latest_t = grid.loc[grid["is_now"], "T"].iloc[0] if (grid["is_now"]).any() else grid["T"].max()
    t_str = latest_t.strftime("%Y-%m-%d %H:%M:%S.%f+00")

    sql = build_package_versions_sql(t_str)
    params = [bigquery.ArrayQueryParameter("names", "STRING", names)]

    return guarded_query_to_parquet(
        sql,
        out_path,
        params=params,
        execute=args.execute,
        label="export_package_versions",
    )


if __name__ == "__main__":
    main()
