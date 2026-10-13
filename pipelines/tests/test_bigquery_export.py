"""Unit tests for Stage 2 BigQuery SQL generation and Stage 3 universe selection."""

import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pandas as pd
import pytest

from pipelines.bigquery_export.export_dependents_by_T import build_dependents_sql
from pipelines.bigquery_export.select_universe import select_universe
from pipelines.common.bq import check_sql_allowed


FORBIDDEN_TABLES = ["Dependencies", "DependencyGraphEdges", "Dependents"]


@pytest.fixture
def sample_grid() -> pd.DataFrame:
    """Create a sample snapshot grid with 5 training dates plus NOW."""
    t_dates = pd.date_range("2024-01-01", periods=5, freq="30D", tz="UTC")
    rows = []
    for i, t in enumerate(t_dates):
        rows.append({
            "target_month": f"2024-0{i+1}",
            "T": t,
            "window_end": t + pd.DateOffset(months=6),
            "is_now": False,
        })
    rows.append({
        "target_month": "NOW",
        "T": pd.Timestamp("2026-10-05 21:01:00", tz="UTC"),
        "window_end": pd.NaT,
        "is_now": True,
    })
    return pd.DataFrame(rows)


def test_generated_sql_contains_one_countif_per_grid_date(sample_grid):
    sql = build_dependents_sql(sample_grid)
    training_dates_count = len(sample_grid[~sample_grid["is_now"]])
    
    assert sql.count("COUNTIF") == training_dates_count
    for i in range(training_dates_count):
        assert f"AS dependents_t{i:02d}" in sql
    assert "dependents_latest" in sql


def test_generated_sql_passes_forbidden_tables_guard(sample_grid):
    sql = build_dependents_sql(sample_grid)
    # Should not raise ValueError
    check_sql_allowed(sql, FORBIDDEN_TABLES)


def test_select_universe_picks_top_n_and_candidate_union(sample_grid):
    # Create mock dependents data for 5 packages
    data = {
        "package": ["express", "lodash", "react", "vue", "chalk"],
        "dependents_latest": [100, 200, 150, 80, 50],
        "dependents_t00": [10, 50, 0, 5, 2],
        "dependents_t01": [15, 60, 20, 10, 0],
        "dependents_t02": [20, 70, 30, 12, 4],
        "dependents_t03": [25, 80, 40, 15, 6],
        "dependents_t04": [30, 90, 50, 18, 8],
    }
    deps_df = pd.DataFrame(data)
    
    # Select top 2 packages per snapshot
    candidates, by_t = select_universe(deps_df, sample_grid, top_n=2)
    
    assert len(candidates) > 0
    assert "package" in candidates.columns
    # Lodash and express/react should dominate the top 2
    assert "lodash" in set(candidates["package"])
    
    # Check universe_by_t structure
    assert set(by_t.columns) == {"target_month", "T", "package", "dependents", "rank"}
    # Rank should be <= 2
    assert (by_t["rank"] <= 2).all()
