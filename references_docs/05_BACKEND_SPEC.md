# 05 - Backend Specification (FastAPI)

Location: `backend/`. Pure, testable core modules (parsing, matching, exposure) with no I/O; thin routers; DB access in `repository`/`db` modules. Reference implementations in sections 3-5 were **tested** (random-graph equivalence, table-driven cases); use them as the starting point, keep their tests.

## 1. Modules

| Module | Responsibility |
|---|---|
| `parsing/lockfile.py` | lockfile v2/v3 -> nodes + edges (section 3) |
| `advisories/matcher.py` | `is_affected(version, ranges, versions)` (section 4) |
| `advisories/repository.py` | fetch advisories for a list of package names |
| `exposure/graph.py` | build a `networkx.DiGraph` from nodes/edges, scope (prod/dev) |
| `exposure/paths.py` | up to K shortest paths root -> node; count distinct direct parents |
| `exposure/fix_leverage.py` | removal leverage by dominators (section 5) |
| `risk/lookup.py` | risk rows for package names |
| `routers/*.py` | HTTP layer only |
| `schemas/*.py` | Pydantic models = section 6 JSON shapes |
| `db/*.py` | SQLAlchemy models, session, Alembic migrations |

## 2. Request pipeline (`POST /api/analyses`)
1. Enforce `MAX_LOCKFILE_MB`; read bytes; compute SHA-256 (stored for dedupe/debug; **the file body is not stored**).
2. `json.loads`; reject non-objects. Require `lockfileVersion` in {2, 3} and a `packages` map, else HTTP 422 `UNSUPPORTED_LOCKFILE`.
3. Parse (section 3) -> nodes, edges. Cap: 20,000 nodes (HTTP 413 `LOCKFILE_TOO_LARGE`).
4. Mark each node: `scannable` iff `version` is strict semver **and** the resolved source is the registry (or absent). Others -> `unscanned` (listed in the summary, never "safe").
5. Fetch advisories where `package = ANY(names)` and `withdrawn IS NULL`; match each scannable node (section 4). `kind='malware'` matches -> `malicious=true`; `kind='vulnerability'` matches -> `advisories[]`, `max_severity`.
6. Build the DiGraph (scope default: production only, section 3.3). Compute exposure paths and removal leverage on the scoped graph.
7. Look up risk for `name` (not version) in `risk_scores` for the active `model_version`.
8. Persist result JSON (`analyses` row, `expires_at = now + ANALYSIS_RETENTION_DAYS`); return `201` with `analysis_id` + summary.
9. Compute **both** scopes (`prod` and `all`) at upload and store both inside the `graph` and `exposure` JSON (keyed by scope), so the UI toggle needs no recomputation. Endpoints select by the `scope` query parameter (default `prod`).
10. Bundled samples: copy the five files from `data/sample_lockfiles/` into `backend/app/samples/` (small enough to ship) and serve them through `/api/samples`; `POST /api/analyses/sample/{id}` runs the same pipeline on them.

## 3. Lockfile parsing (v2/v3) - Algorithmic Specification

### 3.1 Node Resolution Algorithm
Resolving a dependency `dep` referenced by a package located at path `P` within the lockfile `packages` map:
1. **Candidate Path Check**: Form candidate string `<P>/node_modules/<dep>` if `P` is non-empty, else `node_modules/<dep>`.
2. **Registry Lookup**: If candidate exists in the `packages` map, return candidate as the resolved node ID.
3. **Ascend Hierarchy**: If candidate does not exist and `P` is not the root, find the last `/node_modules/` in `P`, truncate `P` to that prefix, and repeat step 1.
4. **Unresolved**: If `P` is empty and candidate is not found, return `None` (representing an optional or uninstalled peer dependency).

### 3.2 Package Identification & Edge Rules
- **Node Identifier**: The exact `packages` dictionary key (e.g., `node_modules/a/node_modules/b`); the root entry `""` is mapped to ID `"root"`.
- **Package Name**: Extracted from `entry["name"]` if explicitly present (aliased packages), otherwise parsed from the path segment following the final `node_modules/` (preserving scoped names such as `@scope/pkg`).
- **Edge Kinds**:
  - `dependencies` -> `prod` edge.
  - `optionalDependencies` -> `optional` edge.
  - `peerDependencies` -> `peer` edge.
  - `devDependencies` -> `dev` edge (evaluated **only for the root and workspace root entries**).
