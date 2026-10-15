import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import SAGEConv, GATConv

def relation_edges(src, dst):
    """Edge `src depends_on dst`. PyG sends messages from row 0 to row 1.
    ei_from_deps: dependency -> dependent (each node aggregates over what it depends on).
    ei_from_dependents: dependent -> dependency (each node aggregates over what depends on it)."""
    src = np.asarray(src, dtype=np.int64)
    dst = np.asarray(dst, dtype=np.int64)
    return np.stack([dst, src]), np.stack([src, dst])

class RelGNN(nn.Module):
    def __init__(self, in_dim, hidden, layers, dropout, arch="sage", heads=2):
        super().__init__()
        self.dropout = dropout
        self.deps, self.dependents = nn.ModuleList(), nn.ModuleList()
        d = in_dim
        for _ in range(layers):
            if arch == "sage":
                self.deps.append(SAGEConv(d, hidden, aggr="mean"))
                self.dependents.append(SAGEConv(d, hidden, aggr="mean"))
            else:
                self.deps.append(GATConv(d, hidden // heads, heads=heads))
                self.dependents.append(GATConv(d, hidden // heads, heads=heads))
            d = hidden
        self.out = nn.Linear(hidden, 1)

    def forward(self, x, ei_from_deps, ei_from_dependents):
        h = x
        for cd, cr in zip(self.deps, self.dependents):
            h = F.relu(cd(h, ei_from_deps) + cr(h, ei_from_dependents))
            h = F.dropout(h, p=self.dropout, training=self.training)
        return self.out(h).squeeze(-1)
