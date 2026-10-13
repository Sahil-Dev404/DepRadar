# 02 - System Architecture

## 1. Principles
1. **Two worlds, strictly separated**: an *offline* world (data download, feature building, training) and an *online* world (API + UI). The only bridge is versioned artifacts and database tables written offline, read online.
2. **Deterministic core, learned add-on**: parsing, matching and exposure analysis are pure functions with no ML. The risk model only adds a precomputed score.
3. **Simple storage**: files (Parquet) offline; PostgreSQL online; graph algorithms in memory (NetworkX). No graph database.
4. **Honest provenance**: every response/screen can say which data snapshot and model version it used.

## 2. Components and data flow

```
OFFLINE (your machine / Colab)                                  ONLINE
------------------------------------------------------         ---------------------------------------------
 OSV npm all.zip ──► pipelines/osv_ingest ─┐
                                           ├─► data/interim/*.parquet
 BigQuery deps.dev ─► pipelines/bigquery_export ─► data/raw/deps_dev/*.parquet
                                           │
                    pipelines/build_snapshots (graph + features + labels per T)
                                           ▼
                           data/processed/T=<date>/{nodes,edges,features,labels}.parquet
                                           ▼
        notebooks/04-08  ◄──►  ml/features, ml/models/{baselines,gnn}, ml/eval
                                           ▼
                       ml/artifacts/<model_version>/{model files, metrics.json, scores.parquet}
                                           ▼
                       pipelines/load_to_db ───────────────►  PostgreSQL
                                                              advisories, risk_scores, package_stats,
                                                              package_edges, model_runs, meta, analyses
                                                                   ▲
 Browser (React+TS+Cytoscape) ──HTTPS/JSON──► FastAPI ────────────┘
                                              ├─ parsing/lockfile      (pure)
                                              ├─ advisories/matcher    (pure, uses DB rows)
                                              ├─ exposure/{paths,fix_leverage} (pure, NetworkX)
                                              └─ risk/lookup           (DB read)
```

### 2.1 Request path (online)
`POST /api/analyses` -> validate size/JSON/lockfile version -> parse to graph -> fetch advisories for the package names (single SQL `= ANY(names)`) -> match versions -> compute exposure paths + removal leverage -> look up risk scores -> persist a **result** (not the lockfile) with `expires_at` -> return `analysis_id` + summary. Subsequent `GET`s read the stored result. Processing is synchronous; no job queue.

### 2.2 Offline path
Stages and file contracts are in 03_DATA_SPEC. Training protocol is in 04_ML_SPEC. The last stage (`load_to_db`) writes `risk_scores`, `package_stats`, `package_edges`, `model_runs`, `meta`, `advisories`.

## 3. Repository mapping (matches the scaffold)

| Concern | Path |
|---|---|
| API entrypoint | `backend/app/main.py` |
| Routers | `backend/app/routers/{analyses,packages,model}.py` |
| Pydantic schemas | `backend/app/schemas/` |
| Lockfile parsing | `backend/app/parsing/lockfile.py` |
| Advisory matching | `backend/app/advisories/{matcher.py,repository.py}` |
| Exposure engine | `backend/app/exposure/{graph.py,paths.py,fix_leverage.py}` |
| Risk lookup | `backend/app/risk/lookup.py` |
| DB models/session | `backend/app/db/{models.py,session.py}` + Alembic migrations in `backend/alembic/` |
| Settings | `backend/app/config.py` (env vars from `.env`) |
| Frontend | `frontend/src/{pages,components,api,state,types,lib}` |
| ML code | `ml/{features,models,eval,configs}`; artifacts in `ml/artifacts/` |
| Offline scripts | `pipelines/{bigquery_export,osv_ingest,build_snapshots,load_to_db}` |
| Notebooks | `notebooks/01..08` |
| Sample data | `data/sample_lockfiles/`, tests fixtures in `backend/tests/fixtures/` |

**Paths used in these docs that the scaffold does not contain yet (create them when needed):** `backend/alembic/`, `backend/app/samples/`, `backend/app/advisories/repository.py`, `backend/app/exposure/graph.py`, `pipelines/common/bq.py`, `ml/configs/data.yaml`, `ml/configs/splits.yaml`. The scaffold's placeholder `docs/architecture.md` is superseded by this file and may be deleted.

## 4. Technology choices (and why)