- **Workspace Symlinks**: Entries with `link: true` have no dependencies of their own; incoming edges follow the `resolved` attribute to link directly to the target package entry.
- **Node Flags**: Recorded directly from lockfile attributes: `dev`, `optional`, `peer` (`devOptional` treated as dev).
- **Version Scannability**: A node is marked `scannable` only if its version string adheres strictly to SemVer specification and its resolved source is a package registry. Non-semver or git/URL sources are marked `unscanned`.

### 3.3 Scope Handling
- `scope = "prod"` (Default): Prune all `dev` edges. Nodes that become unreachable from `root` in the pruned graph are marked `dev_only: true` and excluded from vulnerability exposure and removal leverage.
- `scope = "all"`: Retain `dev` edges; all reachable nodes participate in graph evaluation.

## 4. Advisory matching - Algorithmic Specification

### 4.1 SemVer Range Evaluation Logic
OSV npm advisories specify vulnerability windows via `ranges` (`SEMVER` or `ECOSYSTEM`) and explicit `versions` lists:
1. **Explicit Version Check**: If package `version` appears in `advisory.versions`, the package is immediately marked **affected**.
2. **Range Interval Extraction**:
   - Extract event tuples `(version_point, event_kind)` for kinds: `introduced`, `fixed`, `last_affected`.
   - Map version point `"0"` to `0.0.0`.
3. **Event Ordering**: Sort extracted events by version point in ascending order. At identical version points, order open boundaries (`introduced`) before close boundaries (`fixed` / `last_affected`).
4. **State Machine Traversal**:
   - Initialize `affected = False`.
   - Traverse sorted events:
     - If `event_kind == "introduced"` and `pkg_version >= version_point`: set `affected = True`.
     - If `event_kind == "fixed"` and `pkg_version >= version_point`: set `affected = False`.
     - If `event_kind == "last_affected"` and `pkg_version > version_point`: set `affected = False`.
5. **Multi-Range Combination**: Multiple ranges within an advisory are combined via logical `OR`.
6. **Non-SemVer Handling**: Versions that fail SemVer parsing are not evaluated against ranges and are reported as `unscanned`.

### 4.2 Severity & Weighting
- Map OSV `database_specific.severity` to standard enum: `CRITICAL`, `HIGH`, `MODERATE`, `LOW`, or `UNKNOWN`.
- Configurable severity weights (default: CRITICAL=4, HIGH=3, MODERATE=2, LOW=1, UNKNOWN=1).

## 5. Exposure engine - Algorithmic Specification

Graph structure: Directed Graph $G = (V, E)$, with edges oriented `parent -> child` (dependency relation) and root identifier `"root"`.

### 5.1 Exposure Paths
For each vulnerable or malicious node $v$ reachable from `"root"`:
1. Enumerate up to `MAX_PATHS_PER_NODE` (default 5) shortest simple paths from `"root"` to $v$ (using shortest-simple-paths traversal, bounded to prevent combinatorial explosion).
2. Compute `n_direct_parents`: the count of distinct direct dependencies of `"root"` through which node $v$ is reachable.

### 5.2 Dominator-Based Removal Leverage
Removal leverage quantifies how much systemic vulnerability exposure dropping or replacing a direct dependency would eliminate:
1. **Immediate Dominators**: Compute immediate dominators tree with root `"root"` using the Lengauer-Tarjan dominator tree algorithm. Node $d$ dominates node $v$ if every path from `"root"` to $v$ must pass through $d$.
2. **Dominated Subtrees**: For each direct dependency $d$ (where $(root, d) \in E$), the dominated set $Dom(d)$ contains all descendants of $d$ in the dominator tree plus $d$ itself.
3. **Leverage Calculation**:
   - Intersect $Dom(d)$ with the set of reachable vulnerable nodes $V_{vuln}$.
   - Compute `removes_n`: count of vulnerable nodes in $Dom(d) \cap V_{vuln}$.
   - Compute `removes_weighted`: sum of severity weights for all vulnerable nodes in $Dom(d) \cap V_{vuln}$.
4. **Ranking**: Rank direct dependencies descending by `removes_weighted`, then `removes_n`, then dependency name.
5. **Interpretation**: Leverage represents an **upper bound on exposure reduction** if direct dependency $d$ were eliminated. UI wording must strictly reflect this upper bound.

## 6. API contract (all JSON; `Content-Type: application/json` except upload)

Error shape (all non-2xx): `{"error": {"code": "UNSUPPORTED_LOCKFILE", "message": "human readable", "request_id": "..."}}`. Codes: `INVALID_JSON` (400), `UNSUPPORTED_LOCKFILE` (422), `LOCKFILE_TOO_LARGE` (413), `NOT_FOUND` (404), `EXPIRED` (410), `INTERNAL` (500).

