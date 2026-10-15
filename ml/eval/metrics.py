"""Evaluation metrics functions for ranking, PR-AUC, and calibration."""
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss

KS = (100, 500)

def rank_order(score, seed=0):
    """Indices from highest to lowest score; ties broken by seeded random key."""
    score = np.asarray(score, float)
    key = np.random.default_rng(seed).random(len(score))
    return np.lexsort((key, -np.nan_to_num(score, nan=-np.inf)))

def topk_metrics(score, y, ks=KS, seed=0):
    """Compute precision@k, recall@k and lift@k."""
    y = np.asarray(y).astype(int)
    n = len(y)
    pos = int(y.sum())
    base = pos / n if n else float("nan")
    order = rank_order(score, seed)
    out = {}
    for k in ks:
        ke = min(k, n)
        hits = int(y[order[:ke]].sum()) if ke else 0
        p = hits / ke if ke else float("nan")
        out[f"precision@{k}"] = p
        out[f"recall@{k}"] = hits / pos if pos else float("nan")
        out[f"lift@{k}"] = p / base if base and base > 0 else float("nan")
    return out

def reliability(prob, y, bins=10):
    """Equal-count (quantile) calibration bins and expected calibration error (ECE)."""
    prob = np.asarray(prob, float)
    y = np.asarray(y).astype(float)
    df = pd.DataFrame({"p": prob, "y": y})
    df["bin"] = pd.qcut(df["p"].rank(method="first"), bins, labels=False, duplicates="drop")
    t = df.groupby("bin").agg(mean_pred=("p", "mean"), frac_pos=("y", "mean"), count=("y", "size")).reset_index(drop=True)
    ece = float((t["count"] / t["count"].sum() * (t["mean_pred"] - t["frac_pos"]).abs()).sum())
    return t, ece

def metrics_for_date(score, y, prob=None, seed=0):
    """Compute metrics for one date and one population."""
    y = np.asarray(y).astype(int)
    n = len(y)
    pos = int(y.sum())
    m = {"n": n, "n_pos": pos, "base_rate": pos / n if n else float("nan")}
    ok = 0 < pos < n
    m["pr_auc"] = float(average_precision_score(y, score)) if ok else float("nan")
    m["roc_auc"] = float(roc_auc_score(y, score)) if ok else float("nan")
    m.update(topk_metrics(score, y, seed=seed))
    if prob is not None and n:
        m["brier"] = float(brier_score_loss(y, prob))
        m["ece"] = reliability(prob, y)[1]
    else:
        m["brier"] = float("nan")
        m["ece"] = float("nan")
    return m
