# 03 - Data Specification

Everything in section 2-3 was **observed by running notebook `01_data_feasibility.ipynb`** on the project owner's machine (2026-10-12). Items marked **UNVERIFIED** were not tested.

## 1. Sources

| Source | What | Access | Terms |
|---|---|---|---|
| OSV npm advisories | Vulnerability and malware reports with affected ranges, dates, severity | Downloaded once: `https://storage.googleapis.com/osv-vulnerabilities/npm/all.zip` -> `data/raw/osv/npm_all.zip` (do not unzip) | Per-source licences (CC-BY 4.0, CC0, MIT...). Credit OSV and its sources in the README |
| deps.dev on BigQuery | Package versions, publish dates, declared dependencies, project stats | Dataset `bigquery-public-data.deps_dev_v1`; BigQuery sandbox project `depradar-data` (project ID may differ; use `GCP_PROJECT_ID`) | Generated data CC-BY 4.0: credit deps.dev in the README |
| deps.dev snapshot list | 230 snapshot timestamps | `data/raw/deps_dev/snapshots.parquet` (column `Time`) | same |
| Sample lockfiles | 5 real `package-lock.json` files | `data/sample_lockfiles/` | public GitHub repos; record origin URLs in `data/sample_lockfiles/README.md` |

OpenSSF Scorecard: **not used** (the BigQuery dataset is discontinued; no bulk history). May be shown as a display-only extra later.

## 2. Verified facts (do not contradict)

### 2.1 Snapshots
- 230 snapshots, 2022-05-08 02:05 UTC to 2026-10-05 21:01 UTC. Gap: min 1 day, median 7, max 17.
- Partitions that exist (day partitions on `SnapshotAt`): `PackageVersions` 232 (20220508-20261005), `Advisories` 234, `Projects` 234, `PackageVersionToProject` 233. **`NPMRequirements`: only 40 partitions, 20260105-20261005.**
- Consequence: the declared-dependency table has no data for 2022-2025 snapshots (dry run returned 0.00 GB for 2024-01-01, 2024-07-01, 2024-12-30, 2025-06-30, 2025-12-29). The graph at any past `T` is **reconstructed** (section 5.2).

### 2.2 BigQuery tables (all partitioned by DAY on `SnapshotAt`; no partition filter required)
| Table | Rows | Size (GB, all snapshots) | Clustering | Use |
|---|---|---|---|---|
| NPMRequirements | 2.9B | 1,406.5 | Name, Version | declared deps of every npm version (latest snapshot only needed) |
| PackageVersions | 22.1B | 12,794.6 | System, Name | versions + `UpstreamPublishedAt` |
| PackageVersionToProject | 21.6B | 2,553.3 | System, Name, Version | package -> repository |
| Projects | 861M | 99.5 | Type, Name | stars/forks/open issues per snapshot |
| Advisories | 18.9M | 17.7 | Source, SourceID | deps.dev's copy of advisories (cross-check) |
| **Dependencies, DependencyGraphEdges, Dependents** | 1.26T / 2.71T / 1.08T | 111,084 / 341,964 / 96,708 | - | **NEVER QUERY** (one scan exceeds the free monthly quota) |

Columns (observed):
- `NPMRequirements`: SnapshotAt, Name, Version, Dependencies (REPEATED RECORD: Name, Requirement), DevDependencies (same), OptionalDependencies, PeerDependencies, BundledDependencies, Bundles, PeerDependencyMetadata, OS, CPU. (No `System` column: it is npm-only.)
- `PackageVersions`: SnapshotAt, System, Name, Version, Licenses, Links, Advisories (REPEATED: Source, SourceID), VersionInfo (RECORD: IsRelease BOOL, Ordinal INT), Hashes, DependenciesProcessed, DependencyError, UpstreamPublishedAt TIMESTAMP, Registries, SLSAProvenance, UpstreamIdentifiers, Purl, Attestations, Description, Deprecated, ProjectStatus, ProjectStatusReason. System value for npm is `'NPM'`.
- `Advisories`: SnapshotAt, Source, SourceID, SourceURL, Title, Description, ReferenceURLs, CVSS3Score, Severity, GitHubSeverity, Disclosed TIMESTAMP, Packages (REPEATED: System, Name, AffectedVersions, UnaffectedVersions), Aliases.
- `Projects`: SnapshotAt, Type, Name, OpenIssuesCount, StarsCount, ForksCount, Licenses, Description, Homepage, OSSFuzz.
- `PackageVersionToProject`: SnapshotAt, System, Name, Version, ProjectType, ProjectName, RelationProvenance, RelationType.

