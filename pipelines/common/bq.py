"""BigQuery client wrapper, safety guards, and parquet export helpers."""
from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any
import pandas as pd
import yaml

class BytesLimitExceeded(RuntimeError):
    """Raised when BigQuery byte estimate exceeds configured limit."""

def find_project_root(start_path: Path | None = None) -> Path:
    """Find project root directory containing ml/."""
    current = (start_path or Path(__file__)).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "ml").is_dir():
            return parent
    raise RuntimeError("Could not find project root containing ml/")

def _load_config() -> dict[str, Any]:
    """Load ml/configs/data.yaml if present."""
    cfg = find_project_root() / "ml" / "configs" / "data.yaml"
    if cfg.is_file():
        with open(cfg, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def get_client(project: str | None = None) -> Any:
    """Return BigQuery client for target project or GCP_PROJECT_ID."""
    if project is None:
        cfg = _load_config()
        env_var = cfg.get("bigquery", {}).get("project_env", "GCP_PROJECT_ID")
        project = os.environ.get(env_var)
    if project:
        project = project.strip().strip("<>").strip()
    if not project:
        raise RuntimeError(
            "GCP project not configured. Please set GCP_PROJECT_ID and run "
            "'gcloud auth application-default login'."
        )
    from google.cloud import bigquery
    return bigquery.Client(project=project)

def check_sql_allowed(sql: str, forbidden: list[str]) -> None:
    """Raise ValueError if SQL references any forbidden deps_dev_v1 table."""
    if not forbidden:
        return
    names = "|".join(re.escape(name) for name in forbidden)
    pattern = rf"deps_dev_v1`?\s*\.\s*`?(?:{names})\b"
    match = re.search(pattern, sql, re.IGNORECASE)
    if match:
        raise ValueError(f"Forbidden BigQuery table query detected: '{match.group(0)}'")

def dry_run(sql: str, params: list | None = None, client: Any = None) -> int:
    """Perform BigQuery dry-run and return total bytes processed."""
    if client is None:
        client = get_client()
    try:
        from google.cloud import bigquery
        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params or [])
        job = client.query(sql, job_config=cfg)
    except (TypeError, ImportError):
        job = client.query(sql)
    return getattr(job, "total_bytes_processed", 0)

def atomic_write_parquet(df: pd.DataFrame, path: Path | str) -> None:
    """Write DataFrame to Parquet atomically using a temporary file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{target}.tmp")
    try:
        df.to_parquet(tmp_path, index=False, engine="pyarrow")
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

def log_to_readme(
    readme_path: Path | str,
    *,
    label: str,
    sql: str,
    bytes_estimated: int | None,
    bytes_billed: int | None,
    rows: int | None,
    out_path: Path | str,
) -> None:
    """Append query execution details to markdown README log."""
    path = Path(readme_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    est = f"{bytes_estimated / 1e9:.2f} GB" if bytes_estimated is not None else "N/A"
    billed = f"{bytes_billed / 1e9:.2f} GB" if bytes_billed is not None else "N/A"
    block = (
        f"\n## Query: {label} ({ts})\n\n- **Output**: `{out_path}`\n"
        f"- **Estimated**: {est}\n- **Billed**: {billed}\n- **Rows**: {rows if rows is not None else 'N/A'}\n\n"
        f"```sql\n{sql.strip()}\n```\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)

def guarded_query_to_parquet(
    sql: str,
    out_path: Path | str,
    *,
    params: list | None = None,
    client: Any = None,
    max_bytes: int | None = None,
    execute: bool = False,
    label: str = "",
) -> dict[str, Any]:
    """Execute guarded BigQuery query and write output to Parquet."""
    cfg = _load_config()
    forbidden = cfg.get("bigquery", {}).get(
        "forbidden_tables", ["Dependencies", "DependencyGraphEdges", "Dependents"]
    )
    check_sql_allowed(sql, forbidden)
    bytes_estimated = dry_run(sql, params=params, client=client)
    pct = (bytes_estimated / (2**40)) * 100
    lbl = f"{label}: " if label else ""
    print(f"{lbl}estimated {bytes_estimated / 1e9:.2f} GB ({pct:.2f}% of 1 TiB)")

    if max_bytes is not None:
        cap = max_bytes
    else:
        env_var = cfg.get("bigquery", {}).get("max_bytes_billed_env", "BQ_MAX_BYTES_BILLED")
        val = os.environ.get(env_var)
        default_cap = cfg.get("bigquery", {}).get("default_max_bytes_billed", 60_000_000_000)
        cap = int(val) if val is not None else int(default_cap)

    if bytes_estimated > cap:
        raise BytesLimitExceeded(
            f"Estimated query cost ({bytes_estimated} bytes) exceeds cap ({cap} bytes)."
        )
    if not execute:
        print("DRY RUN ONLY - pass execute=True to run")
        return {
            "label": label, "bytes_estimated": bytes_estimated, "bytes_billed": None,
            "rows": None, "out_path": str(out_path), "executed": False,
        }

    if client is None:
        client = get_client()
    try:
        from google.cloud import bigquery
        run_cfg = bigquery.QueryJobConfig(maximum_bytes_billed=cap, query_parameters=params or [])
        job = client.query(sql, job_config=run_cfg)
    except (TypeError, ImportError):
        job = client.query(sql)

    df, bytes_billed = job.to_dataframe(), getattr(job, "total_bytes_billed", None)
    rows = len(df)
    atomic_write_parquet(df, out_path)
    readme_path = find_project_root() / cfg.get("paths", {}).get("raw_readme", "data/raw/README.md")
    log_to_readme(
        readme_path, label=label, sql=sql, bytes_estimated=bytes_estimated,
        bytes_billed=bytes_billed, rows=rows, out_path=out_path,
    )
    billed_str = f"{bytes_billed / 1e9:.2f} GB" if bytes_billed is not None else "N/A"
    print(f"Executed: {rows} rows, {billed_str} billed")
    return {
        "label": label, "bytes_estimated": bytes_estimated, "bytes_billed": bytes_billed,
        "rows": rows, "out_path": str(out_path), "executed": True,
    }
