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

## 3. Lockfile parsing (v2/v3) - reference (tested)

```python
def pkg_name_from_path(path, entry):
    return entry.get("name") or path.rsplit("node_modules/", 1)[-1]

def resolve(path, dep, packages):
    """Node resolution: <path>/node_modules/<dep>, then walk up parent node_modules, ..., root."""
    base = path
    while True:
        cand = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
        if cand in packages: return cand
        if not base: return None
        i = base.rfind("/node_modules/")
        base = base[:i] if i != -1 else ""
```
Rules:
- Node id = the `packages` key (e.g. `node_modules/a/node_modules/b`); the root entry `""` has id `"root"`.
- Name: `entry["name"]` if present (aliases), else the path segment after the last `node_modules/` (scoped names like `@s/x` are preserved by `rsplit`).
- Edge kinds per source entry: `dependencies` -> `prod`; `optionalDependencies` -> `optional`; `peerDependencies` -> `peer`; `devDependencies` -> `dev` **only for the root and workspace roots**. If `resolve(...)` returns nothing (optional/uninstalled peer) there is **no edge**.
- Entries with `link: true` are workspace symlinks: they have no deps of their own; an edge into a link follows `resolved` to the target package entry.
- Flags from the lockfile: `dev`, `optional`, `peer` (`devOptional` counts as dev for scope purposes).
- Ignore the legacy v2 `dependencies` tree. Do not read `integrity`/`resolved` beyond deciding `scannable`.

### 3.3 Scope
- `scope = "prod"` (default): drop `dev` edges; nodes unreachable from root through the remaining edges are **dev-only** (flagged `dev_only: true`, excluded from exposure/leverage).
- `scope = "all"`: include dev edges.

Tests (fixtures in `backend/tests/fixtures/`): nested duplicate (`a/node_modules/b` vs hoisted `b`), scoped package, dev/optional/peer flags, workspace link, v1 rejection, aliased package name, malformed JSON; and all five files in `data/sample_lockfiles/` parse without error.

## 4. Advisory matching - reference (tested)

OSV semantics for npm (`ranges[].type` of `SEMVER` or `ECOSYSTEM`): within a range, events sorted by version (`"0"` = lowest). A version is **affected** if it falls in an interval that opens at `introduced` and closes at the next `fixed` (exclusive) or `last_affected` (inclusive); an `introduced` with no close is open-ended. A version listed in `affected[].versions` is also affected. Ranges are OR-ed. Unparseable versions are **not matched** (the caller reports the package as `unscanned`).

```python
from semantic_version import Version   # pin an exact version; npm versions are strict semver

def is_affected(version, ranges, versions):
    try: ver = Version(version)
    except ValueError: return False
    if version in (versions or []): return True
    for rng in ranges or []:
        if rng.get("type") not in ("SEMVER", "ECOSYSTEM"): continue
        events = []
        for e in rng.get("events", []):
            for kind in ("introduced", "fixed", "last_affected"):
                if kind in e:
                    events.append((Version("0.0.0") if e[kind] == "0" else Version(e[kind]), kind))
        order = {"introduced": 0, "fixed": 1, "last_affected": 1}
        events.sort(key=lambda t: (t[0], order[t[1]]))     # open before close at equal versions
        affected = False
        for point, kind in events:
            if kind == "introduced" and ver >= point: affected = True
            elif kind == "fixed" and ver >= point: affected = False
            elif kind == "last_affected" and ver > point: affected = False
        if affected: return True
    return False
```
Required table-driven tests (all passed in the reference): `[intro 0, fixed 1.2.3]` (1.2.2 yes, 1.2.3 no); two intervals (1.4.9 yes, 1.5.0 no, 2.0.5 yes, 2.1.0 no, 3.0.0 no); `last_affected 1.9.0` (1.9.0 yes, 1.9.1 no); open-ended; explicit `versions` list; prerelease `1.0.0-beta.1` is below `1.0.0` (affected when fixed is 1.0.0); unsorted events; non-semver version -> no match. Library behaviour for edge cases must be re-checked if the semver library is changed.

Severity: map from OSV `database_specific.severity` (`CRITICAL|HIGH|MODERATE|LOW`); missing -> `UNKNOWN`. Weights from config (default 4/3/2/1, unknown 1).

## 5. Exposure engine

Graph: `DiGraph`, edge `parent -> child` (resolved). Root id `"root"`.

### 5.1 Exposure paths
For every vulnerable (or malicious) node reachable from root: up to `MAX_PATHS_PER_NODE` (default 5) shortest paths (`networkx.shortest_simple_paths`, stop after K), plus `n_direct_parents` = number of distinct direct dependencies of root from which the node is reachable. Never enumerate all paths (can explode).

### 5.2 Removal leverage - reference (tested: equals brute-force node removal on 3,057 checks over 400 random graphs with cycles)