| Layer | Choice | Reason |
|---|---|---|
| Frontend | React + TypeScript (Vite), Tailwind, TanStack Query (server state), Zustand (UI state), Cytoscape.js + `cytoscape-fcose` | Graph-first UI; Cytoscape handles thousands of nodes with styling classes; fcose gives readable force layouts |
| Backend | FastAPI (Pydantic v2), SQLAlchemy 2 + Alembic, `networkx`, `semantic-version` | Typed API, simple graph algorithms in memory; npm versions are strict semver |
| DB | PostgreSQL 16 (docker-compose locally) | Relational data, JSONB for flexible fields |
| ML | scikit-learn, LightGBM, PyTorch + PyTorch Geometric | GNN tooling is far better in PyG than TensorFlow; TF/Keras is not used for the GNN |
| Data | pandas + pyarrow (Parquet), `google-cloud-bigquery`, DuckDB optional for ad-hoc local joins | Parquet is compact and typed |
| Tests | pytest, httpx TestClient; Vitest (+ Playwright optional) | |
| CI | GitHub Actions: ruff, pytest, tsc, vitest | |

Versions: pin exact releases that exist and are at least two weeks old; verify against the registries (do not rely on memory).

## 5. Configuration (`.env`; names only in `.env.example`)
`DATABASE_URL`, `MAX_LOCKFILE_MB` (default 10), `ANALYSIS_RETENTION_DAYS` (7), `CORS_ORIGINS`, `RISK_MODEL_VERSION`, `SEVERITY_WEIGHTS` (default CRITICAL=4, HIGH=3, MODERATE=2, LOW=1, unknown=1), `MAX_GRAPH_NODES_RETURNED` (2000), `MAX_PATHS_PER_NODE` (5). Offline: `GCP_PROJECT_ID`, `BQ_MAX_BYTES_BILLED` (default 60e9).

## 6. Error handling and logging
- All API errors use the shape in 05 section 6; validation errors are 4xx with a human-readable `message`; unexpected errors are 500 with a request id and no stack trace.
- Log one structured line per request: `request_id, route, status, duration_ms, n_packages` (never log lockfile contents or package names of uploads).

## 7. Security and privacy
- Treat the upload as untrusted: size cap, `json.loads` only (no code execution), schema checks, node/edge count caps, ignore unknown fields.
- A lockfile may reveal private package names: store **only** the computed result, keep it `ANALYSIS_RETENTION_DAYS`, and say so in the UI. A scheduled/lazy cleanup deletes expired rows.
- CORS restricted to the frontend origin. No cookies/auth in MVP. Rate limiting: UNVERIFIED which approach the chosen host supports; if none, add a simple per-IP limiter and document it.

## 8. Performance budgets (targets to measure, not claims)
- Request path touches the DB with at most 3 queries (advisories by names, risk by names, insert result).
- Dominator computation is near-linear; path enumeration is capped (`MAX_PATHS_PER_NODE`) and uses shortest-first search.
- The graph response is capped at `MAX_GRAPH_NODES_RETURNED`; the UI collapses subtrees (06).
- Inference never runs in the API. Unseen packages show "not scored".

## 9. Deployment (all prices/limits UNVERIFIED - check before relying)
| Part | Option |
|---|---|
| Frontend | Vercel or Cloudflare Pages (static build) |
| API | Docker container on Render / Fly.io / Hugging Face Spaces |
| DB | Neon or Supabase free Postgres. Size note: OSV npm has ~233k rows (95% malware); if free storage is tight, store malware rows compactly (osv_id, package, published, ranges) |
| Model | None at runtime; precomputed scores loaded into Postgres |
| Secrets | Platform environment variables |
| CI/CD | GitHub Actions: lint + tests on every push; deploy on main |

## 10. Data provenance
`meta` table keys: `osv_data_date`, `osv_source`, `deps_dev_snapshot_t_latest`, `risk_model_version`, `risk_scores_computed_at`. The API exposes them at `GET /api/meta`; the footer shows them.

## 11. Key decisions (short ADRs)
1. No graph DB: graphs are per-request (thousands of nodes) or batch-offline; NetworkX in memory suffices.
2. Graph for ML is rebuilt **as of T** from the latest deps.dev dependency snapshot filtered by version publish date, because `NPMRequirements` only exists from 2026-01-05 (03 section 3). Limitation: versions removed from the registry after `T` are absent.
3. Risk scores are precomputed; the API is stateless w.r.t. models.
4. Malware reports are a separate product finding, not model labels.
5. Removal leverage uses dominator analysis (verified equal to brute-force node removal on random graphs with cycles).