### 2.3 Dry-run costs measured (GB scanned, one partition)
`NPMRequirements` latest: (Name, Version) 3.75; (Name, Version, Dependencies) 17.14; "dependents per package" aggregate 11.16. `PackageVersions` latest, NPM, (Name, Version, UpstreamPublishedAt) 4.76. `Advisories` latest, all columns 0.29. `Projects` one snapshot, 5 columns 0.27. `PackageVersionToProject` one snapshot, NPM, 4 columns 6.04. Free monthly quota is about 1,100 GB (1 TiB). Clustering already reduces cost when filtering on Name/System.

### 2.4 OSV npm file
- 230,262 JSON files -> 233,041 (advisory x package) rows; 225,731 distinct packages; 0 unparseable.
- By id prefix: `MAL` 222,425 (malware reports), `GHSA` 10,614, `EEF` 1, `GSD` 1. Rule: `kind = 'malware'` if id starts `MAL-` else `'vulnerability'` (10,616 rows).
- Withdrawn: 778 rows (exclude from labels and from matching).
- **Timestamps are in mixed ISO formats** (with/without fractional seconds, up to nanoseconds). Parse with `pd.to_datetime(..., utc=True, format='ISO8601')`. Default parsing silently turns most values into NaT. (This bug already happened once.)
- Vulnerability advisories published per year (distinct, non-withdrawn): 2017: 46, 2018: 295, 2019: 358, 2020: 910, 2021: 578, 2022: 651, 2023: 387, 2024: 420, 2025: 606, **2026: 3,187**.

### 2.5 The 2026 surge (distribution shift - handle explicitly)
Distinct vulnerability advisories per month: 2025-08 49, 2025-09 95, 2025-10 51, 2025-11 55, 2025-12 70, 2026-01 136, 2026-02 262, **2026-03 603**, 2026-04 455, 2026-05 361, 2026-06 339, 2026-07 325, 2026-08 182, 2026-09 291, 2026-10 233 (partial month). Sustained, not a one-off spike. Severity mix in 2026 is similar to 2024-25. In 2026, 4,729 advisory rows cover 998 distinct packages; the top 20 packages hold 43.6% of rows (openclaw 590, n8n 333, parse-server 155, electron 155, next 92, flowise 81, axios 76, undici 69, vm2 66, ghost 50 ...). Packages whose first-ever advisory is in 2026: 734 (vs 324 in 2025). The cause is **not established**. Consequence: base rates in test windows from late 2025 are several times higher than in 2023-2024 (04_ML_SPEC section 5).

### 2.6 Label counts from OSV only (packages anywhere in npm, before restricting to the universe)
Vulnerabilities, window `(T, T+6 months]`:

| T | Advisories in window | Positive packages | First-time positives | Packages with any advisory <= T |
|---|---|---|---|---|
| 2024-01-01 | 217 | 180 | 128 | 2,375 |
| 2024-07-01 | 203 | 162 | 108 | 2,503 |
| 2024-12-30 | 233 | 204 | 132 | 2,611 |
| 2025-06-30 | 370 | 299 | 191 | 2,743 |
| 2025-12-29 | 2,161 | 716 | 496 | 2,931 |

Malware: 3,942 / 4,371 / 4,969 / **186,340** / 5,437 reports per same windows; every malware package has exactly one report (no history) -> excluded from labels.

