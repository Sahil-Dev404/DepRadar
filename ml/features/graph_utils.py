"""Graph utilities for degree-preserving edge shuffling."""
import numpy as np
import pandas as pd

def shuffle_edges_keep_degrees(src, dst, seed=0):
    """Permute the destination column. Preserves node in-degree and out-degree.
    Self-loops and duplicate edges created by the shuffle are removed."""
    src, dst = np.asarray(src), np.asarray(dst)
    d = np.random.default_rng(seed).permutation(dst)
    keep = src != d
    e = pd.DataFrame({"s": src[keep], "d": d[keep]})
    n_before = len(e)
    e = e.drop_duplicates()
    return e["s"].to_numpy(), e["d"].to_numpy(), {
        "self_loops_removed": int((~keep).sum()),
        "duplicates_removed": int(n_before - len(e))
    }

def nb_mean_max(n, src, dst, value):
    """Mean and max of value over out-neighbours and in-neighbours."""
    value = np.asarray(value, float)
    res = {}
    for name, a, b in (("out", src, dst), ("in", dst, src)):
        g = pd.DataFrame({"i": a, "v": value[b]}).groupby("i")["v"]
        res[f"{name}_mean"] = g.mean().reindex(range(n)).to_numpy()
        res[f"{name}_max"] = g.max().reindex(range(n)).to_numpy()
    return res
