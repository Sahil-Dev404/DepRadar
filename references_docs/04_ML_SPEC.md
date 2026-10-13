# 04 - Machine Learning Specification

Scope: the **advisory-risk forecast**. (Parsing, matching, exposure analysis are deterministic and are specified in 05; they involve no ML.)
No performance number may appear in any document or UI unless produced by a run of this protocol.

## 1. Task
For a package `P` and snapshot date `T`, predict the probability that `P` receives **at least one new vulnerability advisory published in `(T, T + 6 months]`**.
- Advisory source: OSV npm, `kind = 'vulnerability'` (ids not starting `MAL-`), `withdrawn` null.
- Matching: by package name only.
- Unit: (package, T) for every package in `universe_T` (03 section 3). Data files: 03 section 5.3.
- Positive rate is low and varies by period (03 sections 2.5-2.6). Expect heavy class imbalance.

## 2. Hard rules (leakage)
1. Every feature for `(P, T)` uses only data with timestamp `<= T` (versions published, advisories published, dependents as of `T`, repo stats from the `T` snapshot, graph edges from versions visible at `T`).
2. Never use current-state fields: deprecation flags, current stars, withdrawn status updated after `T`, today's popularity.
3. **Splits are by time with an embargo of one horizon**: label windows of an earlier split may not overlap the feature time of the next split (section 3).
4. Normalisation/imputation statistics are fitted on the training split only.
5. Hyper-parameters are tuned on validation only. Test-A and Test-B are looked at once per final candidate.
6. Package names/ids are never used as features.
7. Unit tests assert (a) no label advisory has `published <= T`; (b) moving `T` earlier never increases any cumulative count feature; (c) edges only reference nodes in `universe_T`.

## 3. Splits (config: `ml/configs/splits.yaml`; dates are monthly grid snapshots)

| Split | Snapshot dates `T` | Label windows end by | Purpose |
|---|---|---|---|
| Train | 2023-01 ... 2024-01 (13 dates) | 2024-07 | fit models |
| Validation | 2024-07 ... 2024-09 (3) | 2025-03 | tuning, model selection, calibration |
| Test-A (pre-surge) | 2025-03 ... 2025-05 (3) | 2025-11 | main held-out evaluation |
| Test-B (surge stress test) | 2025-11 ... 2026-04 (6) | 2026-10 | robustness to distribution shift |

**Embargo rule (implement and test)**: for consecutive splits, `next.T_min.date() > (prev.T_max + 6 months).date()`; drop leading dates of the next split until true. Counts above may shrink by one date. All labelled `T` must satisfy `T + 6 months <= latest snapshot (2026-10-05)`.
Overlapping windows from monthly dates are highly correlated (the same advisory appears in up to six windows): effective sample size is much smaller than the row count. Report mean and spread across `T`, not p-values.
Config values are proposals; confirm after counting positives within the universe (fail if a split has fewer than 30 positives per `T`).

## 4. Features (all as of `T`; file `features.parquet`)

| Group | Feature | Definition |
|---|---|---|
| Package | `age_days` | `T - first published_at` |
| | `n_versions`, `n_release_versions` | count of visible versions |
| | `days_since_last_release` | `T - latest visible published_at` |
| | `releases_90d`, `releases_365d` | releases in the trailing windows |
| | `median_gap_days_last20` | median days between the last 20 releases |
| | `n_deps_prod` | production dependencies of the latest visible version (within universe; also store the raw count) |
| Popularity | `log_dependents` | `log1p(dependents_tXX)` |
| | `dependents_delta_3m` | change vs grid date three months earlier (NaN + flag if unavailable) |
| Advisory history | `n_prior_adv`, `n_prior_adv_365d` | OSV vulnerabilities published `<= T` (and in the last 365 days) |
| | `days_since_last_adv` | NaN + `has_prior_adv` flag if none |
| | `max_severity_ord`, `n_prior_critical_high` | severity ordinal 0..4; counts |
| Repository | `has_repo`, `log_stars`, `log_forks`, `log_open_issues` | from `Projects` at snapshot `T` via `package_project`; missing -> 0 + `has_repo = 0` |
| Graph (M3 only) | `out_deg`, `in_deg` | edges at `T` |
| | `nb_out_mean_prior_adv`, `nb_out_max_prior_adv`, `nb_out_frac_prior_adv` | aggregates over dependencies |
| | `nb_in_mean_prior_adv`, `nb_in_max_prior_adv`, `nb_in_frac_prior_adv` | aggregates over dependents |
| | `nb_out_mean_log_dependents`, `nb_in_mean_log_dependents` | |

