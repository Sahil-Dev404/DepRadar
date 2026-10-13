
from pathlib import Path
from google.cloud import bigquery

PROJECT_ID = "depradar-data"  # Replace with your actual Project ID

client = bigquery.Client(project=PROJECT_ID)

sql = """
SELECT *
FROM `bigquery-public-data.deps_dev_v1.Snapshots`
"""

# Check the amount of data scanned first
dry = client.query(
    sql,
    job_config=bigquery.QueryJobConfig(dry_run=True)
)

mb = dry.total_bytes_processed / 1e6
print(f"Estimated data scanned: {mb:.2f} MB")

# Stop if the scan exceeds 1 GB
if dry.total_bytes_processed > 10**9:
    raise RuntimeError("Query exceeds the 1 GB scan limit.")

# Create the output directory
output = Path("data/raw/deps_dev")
output.mkdir(parents=True, exist_ok=True)

# Execute with a billing cap
cfg = bigquery.QueryJobConfig(
    maximum_bytes_billed=10**9
)

df = client.query(sql, job_config=cfg).to_dataframe()

# Save results locally
file_path = output / "snapshots.parquet"
df.to_parquet(file_path, index=False)

print(f"Saved {len(df)} rows to {file_path}")
print(df.tail())