### 6.1 Endpoints
```
GET  /health                                   -> {"status":"ok"}
GET  /api/meta                                 -> Meta
POST /api/analyses                multipart "file" (+ optional form "scope": "prod"|"all")  -> 201 AnalysisSummary
GET  /api/analyses/{id}                        -> AnalysisSummary
GET  /api/analyses/{id}/graph?scope=prod|all&max_nodes=2000  -> Graph
GET  /api/analyses/{id}/exposure?scope=prod|all              -> Exposure
GET  /api/analyses/{id}/risk                   -> RiskList
GET  /api/packages?name=@scope/pkg             -> PackageDetail          (query param avoids slash routing issues)
GET  /api/packages/neighborhood?name=...&depth=2&max_nodes=300 -> Graph (ecosystem graph, no lockfile)
GET  /api/ecosystem/top?sort=risk|dependents&limit=50           -> PackageRow[]
GET  /api/model/card                           -> ModelCard
GET  /api/samples                              -> [{"id":"expressjs-website","name":"...","n_packages":937}]
POST /api/analyses/sample/{id}                 -> 201 AnalysisSummary    (runs a bundled sample lockfile)
```

### 6.2 Schemas (Pydantic = TypeScript in 06; keep identical)
```jsonc
Meta { "osv_data_date": "2026-10-12", "deps_dev_snapshot": "2026-10-05T21:01:16Z",
       "risk_model_version": "20261101-ab12cd3-m3", "app_version": "0.1.0" }

AnalysisSummary {
  "analysis_id": "uuid", "created_at": "iso", "expires_at": "iso",
  "scope": "prod", "lockfile_version": 3,
  "counts": { "packages": 937, "direct": 41, "vulnerable": 12, "malicious": 0,
              "unscanned": 3, "dev_only": 432, "advisories": 19 },
  "meta": Meta }

Graph {
  "scope": "prod", "truncated": false, "total_nodes": 505,
  "nodes": [ { "id": "node_modules/a/node_modules/b", "name": "b", "version": "2.0.0",
               "depth": 2, "is_direct": false, "dev_only": false, "optional": false, "peer": false,
               "vulnerable": true, "max_severity": "HIGH", "malicious": false, "unscanned": false,
               "risk_score": 0.031, "risk_band": "Medium" /* nullable when not scored */ } ],
  "edges": [ { "source": "root", "target": "node_modules/a", "kind": "prod" } ] }

Exposure {
  "scope": "prod",
  "vulnerable_nodes": [ { "node_id": "...", "name": "b", "version": "2.0.0", "max_severity": "HIGH",
       "advisories": [ { "id": "GHSA-...", "aliases": ["CVE-..."], "severity": "HIGH",
                         "published": "iso", "summary": null } ],
       "paths": [ ["root","node_modules/a","node_modules/a/node_modules/b"] ],
       "n_direct_parents": 1 } ],
  "malicious_nodes": [ { "node_id": "...", "name": "...", "version": "...", "report_ids": ["MAL-..."] } ],
  "fix_leverage": [ { "direct_dep": "node_modules/a", "name": "a", "version": "1.0.0",
       "removes_n": 3, "removes_weighted": 7, "node_ids": ["..."] } ],
  "unscanned": [ { "node_id": "...", "name": "...", "reason": "non-registry source | unparseable version" } ] }

RiskList { "model_version": "...", "items": [ { "name": "b", "score": 0.031, "risk_percentile": 0.97,
           "band": "High", "factors": [ { "feature": "n_prior_adv", "contribution": 0.4, "value": 3 } ] } ],
           "not_scored": ["pkg-x"] }

PackageDetail { "name": "b", "scored": true, "risk": RiskItem|null,
   "stats": { "dependents": 1234, "n_versions": 40, "first_published": "iso", "last_published": "iso",
              "stars": 100|null, "forks": null, "open_issues": null },
   "advisories": [ { "id": "...", "severity": "...", "published": "iso", "kind": "vulnerability" } ] }

ModelCard { "model_version": "...", "chosen_model": "m3", "data": {...}, "splits": {...},
            "results": [ { "model": "B1", "split": "test_a", "population": "first_time",
                           "base_rate": 0.00, "pr_auc": 0.0, "lift_at_500": 0.0 } ],
            "calibration": {...}, "notes": [ "..." ] }   // values come only from stored metrics.json
```
Rules: ids are the lockfile `packages` keys (strings); numbers are never fabricated; absent risk -> `null` and the package name appears in `not_scored`.

