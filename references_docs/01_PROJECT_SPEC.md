# 01 - Project Specification: DepRadar

## 1. One-line pitch
Upload an npm `package-lock.json`; see on an interactive graph **which dependencies expose you to known vulnerabilities, through which paths, and which direct dependencies account for the most exposure**, plus a learned **forecast of which packages are likely to receive new advisories**.

## 2. Problem and users
`npm audit` prints a flat list ("47 vulnerabilities"). Developers then ask: *Which of my direct dependencies brought this in? What is the cheapest change that removes the most exposure?*

| User | Need |
|---|---|
| Application developer | Triage audit noise, pick the 1-2 dependencies worth acting on |
| Small-team security engineer | See exposure paths and known-malicious packages in a lockfile |
| Maintainer | Understand which of their dependencies are likely to attract advisories |

## 3. Differentiator (and honest limits)
1. **Path-aware prioritisation**: exposure paths + removal leverage per direct dependency (dominator analysis), not a flat list.
2. **Forecast layer**: a graph-aware model estimates the chance a package gets a *new* vulnerability advisory in the next 6 months, evaluated with leakage-free temporal splits, with the model card shown in-product.
3. **Known-malicious flag**: lockfile packages that appear in OSV malware reports (`MAL-` ids) are reported separately and prominently.

Limits to state in the README and UI: DepRadar is an analysis layer, not a replacement for Dependabot/Snyk/`npm audit`; advisory data is a point-in-time copy of OSV; the risk score is a statistical prioritisation signal; removal leverage is an upper bound on benefit.

## 4. Scope

### 4.1 MVP (must ship)
| ID | Requirement |
|---|---|
| FR-1 | Upload `package-lock.json` (lockfileVersion 2 or 3). Reject v1 and invalid files with a clear message. |
| FR-2 | Build the resolved dependency graph (nodes = installed packages, edges = resolved dependency relations). |
| FR-3 | Match every installed (name, version) against OSV npm advisories; mark vulnerable nodes with severity. |
| FR-4 | Flag nodes that appear in OSV malware reports (`MAL-`) as **known malicious**, separate from vulnerabilities. |
| FR-5 | For each vulnerable node, return up to 5 shortest exposure paths from the root and the count of distinct direct dependencies that lead to it. |
| FR-6 | **Removal leverage** for each direct dependency (severity-weighted, via dominators); ranked list. |
| FR-7 | Production-vs-dev scope toggle (default: production only; dev-only packages flagged). |
| FR-8 | Learned risk score (0-1 + band + top factors) for each package that is in the scored universe; "not scored" otherwise. |
| FR-9 | Interactive graph: colour/size encodings, click-to-highlight paths, filters, collapse of large subtrees. |
| FR-10 | Package page: advisory history, risk score + factors, 2-hop neighbourhood from the ecosystem graph. |
| FR-11 | Model-card page rendering real stored metrics (baselines vs GNN, temporal split, calibration, stratified results, known failure modes). |
| FR-12 | "Try a sample lockfile" button using bundled sample files. |
| FR-13 | Footer shows data provenance: OSV data date, deps.dev snapshot date, risk model version. |

### 4.2 Later (not in MVP; do not build unless asked)
What-if upgrade simulation (fetch target version deps via the deps.dev API), PyPI/other ecosystems, GitHub App/PR comments, SBOM (CycloneDX) import, accounts and saved history, scheduled data refresh.

## 5. User flow
1. **Landing**: value statement, "Try a sample" and drag-and-drop upload.
2. **Upload** -> loading state (target: results in a few seconds for ~3,000-package lockfiles; measure, do not assume).
3. **Analysis workspace**: summary strip (packages, vulnerable, malicious, direct dependencies); left = ranked removal-leverage list; centre = graph; right = details of the selected node (advisories, paths, risk factors).
4. Click a vulnerable node -> all exposure paths highlight; hover a direct dependency in the list -> the subtree it dominates is highlighted.
5. Toggle dev scope, severity filter, depth filter, risk overlay.
6. Open the package page for any node; open the model card from the nav.

