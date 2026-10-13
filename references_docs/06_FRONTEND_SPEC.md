# 06 - Frontend Specification (React + TypeScript)

Location: `frontend/` (separate from `backend/`). Vite + React + TypeScript (`strict: true`). The API contract is in 05 section 6; the TypeScript types below must match it exactly.

## 1. Stack
| Concern | Choice |
|---|---|
| Build | Vite, React 18+, TypeScript strict |
| Styling | Tailwind CSS; support light and dark mode; 16 px side gutter on phones; no horizontal page scroll |
| Server state | TanStack Query |
| UI state | Zustand (selection, filters, scope, overlay) |
| Routing | React Router |
| Graph | Cytoscape.js instance managed in a `useEffect` (no wrapper that re-creates the graph) + `cytoscape-fcose` layout |
| Charts (model card) | a small chart lib or plain SVG; follow the dataviz rules: one colour system, labelled axes, not colour-only encodings |
| Tests | Vitest + Testing Library; Playwright optional for one end-to-end flow |
Pin exact verified versions of every dependency (see 00 rule 10).

## 2. Folder structure (`frontend/src`)
```
pages/        Landing, Analysis, Package, Ecosystem, ModelCard, NotFound
components/
  graph/      GraphCanvas, GraphLegend, GraphControls, useCytoscape (hook)
  panels/     LeveragePanel, NodeDetailPanel, SummaryStrip, FiltersBar, ProvenanceFooter
  common/     UploadDropzone, SeverityBadge, RiskBadge, ErrorBanner, Spinner, EmptyState
api/          client.ts (fetch wrapper), queries.ts (TanStack hooks)
state/        useUiStore.ts (Zustand)
types/        api.ts (mirrors 05 section 6.2), ui.ts
lib/          colors.ts, graphStyle.ts, format.ts
```

## 3. Types (mirror of the API; keep in sync via the contract test)
```ts
export type Severity = "CRITICAL" | "HIGH" | "MODERATE" | "LOW" | "UNKNOWN";
export type Scope = "prod" | "all";
export type RiskBand = "High" | "Medium" | "Low";

export interface Meta { osv_data_date: string; deps_dev_snapshot: string; risk_model_version: string; app_version: string }
export interface AnalysisSummary {
  analysis_id: string; created_at: string; expires_at: string; scope: Scope; lockfile_version: 2 | 3;
  counts: { packages: number; direct: number; vulnerable: number; malicious: number; unscanned: number; dev_only: number; advisories: number };
  meta: Meta;
}
export interface GraphNode {
  id: string; name: string; version: string | null; depth: number; is_direct: boolean; dev_only: boolean;
  optional: boolean; peer: boolean; vulnerable: boolean; max_severity: Severity | null; malicious: boolean;
  unscanned: boolean; risk_score: number | null; risk_band: RiskBand | null;
}
export interface GraphEdge { source: string; target: string; kind: "prod" | "optional" | "peer" | "dev" }
export interface Graph { scope: Scope; truncated: boolean; total_nodes: number; nodes: GraphNode[]; edges: GraphEdge[] }
export interface AdvisoryRef { id: string; aliases: string[]; severity: Severity; published: string | null; summary: string | null }
export interface VulnerableNode { node_id: string; name: string; version: string; max_severity: Severity; advisories: AdvisoryRef[]; paths: string[][]; n_direct_parents: number }
export interface LeverageRow { direct_dep: string; name: string; version: string; removes_n: number; removes_weighted: number; node_ids: string[] }
export interface Exposure {
  scope: Scope; vulnerable_nodes: VulnerableNode[];
  malicious_nodes: { node_id: string; name: string; version: string; report_ids: string[] }[];
  fix_leverage: LeverageRow[]; unscanned: { node_id: string; name: string; reason: string }[];
}
export interface RiskFactor { feature: string; contribution: number; value: number | null }
export interface RiskItem { name: string; score: number; risk_percentile: number; band: RiskBand; factors: RiskFactor[] }
export interface RiskList { model_version: string; items: RiskItem[]; not_scored: string[] }
// PackageDetail, ModelCard, PackageRow: copy from 05 section 6.2.
```

## 4. Pages

### 4.1 Landing (`/`)
Value statement; `UploadDropzone` (accepts `package-lock.json`, shows size cap, rejects non-JSON early); "Try a sample" list from `/api/samples`; privacy line ("the file is processed, not stored; results expire in 7 days"); provenance footer.