## 7. Database Specification (PostgreSQL & Alembic)

### 7.1 Schema Definitions
The database stores point-in-time reference data and analysis results:

| Table | Primary Key | Columns & Constraints | Description |
|---|---|---|---|
| `advisories` | `(osv_id, package)` | `kind TEXT CHECK (kind IN ('vulnerability','malware'))`, `published TIMESTAMPTZ`, `modified TIMESTAMPTZ`, `withdrawn TIMESTAMPTZ`, `severity TEXT`, `aliases JSONB`, `ranges JSONB`, `versions JSONB` | Ingested OSV advisories. Indexed on `package`. |
| `risk_scores` | `(package, model_version)` | `score REAL NOT NULL`, `risk_percentile REAL NOT NULL`, `band TEXT NOT NULL`, `factors JSONB` | Precomputed package risk scores and approximate drivers. |
| `package_stats` | `package` | `dependents INT`, `n_versions INT`, `first_published TIMESTAMPTZ`, `last_published TIMESTAMPTZ`, `n_prior_advisories INT`, `stars INT`, `forks INT`, `open_issues INT` | Package popularity and release cadence summary. |
| `package_edges` | `(src, dst)` | `src TEXT NOT NULL`, `dst TEXT NOT NULL` | Ecosystem dependency edges as of latest snapshot. Indexed on `dst`. |
| `model_runs` | `id SERIAL` | `model_version TEXT UNIQUE`, `chosen_model TEXT`, `trained_at TIMESTAMPTZ`, `git_sha TEXT`, `config JSONB`, `splits JSONB`, `metrics JSONB`, `is_active BOOLEAN` | Metadata and evaluation metrics for model card serving. |
| `meta` | `key` | `value TEXT NOT NULL` | System provenance metadata (`osv_data_date`, `deps_dev_snapshot`, etc.). |
| `analyses` | `id UUID` | `created_at TIMESTAMPTZ`, `expires_at TIMESTAMPTZ NOT NULL`, `lockfile_sha256 TEXT`, `lockfile_version INT`, `scope_default TEXT`, `summary JSONB`, `graph JSONB`, `exposure JSONB` | Transient analysis results (**never stores lockfile body**). Indexed on `expires_at`. |

Storage note: OSV npm is ~233k rows, 95% malware. If storage is constrained, malware rows are stored compactly (`osv_id`, `package`, `published`, `ranges`). Expired analyses are pruned lazily on write or via periodic task.

## 8. Testing requirements
- Unit (no DB): parsing, matching (table-driven), exposure (property test + hand graphs), scope handling, severity mapping.
- API (httpx TestClient + test DB or SQLite-compatible fixtures): upload each sample lockfile -> 201, graph/exposure shapes validate against schemas; invalid JSON -> 400; v1 lockfile -> 422; oversize -> 413; unknown id -> 404; expired -> 410.
- Contract test: serialise each Pydantic schema to JSON Schema and compare with `frontend/src/types` (script or snapshot).
- Performance smoke: analyse `modernweb.json` (3,078 packages) and record the time (report the measured number; do not assume).
- Privacy test: assert no lockfile body or package names appear in logs or the `analyses` row beyond the computed result.

## 9. Dependencies (pin exact, verified versions)
fastapi, uvicorn, pydantic (v2), pydantic-settings, SQLAlchemy 2, alembic, psycopg (v3, binary), networkx, semantic-version, python-multipart, pytest, httpx, ruff. Loading scripts (offline only): pandas, pyarrow.

## 10. Project-Level Guidelines & Engineering Standards
This project is a **High-Impact Technical Portfolio Project** designed to showcase applied software engineering, graph data science, and applied machine learning skills for job and internship applications.

1. **Target Complexity**: Professional Portfolio Grade — strong fundamentals, rigorous methodology, clean code design, and clear problem-solving, without enterprise over-engineering.
2. **Professional Tone (No Classroom/Viva Jargon)**:
   - Strictly **avoid** academic/classroom terms like "viva", "oral defense", "notes for examiners", "student capstone defense", or "homework".
   - Use professional software engineering terminology: **"Key Engineering Takeaways"**, **"Technical Insights & Trade-offs"**, **"Production Considerations"**, **"Performance Scorecard"**.
3. **Coding Style**:
   - Clean, readable, idiomatic Python (`FastAPI`, `Pydantic`, `SQLAlchemy`, `NetworkX`, `semantic-version`).
   - Self-contained, modular, pure algorithmic cores, and easy to walk through during a technical interview.
