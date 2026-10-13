"""Tests for BigQuery SQL guards, dry-runs, byte caps, atomic parquet, and readme logging."""

import sys
from pathlib import Path

# Ensure project root is on sys.path when pytest is invoked directly
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pandas as pd
import pytest

from pipelines.common.bq import (
    BytesLimitExceeded,
    atomic_write_parquet,
    check_sql_allowed,
    guarded_query_to_parquet,
    log_to_readme,
)

FORBIDDEN_TABLES = ["Dependencies", "DependencyGraphEdges", "Dependents"]


class FakeQueryJob:
    """Mock BigQuery query job object."""

    def __init__(self, bytes_processed: int, data: pd.DataFrame | None = None):
        self.total_bytes_processed = bytes_processed
        self.total_bytes_billed = bytes_processed
        self._data = data if data is not None else pd.DataFrame({"col": [1, 2, 3]})

    def to_dataframe(self) -> pd.DataFrame:
        return self._data.copy()


class FakeBigQueryClient:
    """Mock BigQuery client tracking call count."""

    def __init__(self, bytes_processed: int = 1000, data: pd.DataFrame | None = None):
        self.bytes_processed = bytes_processed
        self.data = data
        self.call_count = 0
        self.queries_run: list[str] = []

    def query(self, sql: str, job_config=None, **kwargs):
        self.call_count += 1
        self.queries_run.append(sql)
        return FakeQueryJob(self.bytes_processed, self.data)


def test_check_sql_allowed_raises_on_forbidden_tables():
    forbidden_queries = [
        "SELECT * FROM `bigquery-public-data.deps_dev_v1.Dependents`",
        "select * from deps_dev_v1.dependents",
        "SELECT * FROM deps_dev_v1.Dependencies",
        "SELECT * FROM `bigquery-public-data.deps_dev_v1.`DependencyGraphEdges`",
        "SELECT * FROM deps_dev_v1 . Dependencies",
        "SELECT * FROM `deps_dev_v1`.`Dependents`",
    ]
    for q in forbidden_queries:
        with pytest.raises(ValueError, match="Forbidden BigQuery table"):
            check_sql_allowed(q, FORBIDDEN_TABLES)


def test_check_sql_allowed_allows_permitted_queries():
    allowed_queries = [
        "SELECT * FROM `bigquery-public-data.deps_dev_v1.NPMRequirements`",
        "SELECT * FROM deps_dev_v1.PackageVersions",
        "SELECT * FROM deps_dev_v1.Snapshots",
        "SELECT DependentCount FROM deps_dev_v1.PackageVersions",
        "SELECT v.Dependencies FROM deps_dev_v1.NPMRequirements v",
    ]
    for q in allowed_queries:
        check_sql_allowed(q, FORBIDDEN_TABLES)


def test_estimate_above_cap_raises_bytes_limit_exceeded(tmp_path):
    client = FakeBigQueryClient(bytes_processed=70_000_000_000)
    out_file = tmp_path / "out.parquet"

    with pytest.raises(BytesLimitExceeded):
        guarded_query_to_parquet(
            "SELECT * FROM deps_dev_v1.PackageVersions",
            out_file,
            client=client,
            execute=True,
            label="test_cap",
        )

    assert client.call_count == 1
    assert not out_file.exists()


def test_execute_false_nothing_written_nothing_run(tmp_path):
    client = FakeBigQueryClient(bytes_processed=10_000)
    out_file = tmp_path / "out.parquet"

    res = guarded_query_to_parquet(
        "SELECT * FROM deps_dev_v1.PackageVersions",
        out_file,
        client=client,
        execute=False,
        label="dry_run_test",
    )

    assert res["executed"] is False
    assert res["rows"] is None
    assert client.call_count == 1
    assert not out_file.exists()


def test_atomic_write_parquet_success_and_forced_failure(tmp_path, monkeypatch):
    out_file = tmp_path / "data.parquet"
    tmp_file = Path(f"{out_file}.tmp")
    df = pd.DataFrame({"x": [1, 2, 3]})

    atomic_write_parquet(df, out_file)
    assert out_file.exists()
    assert not tmp_file.exists()
    loaded = pd.read_parquet(out_file)
    assert len(loaded) == 3

    def failing_to_parquet(*args, **kwargs):
        tmp_file.write_text("corrupted tmp content")
        raise RuntimeError("Simulated write failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", failing_to_parquet)

    with pytest.raises(RuntimeError, match="Simulated write failure"):
        atomic_write_parquet(df, out_file)

    assert not tmp_file.exists()
    original = pd.read_parquet(out_file)
    assert len(original) == 3


def test_log_to_readme_appends_and_preserves_earlier(tmp_path):
    readme = tmp_path / "README.md"
    initial_text = "# Initial Raw Data Log\n\nExisting contents here.\n"
    readme.write_text(initial_text, encoding="utf-8")

    log_to_readme(
        readme,
        label="Query One",
        sql="SELECT 1",
        bytes_estimated=5_000_000_000,
        bytes_billed=5_000_000_000,
        rows=100,
        out_path="data/q1.parquet",
    )
    log_to_readme(
        readme,
        label="Query Two",
        sql="SELECT 2",
        bytes_estimated=12_000_000_000,
        bytes_billed=12_000_000_000,
        rows=200,
        out_path="data/q2.parquet",
    )

    content = readme.read_text(encoding="utf-8")
    assert "Existing contents here." in content
    assert "Query: Query One" in content
    assert "SELECT 1" in content
    assert "Query: Query Two" in content
    assert "SELECT 2" in content


def test_env_var_cap_parsing_honoured_over_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("BQ_MAX_BYTES_BILLED", "1000")
    client = FakeBigQueryClient(bytes_processed=2000)
    out_file = tmp_path / "out.parquet"

    with pytest.raises(BytesLimitExceeded):
        guarded_query_to_parquet(
            "SELECT * FROM deps_dev_v1.PackageVersions",
            out_file,
            client=client,
            execute=False,
        )

    res = guarded_query_to_parquet(
        "SELECT * FROM deps_dev_v1.PackageVersions",
        out_file,
        client=client,
        max_bytes=5000,
        execute=False,
    )
    assert res["executed"] is False