### 2.7 Sample lockfiles
| File | lockfileVersion | Packages | dev-only | Size KB |
|---|---|---|---|---|
| arcane-demo.json | 3 | 205 | 152 | 102 |
| digitalgov.json | **2** | 999 | 272 | 805 |
| expressjs-website.json | 3 | 937 | 432 | 474 |
| modernweb.json | 3 | 3,078 | 890 | 1,450 |
| wger.json | 3 | 232 | 227 | 117 |
All have a `packages` map. `modernweb` is the rendering stress test. `wger` is almost entirely dev-only (poor demo in production-only mode).

### 2.8 Go / no-go decision (notebook 01)
**GO for the ML component with these adjustments**: monthly snapshot grid instead of five dates; graph reconstructed from the latest snapshot; embargoed temporal splits; explicit handling of the 2026 surge; positives re-counted after restricting to the universe (expect fewer than section 2.6). Open items: universe-restricted positive counts; whether a graph model beats the neighbour-aggregate baseline.

## 3. Local folder layout

```
data/
  raw/
    README.md                       download dates + query text for reproducibility
    osv/npm_all.zip
    deps_dev/
      snapshots.parquet
      snapshot_grid.parquet         monthly T grid (stage 1)
      dependents_by_T.parquet
      package_versions.parquet
      version_deps.parquet
      package_project.parquet
      project_stats_by_T.parquet
      advisories_depsdev_latest.parquet   (optional cross-check)
  interim/
    advisories_osv.parquet          (exists, produced by notebook 01 Part B)
    universe_candidates.parquet
    universe_by_T.parquet
  processed/
    T=YYYY-MM-DD/{nodes,edges,features,labels}.parquet
    T=NOW/{nodes,edges,features}.parquet     features at latest snapshot, no labels
  sample_lockfiles/
```

## 4. Pipeline stages (offline) - run in order, each idempotent

Common rules: use `pipelines/common/bq.py` helper that (1) dry-runs, (2) prints GB, (3) refuses if over `BQ_MAX_BYTES_BILLED`, (4) runs with `maximum_bytes_billed`, (5) writes Parquet atomically (temp file then rename), (6) appends the SQL and timestamp to `data/raw/README.md`. Timestamps in SQL are written as `TIMESTAMP('YYYY-MM-DD HH:MM:SS.ffffff+00')` using exact snapshot values from `snapshots.parquet`.

| # | Stage (script) | Input | Output | Est. GB scanned |
|---|---|---|---|---|
| 1 | `build_snapshot_grid` (local) | snapshots.parquet | `snapshot_grid.parquet`: idx, T (timestamp), split_name; monthly, snapshot closest to the 1st, 2023-01 .. 2026-04; **drop any T with T+6 months > latest snapshot** (-> about 40 dates); also row `NOW` = latest | 0 |
| 2 | `export_dependents_by_T` | BigQuery | `dependents_by_T.parquet` | ~25 (UNVERIFIED) |
| 3 | `select_universe` (local) | stage 2 | `universe_candidates.parquet`, `universe_by_T.parquet` | 0 |
| 4 | `export_package_versions` | BigQuery + universe | `package_versions.parquet` | <= 5 |
| 5 | `export_version_deps` | BigQuery + universe | `version_deps.parquet` | <= 17 |
| 6 | `export_package_project` | BigQuery + universe | `package_project.parquet` | <= 6 |
| 7 | `export_project_stats_by_T` | BigQuery + grid | `project_stats_by_T.parquet` | ~0.3 x #T (about 12) |
| 8 | `export_advisories_depsdev` (optional) | BigQuery | `advisories_depsdev_latest.parquet` | 0.3 |
| 9 | `ingest_osv` | OSV zip | `interim/advisories_osv.parquet` | 0 |
| 10 | `build_snapshots` (local) | all above | `processed/T=.../*.parquet` | 0 |

