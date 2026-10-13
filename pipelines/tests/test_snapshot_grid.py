"""Tests for monthly snapshot grid generator and NOW row construction."""

import sys
from pathlib import Path

# Ensure project root is on sys.path when pytest is invoked directly
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pandas as pd
import pytest

from pipelines.build_snapshot_grid import add_now_row, monthly_grid


@pytest.fixture
def fake_snapshots() -> pd.Series:
    """Generate fake snapshot timestamps every 7 days."""
    dates = pd.date_range("2022-05-08", "2026-10-05", freq="7D", tz="UTC")
    return pd.Series(dates)


def test_every_t_is_one_of_fake_snapshots(fake_snapshots):
    grid = monthly_grid(fake_snapshots)
    snaps_set = set(fake_snapshots)
    assert all(t in snaps_set for t in grid["T"])


def test_t_values_unique_and_sorted(fake_snapshots):
    grid = monthly_grid(fake_snapshots)
    assert grid["T"].is_monotonic_increasing
    assert grid["T"].is_unique


def test_no_window_end_after_latest_and_last_six_months_excluded(fake_snapshots):
    grid = monthly_grid(fake_snapshots)
    latest = fake_snapshots.max()
    assert (grid["window_end"] <= latest).all()
    target_months = list(grid["target_month"])
    for m in ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]:
        assert m not in target_months


def test_add_now_row(fake_snapshots):
    grid = monthly_grid(fake_snapshots)
    g_now = add_now_row(grid, fake_snapshots)
    now_rows = g_now[g_now["is_now"]]
    assert len(now_rows) == 1
    now_row = now_rows.iloc[0]
    assert now_row["target_month"] == "NOW"
    assert now_row["T"] == fake_snapshots.max()
    assert pd.isna(now_row["window_end"])
    assert (g_now.loc[~g_now["is_now"], "is_now"] == False).all()


def test_month_with_no_snapshot_near_it_maps_and_drops_duplicates():
    sparse = pd.Series([
        pd.Timestamp("2023-01-15", tz="UTC"),
        pd.Timestamp("2024-06-15", tz="UTC"),
    ])
    g = monthly_grid(sparse, first="2023-01", last="2023-04", horizon=6)
    assert len(g) == 1
    assert g["T"].iloc[0] == pd.Timestamp("2023-01-15", tz="UTC")


def test_boundary_exact_same_second_kept():
    t0 = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    t1 = pd.Timestamp("2024-07-01 00:00:00", tz="UTC")
    snaps = pd.Series([t0, t1])
    g = monthly_grid(snaps, first="2024-01", last="2024-01", horizon=6)
    assert len(g) == 1
    assert g["T"].iloc[0] == t0
    assert g["window_end"].iloc[0] == t1


def test_timezone_aware_utc(fake_snapshots):
    grid = monthly_grid(fake_snapshots)
    g_now = add_now_row(grid, fake_snapshots)
    assert grid["T"].dt.tz is not None and str(grid["T"].dt.tz) == "UTC"
    assert grid["window_end"].dt.tz is not None and str(grid["window_end"].dt.tz) == "UTC"
    assert g_now["T"].dt.tz is not None and str(g_now["T"].dt.tz) == "UTC"
    assert g_now["window_end"].dt.tz is not None and str(g_now["window_end"].dt.tz) == "UTC"