## 6. Non-goals (do not add)
Graph database; authentication/accounts; background job queue; LLM/chatbot features; real-time feeds (data is replayed snapshots, never labelled "live"); microservices; mobile app; features that execute or install code from the uploaded lockfile.

## 7. Wording rules (UI and README)
- "Removal leverage: exposure removed if this dependency were dropped" - never "fixes N vulnerabilities".
- Risk: "Estimated likelihood of a new advisory within 6 months (statistical prioritisation signal)". Show the band (Low/Medium/High) and the top factors; never a bare verdict.
- Say "known vulnerabilities in the OSV data as of [date]", never "no vulnerabilities" (absence of an advisory is not safety).
- Packages whose version cannot be parsed, or that come from git/file/URL sources, are listed as **unscanned**, not as safe.
- Never present replayed or snapshot data as live.

## 8. Quality bar / acceptance criteria
- Exposure engine: tests include random-graph equivalence with brute-force node removal (05 section 5).
- Matching engine: table-driven tests for introduced/fixed/last_affected/multi-interval/prerelease/explicit-versions cases (05 section 4).
- Parser: fixtures for lockfile v2 and v3, scoped packages, nested duplicates, workspaces/links, dev/optional/peer flags.
- All five files in `data/sample_lockfiles/` analyse without error (sizes 205 to 3,078 packages; one is v2).
- ML: evaluation follows 04_ML_SPEC exactly; model card contains only measured numbers.
- Frontend: 3,078-package lockfile renders without freezing (filtered/collapsed subgraph; cap documented).
- Privacy: the uploaded lockfile body is not stored; stored analysis results expire (default 7 days).

## 9. Technical Interview Walkthrough & Demo Flow
1. Load a real lockfile, review audit findings vs. reachability exposure.
2. Graph renders: vulnerable nodes highlighted by severity; known-malicious alerts surfaced distinctly.
3. Path tracing: inspect transitive dependencies to reveal root exposure chains.
4. Dominator-based removal leverage: quantify systemic exposure reduction achievable by dropping or upgrading direct dependencies.
5. Risk forecast & Model Card: inspect leak-free temporal evaluation, performance scorecards, and calibrated risk percentiles.
6. Architecture overview: deterministic online engine, precomputed statistical artifacts, stateless serving.

## 10. Technical Resume & Portfolio Highlights
- **Full-Stack Graph Analysis Platform**: Architected a production-grade dependency-exposure analyser (React/TypeScript, FastAPI, PostgreSQL) featuring in-memory lockfile graph reconstruction, OSV range matching, and dominator tree removal-leverage ranking.
- **Leak-Free Temporal ML Pipeline**: Designed a temporal evaluation framework with strict 6-month embargo buffers comparing heuristic baselines, gradient-boosted decision trees with topological neighbor aggregates, and PyTorch Geometric GNNs on an ecosystem-scale dependency graph.
- **Interactive High-Performance UI**: Developed an interactive Cytoscape.js workspace rendering multi-thousand-node dependency graphs with dynamic path highlighting, progressive subtree collapsing, and in-product model cards.

## 11. Project-Level Guidelines & Engineering Standards
This project is a **High-Impact Technical Portfolio Project** designed to showcase applied software engineering, graph data science, and applied machine learning skills for job and internship applications.

1. **Target Complexity**: Professional Portfolio Grade — strong fundamentals, rigorous methodology, clean code design, and clear problem-solving, without enterprise over-engineering (no microservice boilerplate, defensive try-except mazes, or excessive abstractions).
2. **Professional Tone**: Strictly avoid academic/classroom jargon. Use professional software engineering terminology: **"Key Engineering Takeaways"**, **"Technical Insights & Trade-offs"**, **"Production Considerations"**, **"Performance Scorecard"**.
3. **Coding Style**: Clean, readable, idiomatic code (`FastAPI`, `Pydantic`, `pandas`, `NetworkX`, `LightGBM`, `TypeScript`), self-contained, modular, and easy to explain during a technical interview.