Estimated total scan: under about 70 GB (about 6% of the monthly free quota). Treat as an estimate; the dry runs decide.

### Stage 2 - Dependents per package at every T (Specification)
Counts, for each package `P` and each snapshot `T`, the number of distinct packages that had **declared `P` as a production dependency in some version published on or before `T`**.
- **Source Tables**: `bigquery-public-data.deps_dev_v1.NPMRequirements` (aliased `r`) joined with `PackageVersions` (aliased `pv`) on `Name` and `Version` at `SnapshotAt = T_LATEST`.
- **Relationship**: Unnest `r.Dependencies` to extract `dep.Name AS dep_name` and `r.Name AS dependent`.
- **Look-Ahead Prevention**: Compute `first_declared = MIN(pv.UpstreamPublishedAt)` for each `(dep_name, dependent)` pair.
- **Aggregation**: For each grid date `T_xx`, compute `COUNTIF(first_declared <= T_xx)` to determine `dependents_txx`. Include overall `COUNT(*) AS dependents_latest`. Filter with `HAVING COUNT(*) >= 3`.
- **Operational Rule**: If BigQuery resource limits occur, partition the date columns across queries. Perform dry runs first with `maximum_bytes_billed` protection.

### Stage 3 - Universe Selection
For each grid `T`: `universe_T` = top **N = 30,000** packages ranked by `dependents_tXX` among packages with at least one release version published on or before `T`.
- `universe_candidates` = union of `universe_T` across all `T` (tens of thousands of unique packages).
- **Selection-Bias Rule**: Ranking is strictly computed *as of T* to prevent look-ahead bias.

### Stage 4 - Package Versions (Specification)
Extract package releases for all `universe_candidates`:
- **Source Table**: `bigquery-public-data.deps_dev_v1.PackageVersions` at `SnapshotAt = T_LATEST`.
- **Filter**: `System = 'NPM'` and `Name IN UNNEST(@universe_candidates)`.
- **Extracted Fields**: `name`, `version`, `published_at` (`UpstreamPublishedAt`), `is_release` (`VersionInfo.IsRelease`), and `ordinal` (`VersionInfo.Ordinal`).
- **Strict Leakage Prevention**: Do **not** select mutable current-state fields (such as `Deprecated` or `ProjectStatus`), which leak future state.

### Stage 5 - Version Dependencies (Specification)
Extract resolved direct dependencies for each version:
- **Source Table**: `bigquery-public-data.deps_dev_v1.NPMRequirements` at `SnapshotAt = T_LATEST`.
- **Filter**: `r.Name IN UNNEST(@universe_candidates)` and `dep.Name IN UNNEST(@universe_candidates)`.
- **Extracted Fields**: `name`, `version`, `dep_name` (`dep.Name`), and `requirement` (`dep.Requirement`). Production dependencies only.

### Stage 6 - Package to Repository Linkage (Specification)
Map packages to source repository identifiers:
- **Source Table**: `bigquery-public-data.deps_dev_v1.PackageVersionToProject` at `SnapshotAt = T_LATEST`.
- **Filter**: `System = 'NPM'` and `Name IN UNNEST(@universe_candidates)`.
- **Extracted Fields**: `name`, `version`, `project_type`, `project_name`, `relation_type`, `relation_provenance`.
- **Processing Rule**: Filter for source repository relation provenance; map each package from its highest-`ordinal` release version to produce `package_project.parquet(name, project_type, project_name)`.

### Stage 7 - Repository Metrics at Each T (Specification)
Extract historical repository activity metrics as of each snapshot:
- **Source Table**: `Projects` snapshot at `SnapshotAt = T`.
- **Filter**: `Type = 'GITHUB'` and `Name IN UNNEST(@project_names)`.
- **Extracted Fields**: `project_type`, `project_name`, `stars` (`StarsCount`), `forks` (`ForksCount`), `open_issues` (`OpenIssuesCount`).
- **Temporal Integrity**: Historical snapshots preserve exact metric values as of `T` without look-ahead leakage.

