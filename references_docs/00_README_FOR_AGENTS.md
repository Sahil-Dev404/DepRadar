# 00 - READ THIS FIRST (instructions for coding agents)

You are helping build **DepRadar**: a web product that analyses an npm `package-lock.json`, shows which dependencies expose the project to known vulnerabilities and through which paths, ranks direct dependencies by how much exposure dropping them would remove, and shows a learned "future advisory risk" score per package.

This folder (`reference_docs/`) is the single source of truth. **Read the documents listed for your task before writing code.**

## 1. Which document to read

| Your task | Read |
|---|---|
| Anything | 00 (this file) |
| Understand the product, scope, what NOT to build | 01_PROJECT_SPEC.md |
| System layout, data flow, folders, config, deployment | 02_ARCHITECTURE.md |
| Downloading / transforming data, file schemas, SQL, verified facts about the data | 03_DATA_SPEC.md |
| Labels, features, splits, models, evaluation, GNN | 04_ML_SPEC.md |
| API, lockfile parsing, advisory matching, exposure engine, database | 05_BACKEND_SPEC.md |
| Pages, components, state, graph rendering, TS types | 06_FRONTEND_SPEC.md |

DoNot generate the all the files at once & i will be giving the prompt in the chat so we can build the project step by step 

If two documents disagree: **03_DATA_SPEC "verified facts" wins on anything about data**, 05 wins on API shapes, 04 wins on ML protocol. Report the conflict; do not silently pick.

## 2. Repository map (already scaffolded)

```
depradar/
  frontend/      React + TypeScript (Vite). SEPARATE from backend.
  backend/       FastAPI. app/{routers,schemas,parsing,advisories,exposure,risk,db}, tests/
  notebooks/     EDA + prototyping + training notebooks (01..08). Import code from ml/.
  ml/            Reusable ML code: features/, models/{baselines,gnn}/, eval/, configs/, artifacts/ (git-ignored)
  pipelines/     Offline scripts: bigquery_export/, osv_ingest/, build_snapshots/, load_to_db/
  data/          raw/ interim/ processed/ sample_lockfiles/  (git-ignored except sample_lockfiles)
  refernce_docs/          these documents
```
Notebooks explore; **reusable code lives in `ml/` and `pipelines/`** and is imported by notebooks. Do not put logic only in notebooks.

## 3. Hard rules (never violate)

1. **Never invent numbers.** No accuracy, PR-AUC, latency or user figures unless produced by a real run in this repo. Use placeholders such as `[N]`, `TBD`.
2. **No leakage.** Every feature for snapshot date `T` uses only information available at or before `T`. Splits are by time with an embargo of one label horizon. Random train/test splits are forbidden. (04_ML_SPEC section 3.)
3. **Training and serving are separate.** The API never trains or runs a GNN. It reads precomputed `risk_scores`. (02_ARCHITECTURE.)
4. **Malware reports are not vulnerabilities.** OSV ids starting `MAL-` are malicious-package reports. They are shown as a separate "known malicious" finding and are excluded from risk-model labels.
5. **BigQuery safety.** Every query gets a dry run first and `maximum_bytes_billed` set. Default cap per query: 60 GB. Never `SELECT *` on large tables; always filter on `SnapshotAt`. Never run the tables `Dependencies`, `DependencyGraphEdges`, `Dependents` (hundreds of TB; see 03).
6. **No secrets in the repo.** No credentials, `.env`, or data files committed. Use `.env.example` for names only.
7. **No graph database, no auth, no LLM features, no microservices** in the MVP. (01 section 6.)
8. **Honest UI wording.** "Removal leverage" is an upper bound on benefit, not a guarantee that an upgrade fixes things. The risk score is a prioritisation signal, not a verdict. (01 section 7.)
9. **Unverified items.** Anything marked `UNVERIFIED` in these docs must be checked (docs, a dry run, `npm view`, a quick test) before you rely on it. Say what you checked.
10. **Pin versions.** Add dependencies with exact versions that exist and are at least two weeks old. Verify with the package registry; do not trust memory.

## 4. Engineering conventions

- Python: target 3.11, type hints on public functions, `ruff` clean, `pytest` for tests. Pure functions for parsing/matching/exposure (no I/O inside) so they are trivially testable.
- TypeScript: `strict: true`, no `any` without a comment, one API client module, types mirror 05_BACKEND_SPEC schemas exactly.
- Paths: use `pathlib`; never hard-code absolute paths. Project root is found by walking up to the folder containing `ml/` (see notebook setup cell).
- Parquet for data files, JSON for API, YAML for configs in `ml/configs/`.
- Logging: standard `logging`, structured key=value, no `print` in library code.
- Errors: the API returns the error shape in 05 section 6. Never leak stack traces.
- Every module gets at least one test. Every bug fix gets a regression test.
- Small, reviewable steps: one module (or one pipeline stage) per task, runnable and tested before moving on.

## 5. Definition of done (for any task)

- [ ] Code runs from a clean checkout following the README/dev setup
- [ ] Tests written and passing (`pytest backend/tests ml pipelines` / `npm test` where relevant)
- [ ] Lint/type checks pass (`ruff`, `tsc --noEmit`)
- [ ] No leakage / honesty rules above violated
- [ ] Docs updated if behaviour or schema changed (including this folder)
- [ ] You listed what you did **not** verify

## 6. Glossary

| Term | Meaning |
|---|---|
| Direct dependency | A package listed in the project root's `dependencies` (or `devDependencies` when dev is included) |
| Exposure | A vulnerable package version that is installed in the lockfile and reachable from the root |
| Exposure path | A chain root -> ... -> vulnerable package |
| Removal leverage (fix leverage) | For direct dependency `d`: the vulnerable packages that `d` **dominates** (every root path to them passes through `d`), i.e. would disappear if `d` were dropped. Severity-weighted. |
| Snapshot `T` | A date for which a deps.dev snapshot exists; the point-in-time at which features are computed |
| Horizon | Label window length: 6 months. Positive = a new vulnerability advisory published in `(T, T + 6 months]` |
| Universe_T | The set of packages included in modelling at `T` (03 section 5) |
| Embargo | Gap of one horizon between train / validation / test `T` ranges |
| OSV | Open Source Vulnerabilities database (advisories). deps.dev = Google's dependency/package metadata dataset on BigQuery |

## 7. How the human works

The human generates code with an agent, then retypes it into VS Code. Therefore: keep files detailed and focused,avoid uneccesary codes addition, avoid clever metaprogramming, prefer explicit readable code, and put a short docstring at the top of every file saying what it does and which doc section it implements.