Missing values: LightGBM handles NaN natively; for logistic regression and the GNN impute with train medians + missing flags, then standardise (train statistics only). Skewed counts are `log1p`-transformed.

## 5. Distribution shift (2026 surge) - required handling
Base rate in windows starting late 2025 is several times higher than in 2023-2024 (03 section 2.5). Therefore:
- Primary metrics are **ranking metrics** (precision@k, lift@k) and PR-AUC *reported next to the base rate of that split*; never compare raw PR-AUC across Test-A and Test-B without the base rate.
- Report calibration separately for Test-A and Test-B (Brier, reliability curve, expected calibration error). Expect miscalibration under shift; report it.
- The product displays a **relative risk band by score percentile within the universe** (default: High = top 2%, Medium = next 8%, Low = rest; configurable) plus the calibrated probability with a caveat. Do not present absolute probabilities as stable truths.
- Investigate the surge in `notebooks/03_eda_advisories` (monthly counts, package concentration, first-time packages) and record the findings in the model card.

## 6. Models (all trained on the same Train split, compared on the same Validation/Test splits)

| ID | Model | Notes |
|---|---|---|
| B0 | Random | expected PR-AUC = base rate |
| B1 | Rank by `n_prior_adv` (ties by `log_dependents`) | **the baseline that matters**: models must beat "has had advisories before" |
| B2 | Rank by `log_dependents` | popularity-only baseline |
| B3 | Rank by `n_prior_adv_365d` | recency baseline |
| M1 | Logistic regression (L2) on package/popularity/advisory/repo features | scikit-learn |
| M2 | LightGBM on the same features | tune on validation; consider `scale_pos_weight` |
| M3 | LightGBM + neighbour aggregates (graph group) | the strong baseline the GNN must beat |
| M4 | GNN: GraphSAGE and GAT variants (PyTorch Geometric) | below |
| C1 | Control: M3 with neighbour aggregates computed on a **degree-preserving shuffled graph** | tests whether real structure helps |
| C2 | Control: M4 trained on a shuffled graph | same, for the GNN |

### 6.1 GNN design (M4)
- Graph per `T`: nodes = `universe_T` packages; node features = the non-graph feature columns (standardised); edges from `edges.parquet` as `depends_on` plus reverse `depended_by` (two relation types with separate weights, summed in each layer; implement with two `SAGEConv`/`GATConv` per layer, or `HeteroConv`).
- Task: node-level binary classification (label at `T`). Loss: BCE with `pos_weight` or focal loss; choose on validation.
- Training: one graph per `T` in Train, full-batch per graph (tens of thousands of nodes; CPU or Colab). Inductive by construction (no node-id embeddings), so nodes unseen in training can be scored.
- Search space (small): layers {1, 2, 3}, hidden {32, 64, 128}, dropout {0.2, 0.4}, lr {1e-3, 3e-3}, GAT heads {2, 4}. Early stopping on validation primary metric. **5 seeds**; report mean and standard deviation.
- Outputs: node score in [0,1] per package.
- Explanations: GAT attention is *not* treated as an explanation of risk. Displayed "factors" always come from LightGBM per-feature contributions (`pred_contrib=True`) of M3/M2 and are labelled "approximate drivers" when the shipped scorer is the GNN.

## 7. Evaluation protocol (notebooks 05-08; code in `ml/eval/`)

