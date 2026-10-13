"""Tests for EDA graph outputs, figures, and reconciliation checks."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from pipelines.common.bq import find_project_root


def test_eda_graph_summary_and_figures():
    root = find_project_root()
    summary_path = root / "data" / "interim" / "eda_graph_summary.json"
    fig_dir = root / "docs" / "figures" / "eda_graph"

    if not summary_path.exists():
        pytest.skip("eda_graph_summary.json has not been generated yet")

    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check required top-level keys
    for k in ["overview", "homophily", "auc_diagnostics", "coverage", "lockfiles", "results"]:
        assert k in data, f"Missing key '{k}' in eda_graph_summary.json"

    # Verify reconciliation checks in results
    results = data["results"]
    assert len(results) > 0, "No reconciliation checks recorded in results"
    failures = [r for r in results if not r.get("ok")]
    assert len(failures) == 0, f"Reconciliation checks failed: {failures}"

    # Verify all 13 figures are present and non-empty
    figures = sorted(fig_dir.glob("fig_*.png"))
    assert len(figures) == 13, f"Expected 13 figures, found {len(figures)}"
    for fig in figures:
        assert fig.stat().st_size > 1000, f"Figure {fig.name} is too small ({fig.stat().st_size} bytes)"
