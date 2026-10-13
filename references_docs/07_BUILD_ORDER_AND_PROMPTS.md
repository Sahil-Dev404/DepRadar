# 07 - Build Order and Ready-to-Paste Prompts

Rule of thumb: **one prompt = one module or one pipeline stage**, runnable and tested before the next. Paste the "Context line" at the start of every prompt.

**Context line (paste first, every time):**
> You are building DepRadar. Read `docs/00_README_FOR_AGENTS.md` first and follow its hard rules. Then read only the documents listed in this prompt. Do not modify files outside the listed paths. When finished, list what you did not verify.

## Build order

| Step | Where | Docs | Result |
|---|---|---|---|
| 1 | `pipelines/` data stages 1-9 | 03 | all Parquet files in `data/raw`, `data/interim` |
| 2 | `ml/features` + `build_snapshots` | 03, 04 | `data/processed/T=*/` with validation checks passing |
| 3 | notebooks 02-04 (EDA, surge, features) | 03, 04 | insights; code moved into `ml/` |
| 4 | baselines B0-B3, M1-M3 + eval | 04 | metrics for non-graph models |
| 5 | GNN M4 + controls + ablations | 04 | decision per section 8 of 04 |
| 6 | backend core: parsing -> matcher -> exposure | 05 | tested pure modules (can start in parallel with 1-5) |
| 7 | backend DB + API + loaders | 05, 02 | all endpoints, contract tests |
| 8 | frontend | 06, 05 | workspace, package, ecosystem, model-card pages |
| 9 | deploy + README + demo | 02, 01 | live app, polished repo |

Backend core (step 6) does not depend on the ML work, so it can be built early while data exports run.

## Prompts

### P1 - BigQuery helper and snapshot grid
Read docs 00, 03. Create `pipelines/common/bq.py` (dry run, GB printout, refuse above `BQ_MAX_BYTES_BILLED`, run with `maximum_bytes_billed`, atomic Parquet write, append SQL + timestamp to `data/raw/README.md`) and `pipelines/build_snapshot_grid.py` implementing stage 1 (monthly grid from `data/raw/deps_dev/snapshots.parquet`, drop dates whose 6-month window exceeds the latest snapshot, add a `NOW` row). Add pytest tests for the grid logic with a fake snapshot table. No real BigQuery calls in tests.

### P2 - Export dependents per package
Read docs 00, 03 (sections 2, 4 stage 2-3). Implement `pipelines/bigquery_export/export_dependents_by_T.py` generating the SQL column list from the grid, and `pipelines/bigquery_export/select_universe.py` (stage 3, N from `ml/configs/data.yaml`). First run **only the dry run** and print the GB; do not execute until I confirm. Include a unit test that the generated SQL contains one `COUNTIF` per grid date.

### P3 - Export versions, version deps, project links, project stats
Read docs 00, 03 (stages 4-8). Implement the four export scripts using the helper, with `@names` as query parameters. Dry-run first and print GB for each. For stage 6/7, first add a tiny script that prints the distinct `RelationType`, `RelationProvenance` and `Projects.Type` values so I can choose the right strings (they are UNVERIFIED).

### P4 - OSV ingest
Read docs 00, 03 (section 2.4, stage 9). Move notebook 01 Part B into `pipelines/osv_ingest/ingest_osv.py` with a CLI (`--zip`, `--out`). Keep `format='ISO8601'` date parsing and add a regression test with mixed-format timestamps. Output schema exactly as in 03.

### P5 - Build snapshots (features, labels, graph)
Read docs 00, 03 (section 5-6), 04 (sections 1-4). Implement `ml/features/{build_graph,build_features,build_labels}.py` and `pipelines/build_snapshots/build_snapshots.py` producing `data/processed/T=*/{nodes,edges,features,labels}.parquet`. Include the leakage tests from 04 section 2 and the validation checks from 03 section 6. Print the per-T table (nodes, edges, positives, first-time positives, base rate).

### P6 - Baselines and evaluation
Read docs 00, 04 (sections 3, 6-8). Implement `ml/eval/{splits,metrics,stratified}.py` (embargo logic with tests) and `ml/models/baselines/*` (B0-B3, M1-M3, shuffled-graph control C1). Provide a runnable script that writes `metrics.json` for each model and split. Do not tune on test splits. Do not write any numbers into docs.

### P7 - GNN
Read docs 00, 04 (sections 6.1, 7, 8). Implement `ml/models/gnn/{sage,gat}.py` with PyTorch Geometric: two relation types (depends_on, depended_by), per-T full-batch training, early stopping on validation, 5 seeds, and the shuffled-graph control C2. Output the same `metrics.json` format as P6.

### P8 - Lockfile parser
Read docs 00, 05 (sections 3, 8). Implement `backend/app/parsing/lockfile.py` from the reference in section 3 plus tests with fixtures (create small fixtures; also test the five files in `data/sample_lockfiles/`). Pure functions only.

### P9 - Matcher
Read docs 00, 05 (section 4). Implement `backend/app/advisories/matcher.py` from the reference and the table-driven tests listed. Pin `semantic-version` to a verified version.

### P10 - Exposure engine
Read docs 00, 05 (section 5). Implement `backend/app/exposure/{graph,paths,fix_leverage}.py` from the reference, including the random-graph brute-force property test and the hand-built graphs. Include `MAX_PATHS_PER_NODE` and the `n_direct_parents` computation.

### P11 - API and database
Read docs 00, 02, 05. Implement schemas, routers, DB models + Alembic migration (DDL in 05 section 7), the request pipeline (05 section 2), error handling (05 section 6), and API tests (05 section 8). Use docker-compose Postgres for dev. Do not add authentication or background jobs.

### P12 - Loaders (offline -> DB)
Read docs 00, 03, 05 (section 7). Implement `pipelines/load_to_db/{load_advisories,load_risk_scores,load_package_stats,load_package_edges,set_meta}.py` (idempotent upserts, transaction per table, print row counts). Keep malware rows compact if size is a concern.

### P13 - Frontend shell and upload flow
Read docs 00, 06 (sections 1-4.1, 7), 05 (section 6). Scaffold the app inside `frontend/`, set up Tailwind, router, API client, TanStack Query, Zustand store, types (section 3), Landing page with Dropzone and samples. Add Vitest tests for client error handling and the Dropzone.

### P14 - Analysis workspace and graph
Read docs 00, 06 (sections 4.2, 5, 6, 8, 9). Implement `GraphCanvas` with Cytoscape + fcose, styling classes, path highlighting, leverage panel, node detail panel, filters, truncation/collapse behaviour. Test with the 3,078-package sample and report measured render time.

### P15 - Package, Ecosystem, Model-card pages
Read docs 00, 06 (4.3-4.5), 04 (section 12). Implement the three pages, rendering only what the API returns ("not measured" when absent).

### P16 - Deploy
Read docs 00, 02 (sections 5, 9). Write Dockerfile(s), CI workflow additions (ruff, pytest, tsc, vitest), deployment notes. Verify current free-tier limits of the chosen hosts before relying on them and list what you verified.

## Review prompt (use after any step)
> Review the last change against `docs/00_README_FOR_AGENTS.md` rules 1-10 and the relevant spec sections. List violations, missing tests, anything unverified, and any place the code diverges from the spec. Do not fix anything yet.