Populations:
1. **All packages** in `universe_T`.
2. **First-time subset** (packages with no advisory `<= T`) - positives are first-ever advisories. This is the honest test, since history features are uninformative here.

Metrics per `T`, then mean +/- sd across `T` in a split: PR-AUC, precision@k and recall@k and **lift@k** for k in {100, 500}, ROC-AUC (secondary only), Brier score, reliability curve data, expected calibration error. Also report: base rate; stratified results by `log_dependents` decile; results per severity of the first advisory if available.

Ablations (notebook 07): M2 vs M3 vs M4; M3/M4 vs shuffled-graph controls C1/C2; GNN depth {1,2,3}; with/without repository features; with/without advisory-history features (shows how much the history dominates).

Primary selection metric: **lift@500 on the first-time subset, validation split**, ties -> precision@100. Secondary: all-packages PR-AUC.

## 8. Decision rules (honesty gates)
1. **Ship the risk layer** only if M2 or M3 beats B1 on the first-time subset on both Validation and Test-A. Otherwise ship the deterministic product with the risk overlay hidden or labelled "experimental", and record the negative result in the model card.
2. **Ship the GNN** only if it beats M3 on Validation *and* Test-A by more than the seed standard deviation **and** beats its shuffled-graph control C2. Otherwise ship M3 (or M2) and keep the GNN result in the model card as a finding.
3. Test-B is reported as a stress test; do not select models on it.
4. Never tune on Test-A/Test-B; never change the splits after seeing test results (log any change and mark the affected numbers).

## 9. Production scoring
1. Train the chosen model on all `T` with a full label window (up to 2026-04), using hyper-parameters selected on Validation (no test-set tuning).
2. Compute features for `T = NOW` (latest snapshot) and score `universe_NOW`.
3. Write `ml/artifacts/<model_version>/`:
   - `scores.parquet`: `package, score, risk_percentile, band, factors_json` (top 5 `{feature, contribution, value}`)
   - `metrics.json`: all evaluation results from section 7 (with base rates), split definitions, seeds
   - `config.yaml`, `split.json`, model files, `data_manifest.json` (input file names, sizes, SHA-256)
   - `model_version = YYYYMMDD-<git_sha7>-<model_id>`
4. `pipelines/load_to_db` loads scores, factors, `package_stats` and the `model_runs` row (05 section 7).

## 10. Notebook <-> code mapping
| Notebook | Purpose | Code that moves out |
|---|---|---|
| 01 | feasibility (done) | OSV ingest -> `pipelines/osv_ingest`; label stats -> `ml/features/build_labels.py` |
| 02 | graph EDA | graph helpers -> `ml/features/build_graph.py` |
| 03 | advisory EDA, surge analysis | - |
| 04 | feature engineering | `ml/features/build_features.py` |
| 05 | B0-B3, M1-M3 | `ml/models/baselines/*`, `ml/eval/{metrics,splits,stratified}.py` |
| 06 | M4 training | `ml/models/gnn/{sage,gat}.py` |
| 07 | evaluation + ablations | - |
| 08 | error analysis, calibration, model-card text | - |

## 11. Reproducibility and compute
Fixed seeds (Python, NumPy, PyTorch); log library versions into `metrics.json`; configs in `ml/configs/*.yaml` (`data.yaml`: universe size N, horizon, grid; `splits.yaml`; `baseline.yaml`; `gnn.yaml`). CPU or free Colab is sufficient; no multi-GPU. Keep Keras/TensorFlow out of the GNN path.

## 12. Model card (`docs/model_card.md` and `GET /api/model/card`)
Must contain: data sources and dates, universe definition and its selection-bias note, label definition, features, splits with embargo, results tables (with base rates) for all models and controls, calibration plots/numbers for Test-A and Test-B, stratified results, the surge discussion, limitations (03 section 5.2), intended use, and failure modes. Numbers are copied from `metrics.json`, never typed by hand.
