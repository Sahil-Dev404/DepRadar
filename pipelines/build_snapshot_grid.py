"""Build monthly snapshot grid from deps.dev snapshots list."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import yaml

from pipelines.common.bq import atomic_write_parquet, find_project_root


def monthly_grid(snap_times, first="2023-01", last="2026-04", horizon=6):
    """One real snapshot per target month: the snapshot closest to the 1st of that month.
    Drops dates whose label window (T + horizon months) ends after the latest snapshot."""
    snaps = pd.Series(pd.to_datetime(snap_times, utc=True)).sort_values().reset_index(drop=True)
    latest = snaps.max()
    rows = []
    for month in pd.period_range(first, last, freq="M"):
        target = pd.Timestamp(month.start_time, tz="UTC")
        T = snaps.iloc[(snaps - target).abs().argmin()]
        rows.append({"target_month": str(month), "T": T})
    g = pd.DataFrame(rows).drop_duplicates("T").reset_index(drop=True)
    g["window_end"] = g["T"] + pd.DateOffset(months=horizon)
    return g[g["window_end"] <= latest].reset_index(drop=True)


def add_now_row(grid: pd.DataFrame, snap_times) -> pd.DataFrame:
    """Append a NOW row representing the latest snapshot for live scoring."""
    g = grid.copy()
    if "is_now" not in g.columns:
        g["is_now"] = False
    snaps = pd.Series(pd.to_datetime(snap_times, utc=True))
    latest = snaps.max()
    now_row = pd.DataFrame([{"target_month": "NOW", "T": latest, "window_end": pd.NaT, "is_now": True}])
    res = pd.concat([g, now_row], ignore_index=True)
    res["T"] = pd.to_datetime(res["T"], utc=True)
    res["window_end"] = pd.to_datetime(res["window_end"], utc=True)
    return res


def main(config_path: str | Path | None = None) -> None:
    """Load config, build snapshot grid, write to parquet, and print summary."""
    root = find_project_root()
    if config_path is None:
        parser = argparse.ArgumentParser(description="Build snapshot grid from deps.dev snapshots.")
        parser.add_argument("--config", default="ml/configs/data.yaml", help="Path to data config YAML")
        args = parser.parse_args()
        cfg_arg = args.config
    else:
        cfg_arg = config_path

    cfg_file = Path(cfg_arg)
    if not cfg_file.is_absolute():
        cfg_file = root / cfg_file

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    first = cfg.get("grid", {}).get("first_target_month", "2023-01")
    last = cfg.get("grid", {}).get("last_target_month", "2026-04")
    horizon = int(cfg.get("grid", {}).get("horizon_months", 6))
    snaps_rel = cfg.get("paths", {}).get("snapshots", "data/raw/deps_dev/snapshots.parquet")
    out_rel = cfg.get("paths", {}).get("grid_out", "data/interim/snapshot_grid.parquet")

    snaps_path = root / snaps_rel
    out_path = root / out_rel

    snaps_df = pd.read_parquet(snaps_path)
    grid = monthly_grid(snaps_df["Time"], first=first, last=last, horizon=horizon)
    full_grid = add_now_row(grid, snaps_df["Time"])

    atomic_write_parquet(full_grid, out_path)

    print(full_grid.to_string(index=False))

    training = full_grid[~full_grid["is_now"]]
    first_t = training["T"].iloc[0]
    last_t = training["T"].iloc[-1]
    now_t = full_grid.loc[full_grid["is_now"], "T"].iloc[0]
    print(f"Training dates: {len(training)}, first T: {first_t}, last T: {last_t}, NOW: {now_t}")


if __name__ == "__main__":
    main()