### 4.2 Analysis workspace (`/analysis/:id`)
Three-column layout (stacks on small screens, graph first):
- **Top**: `SummaryStrip` (packages, direct, vulnerable, malicious, unscanned) + `FiltersBar` (scope toggle prod/all, severity multi-select, max depth slider, risk overlay toggle, search by name).
- **Left**: `LeveragePanel` - ranked removal-leverage rows (bar = `removes_weighted`, badge = `removes_n`); hover highlights `node_ids` + the direct dependency in the graph; click selects the direct dependency. Heading text: "Exposure removed if dropped".
- **Centre**: `GraphCanvas`.
- **Right**: `NodeDetailPanel` for the selected node: advisories (id, aliases, severity, date), exposure paths (clickable chains that highlight), `n_direct_parents`, risk score/band/factors with the caveat text, link to Package page.
- Malicious nodes get a prominent banner with report ids; unscanned packages are listed in a collapsible section with reasons.

### 4.3 Package page (`/package?name=@scope/pkg`)
Header (name, risk band + percentile or "not scored"), stats (dependents, versions, first/last publish, repo stats if present), advisory history table, 2-hop neighbourhood graph from `/api/packages/neighborhood` (same `GraphCanvas`), risk factors with caveat.

### 4.4 Ecosystem explorer (`/ecosystem`)
Table of top packages by risk or dependents (`/api/ecosystem/top`), sortable; click opens the Package page. Simple distribution chart of scores by band.

### 4.5 Model card (`/model`)
Renders `GET /api/model/card` only: data sources/dates, universe definition, split diagram with the embargo gaps, results tables (models x splits x populations, **with base rates**), calibration plots for Test-A and Test-B, stratified results, surge note, limitations. Never hard-code numbers in the page; if a value is absent show "not measured".

## 5. Graph rendering rules (`GraphCanvas`, `graphStyle.ts`)
- Create the Cytoscape instance once; update with `cy.batch()`; use style **classes** (`vuln-critical`, `dev-only`, `selected`, `path`, `dimmed`) instead of per-element style writes.
- Encodings: **colour** = severity of the worst advisory (sequential scale: none/low/moderate/high/critical) or, when the risk overlay is on, risk band; **size** = log of number of dependents in the lockfile graph (fallback constant); **shape/border** also differ for malicious (octagon + thick border) and unscanned (dashed) so meaning never relies on colour alone; direct dependencies get a ring.
- Layout: fcose, run once per data/scope change; keep positions when only styles change; "Re-layout" button.
- Interaction: click node -> select, highlight all returned `paths` (edges + nodes get `path`, others `dimmed`); hover a leverage row -> highlight its `node_ids`; double-click -> focus neighbourhood; box select optional.
- **Scale**: the API caps nodes (`max_nodes`, default 2000, `truncated` flag). When truncated or very large: start with vulnerable nodes + their exposure paths + direct dependencies, collapse the remaining subtrees into expandable "+N hidden" nodes; show a banner "Showing X of Y packages". Hide labels below a zoom threshold. The 3,078-package sample (`modernweb.json`) is the acceptance test: no UI freeze (measure and report).
- Accessibility: keyboard-focusable list alternative for the graph (leverage list and vulnerable-node list are navigable), sufficient contrast in both themes, `aria-label`s, no colour-only encodings.

## 6. State (Zustand `useUiStore`)
`scope: Scope`, `selectedNodeId: string | null`, `hoverNodeIds: string[]`, `severityFilter: Severity[]`, `maxDepth: number`, `riskOverlay: boolean`, `search: string`. Server data lives only in TanStack Query. URL holds `analysis id` and `scope`.

## 7. API client
Single `client.ts`: base URL from `import.meta.env.VITE_API_URL`; JSON helpers; upload via `FormData`; error normalisation to `{code, message, request_id}`; queries keyed by `[analysisId, scope]`. Handle 410 (expired) with a "re-upload" screen, 413/422 with the server message.

## 8. Wording and copy rules (from 01 section 7)
"Known vulnerabilities in OSV data as of {meta.osv_data_date}"; "Exposure removed if this dependency were dropped"; risk = "Estimated likelihood of a new advisory within 6 months (statistical prioritisation signal)" with band + percentile + factors; unscanned packages are "not checked", never "safe"; no "live" labels.

## 9. States to implement for every data view
Loading skeleton, empty ("No known vulnerabilities in the OSV data as of ... - this is not a guarantee of safety"), error banner with retry, truncated warning, expired analysis.

## 10. Tests
- Unit: formatters, colour/size mapping functions, store reducers, API error normalisation.
- Component: Dropzone rejects non-JSON/oversize; LeveragePanel ordering and hover callbacks; NodeDetailPanel renders paths; ModelCard shows "not measured" for missing values.
- Contract: type check against the exported API JSON Schema (05 section 8).
- E2E (optional): load a sample -> graph visible -> click vulnerable node -> paths highlighted -> toggle scope.