### Stage 9 - OSV ingest
Already implemented in notebook 01 Part B: move to `pipelines/osv_ingest/ingest_osv.py`. Output columns: `osv_id, package, published, modified, withdrawn, severity, aliases (JSON string), ranges_json (JSON string), versions_json (JSON string), kind`. Exclude rows with `withdrawn` set when labelling and when serving matches. Record the download date in `data/raw/README.md` and in `meta.osv_data_date`.

## 5. Point-in-time reconstruction (stage 10: `build_snapshots`)

### 5.1 Rules
For each grid date `T` (and `NOW`):
1. `universe_T` as in stage 3.
2. **Version visibility**: a version is visible at `T` iff `published_at <= T` and `is_release`.
3. **Package graph**: for each package in `universe_T`, take its **latest visible version by `published_at`** (tie-break `ordinal`); edges `package -> dep_name` are that version's rows in `version_deps` where `dep_name` is in `universe_T`. Edges are directed `src depends_on dst`; the model adds reverse edges.
4. **Advisory history** uses OSV rows with `kind='vulnerability'`, `withdrawn` null and `published <= T`.
5. **Labels**: `label = 1` iff the package has an OSV vulnerability (non-withdrawn) with `published` in `(T, T + 6 months]`. `had_prior_advisory`, `n_adv_in_window` stored for analysis. `NOW` has no labels.
6. Match OSV to universe by **package name only** (case-sensitive npm name).

### 5.2 Known limitations (state in the model card)
- Versions removed from the registry after `T` are absent (survivorship).
- Dependents are "ever declared by T", production deps only.
- Withdrawn status is as of the OSV download date.
- Malware excluded from labels; per-window malware floods (e.g. 186,340 reports in one window) mean any malware-related feature must be avoided.

### 5.3 Processed file schemas
- `nodes.parquet`: `package STRING, idx INT` (0..n-1, stable within a T).
- `edges.parquet`: `src INT, dst INT` (idx values; src depends on dst).
- `features.parquet`: one row per package: `package, idx` + feature columns defined in 04_ML_SPEC section 4.
- `labels.parquet`: `package, idx, label INT, had_prior_advisory BOOL, n_adv_in_window INT`.

## 6. Data validation checks (implement as tests / asserts in `build_snapshots`)
- No label uses an advisory with `published <= T`; no feature uses data after `T` (property test: shifting `T` earlier never increases any count-feature).
- `T + 6 months <= latest snapshot` for every labelled `T`.
- Every `edges.src/dst` is a valid node idx; no self-loops; duplicates removed.
- Counts printed per `T`: nodes, edges, positives, first-time positives, base rate. Fail loudly if positives < 30 for a split `T`.

## 7. Credentials and safety
`gcloud auth application-default login` stores credentials on the user's machine. Never place credentials in prompts, notebooks or the repo. Project ID is read from `GCP_PROJECT_ID`.

## 8. Data credits (put in README)
"Dependency and package metadata: deps.dev (Google), CC-BY 4.0. Vulnerability and malware data: OSV (osv.dev) and its upstream sources, per their licences."

## 9. Project-Level Guidelines & Engineering Standards
This project is a **High-Impact Technical Portfolio Project** designed to showcase applied software engineering, graph data science, and applied machine learning skills for job and internship applications.

1. **Target Complexity**: Professional Portfolio Grade — strong fundamentals, rigorous methodology, clean code design, and clear problem-solving, without enterprise over-engineering.
2. **Professional Tone**: Strictly avoid academic/classroom jargon. Use professional software engineering terminology: **"Key Engineering Takeaways"**, **"Technical Insights & Trade-offs"**, **"Production Considerations"**, **"Performance Scorecard"**.
3. **Data Engineering Principles**: Deterministic point-in-time snapshot reconstruction, zero look-ahead leakage, dry-run query cost validation, and strict schema validation across all pipeline stages.
