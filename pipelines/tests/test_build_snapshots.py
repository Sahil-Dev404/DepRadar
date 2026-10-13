"""Unit and leakage tests for point-in-time snapshot reconstruction."""

from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from ml.features.build_graph import build_snapshot_graph
from ml.features.build_labels import build_snapshot_labels
from ml.features.build_features import build_snapshot_features


def test_build_snapshot_graph_logic():
    universe = ["pkg-a", "pkg-b", "pkg-c"]
    T = pd.Timestamp("2024-01-01", tz="UTC")

    # pkg-a has 2 versions: 1.0 (rel, published earlier), 2.0 (rel, published later)
    # pkg-b has 1 version: 1.0 (rel)
    # pkg-c has 1 version: 1.0 (prerelease, should be excluded if is_release filtered)
    versions = pd.DataFrame([
        {"name": "pkg-a", "version": "1.0", "published_at": pd.Timestamp("2023-01-01", tz="UTC"), "is_release": True, "ordinal": 1},
        {"name": "pkg-a", "version": "2.0", "published_at": pd.Timestamp("2023-06-01", tz="UTC"), "is_release": True, "ordinal": 2},
        {"name": "pkg-b", "version": "1.0", "published_at": pd.Timestamp("2023-01-01", tz="UTC"), "is_release": True, "ordinal": 1},
        {"name": "pkg-c", "version": "1.0", "published_at": pd.Timestamp("2023-01-01", tz="UTC"), "is_release": False, "ordinal": 1},
    ])

    # deps: pkg-a v2.0 depends on pkg-b and pkg-a (self-loop) and outside-pkg
    # pkg-a v1.0 depends on pkg-c
    version_deps = pd.DataFrame([
        {"name": "pkg-a", "version": "2.0", "dep_name": "pkg-b"},
        {"name": "pkg-a", "version": "2.0", "dep_name": "pkg-a"},        # self-loop
        {"name": "pkg-a", "version": "2.0", "dep_name": "outside-pkg"},  # outside universe
        {"name": "pkg-b", "version": "1.0", "dep_name": "pkg-a"},
    ])

    vis_rel = versions[versions["is_release"]]
    nodes, edges = build_snapshot_graph(universe, vis_rel, version_deps)

    assert len(nodes) == 3
    assert (nodes["idx"].values == np.array([0, 1, 2])).all()
    # Edges should have pkg-a -> pkg-b (0 -> 1) and pkg-b -> pkg-a (1 -> 0)
    assert len(edges) == 2
    assert set(zip(edges["src"], edges["dst"])) == {(0, 1), (1, 0)}


def test_build_snapshot_labels_logic():
    nodes = pd.DataFrame({"package": ["pkg-a", "pkg-b", "pkg-c"], "idx": [0, 1, 2]})
    T = pd.Timestamp("2024-01-01", tz="UTC")

    # pkg-a: prior vuln at 2023-06-01, future vuln at 2024-03-01
    # pkg-b: withdrawn vuln in future window (should be ignored)
    # pkg-c: future vuln at 2024-05-01
    advisories = pd.DataFrame([
        {"package": "pkg-a", "published": pd.Timestamp("2023-06-01", tz="UTC"), "kind": "vulnerability", "withdrawn": None},
        {"package": "pkg-a", "published": pd.Timestamp("2024-03-01", tz="UTC"), "kind": "vulnerability", "withdrawn": None},
        {"package": "pkg-b", "published": pd.Timestamp("2024-03-01", tz="UTC"), "kind": "vulnerability", "withdrawn": pd.Timestamp("2024-03-02", tz="UTC")},
        {"package": "pkg-c", "published": pd.Timestamp("2024-05-01", tz="UTC"), "kind": "vulnerability", "withdrawn": None},
    ])

    labels = build_snapshot_labels(nodes, advisories, T, horizon_months=6)

    assert labels.loc[labels["package"] == "pkg-a", "label"].iloc[0] == 1
    assert labels.loc[labels["package"] == "pkg-a", "had_prior_advisory"].iloc[0] == True
    assert labels.loc[labels["package"] == "pkg-a", "n_adv_in_window"].iloc[0] == 1

    assert labels.loc[labels["package"] == "pkg-b", "label"].iloc[0] == 0
    assert labels.loc[labels["package"] == "pkg-b", "had_prior_advisory"].iloc[0] == False

    assert labels.loc[labels["package"] == "pkg-c", "label"].iloc[0] == 1
    assert labels.loc[labels["package"] == "pkg-c", "had_prior_advisory"].iloc[0] == False


def test_leakage_property_test():
    """Property test: shifting T earlier must never increase count features."""
    nodes = pd.DataFrame({"package": ["pkg-a"], "idx": [0]})
    edges = pd.DataFrame(columns=["src", "dst"], dtype=np.int64)
    versions = pd.DataFrame([
        {"name": "pkg-a", "version": "1.0", "published_at": pd.Timestamp("2023-01-01", tz="UTC"), "is_release": True, "ordinal": 1},
        {"name": "pkg-a", "version": "2.0", "published_at": pd.Timestamp("2023-08-01", tz="UTC"), "is_release": True, "ordinal": 2},
    ])
    advisories = pd.DataFrame([
        {"package": "pkg-a", "published": pd.Timestamp("2023-05-01", tz="UTC"), "kind": "vulnerability", "withdrawn": None, "severity": "HIGH"},
    ])

    T_late = pd.Timestamp("2024-01-01", tz="UTC")
    T_early = pd.Timestamp("2023-06-01", tz="UTC")

    f_late = build_snapshot_features(
        nodes, edges, versions[versions["published_at"] <= T_late], None, advisories, T_late
    )
    f_early = build_snapshot_features(
        nodes, edges, versions[versions["published_at"] <= T_early], None, advisories, T_early
    )

    # Shifting T earlier: n_versions and n_prior_adv must be <= late
    assert f_early["n_versions"].iloc[0] <= f_late["n_versions"].iloc[0]
    assert f_early["n_prior_adv"].iloc[0] <= f_late["n_prior_adv"].iloc[0]
    assert f_early["releases_365d"].iloc[0] <= f_late["releases_365d"].iloc[0]