```python
import networkx as nx
ROOT = "root"
def dominated_sets(G, root=ROOT):
    idom = nx.immediate_dominators(G, root)          # reachable nodes only; idom[root] == root
    tree = nx.DiGraph(); tree.add_nodes_from(idom)
    for n, p in idom.items():
        if n != root: tree.add_edge(p, n)
    return {n: set(nx.descendants(tree, n)) | {n} for n in tree.nodes if n != root}

def fix_leverage(G, vulnerable, weights, root=ROOT):
    """vulnerable: node_id -> severity; weights: severity -> int"""
    dom, rows = dominated_sets(G, root), []
    for d in G.successors(root):
        removed = [v for v in dom.get(d, set()) if v in vulnerable]
        rows.append({"direct_dep": d, "removes_n": len(removed),
                     "removes_weighted": sum(weights.get(vulnerable[v], 1) for v in removed),
                     "node_ids": sorted(removed)})
    return sorted(rows, key=lambda r: (-r["removes_weighted"], -r["removes_n"], r["direct_dep"]))
```
Meaning: `d` *dominates* `v` if every root-to-`v` path passes through `d`; dropping `d` removes `v`. This is an **upper bound on benefit of changing `d`** (an upgrade of `d` may or may not remove them). UI wording follows 01 section 7.
Required tests: the property test (random digraphs with cycles; compare with brute-force "delete node, recompute reachability"), a hand-built graph where a vulnerable node is reachable via two direct dependencies (leverage 0 for each), and a node reachable through exactly one.

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

## 7. Database (PostgreSQL; Alembic migrations)

```sql
CREATE TABLE advisories (
  osv_id TEXT NOT NULL, package TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('vulnerability','malware')),
  published TIMESTAMPTZ, modified TIMESTAMPTZ, withdrawn TIMESTAMPTZ,
  severity TEXT, aliases JSONB, ranges JSONB, versions JSONB,
  PRIMARY KEY (osv_id, package));
CREATE INDEX advisories_package_idx ON advisories (package);

CREATE TABLE risk_scores (
  package TEXT NOT NULL, model_version TEXT NOT NULL, score REAL NOT NULL, risk_percentile REAL NOT NULL,
  band TEXT NOT NULL, factors JSONB, PRIMARY KEY (package, model_version));

CREATE TABLE package_stats (
  package TEXT PRIMARY KEY, dependents INT, n_versions INT, first_published TIMESTAMPTZ, last_published TIMESTAMPTZ,
  n_prior_advisories INT, stars INT, forks INT, open_issues INT);

CREATE TABLE package_edges (src TEXT NOT NULL, dst TEXT NOT NULL, PRIMARY KEY (src, dst));   -- ecosystem graph at NOW
CREATE INDEX package_edges_dst_idx ON package_edges (dst);

CREATE TABLE model_runs (
  id SERIAL PRIMARY KEY, model_version TEXT UNIQUE NOT NULL, chosen_model TEXT, trained_at TIMESTAMPTZ,
  git_sha TEXT, config JSONB, splits JSONB, metrics JSONB, is_active BOOLEAN DEFAULT FALSE);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- osv_data_date, deps_dev_snapshot, ...

CREATE TABLE analyses (
  id UUID PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL,
  lockfile_sha256 TEXT, lockfile_version INT, scope_default TEXT,
  summary JSONB NOT NULL, graph JSONB NOT NULL, exposure JSONB NOT NULL);   -- no lockfile body
CREATE INDEX analyses_expires_idx ON analyses (expires_at);
```
Storage note: OSV npm is ~233k rows, 95% malware. If free-tier storage is tight, store malware rows compactly (osv_id, package, published, ranges). Expired analyses are deleted lazily on write or by a periodic task.

## 8. Testing requirements
- Unit (no DB): parsing, matching (table-driven), exposure (property test + hand graphs), scope handling, severity mapping.
- API (httpx TestClient + test DB or SQLite-compatible fixtures): upload each sample lockfile -> 201, graph/exposure shapes validate against schemas; invalid JSON -> 400; v1 lockfile -> 422; oversize -> 413; unknown id -> 404; expired -> 410.
- Contract test: serialise each Pydantic schema to JSON Schema and compare with `frontend/src/types` (script or snapshot).
- Performance smoke: analyse `modernweb.json` (3,078 packages) and record the time (report the measured number; do not assume).
- Privacy test: assert no lockfile body or package names appear in logs or the `analyses` row beyond the computed result.

## 9. Dependencies (pin exact, verified versions)
fastapi, uvicorn, pydantic (v2), pydantic-settings, SQLAlchemy 2, alembic, psycopg (v3, binary), networkx, semantic-version, python-multipart, pytest, httpx, ruff. Loading scripts (offline only): pandas, pyarrow.
