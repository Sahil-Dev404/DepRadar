"""Feature preprocessor for log1p, median imputation, missing flags, and standardisation."""
import numpy as np
import pandas as pd

class Preprocessor:
    """Fit on the TRAIN split only; transform any split with stored statistics."""
    def __init__(self, numeric=None, log1p_cols=()):
        self.numeric = list(numeric) if numeric is not None else None
        self.log1p_cols = set(log1p_cols)

    def _base(self, df):
        cols = self.numeric if self.numeric is not None else list(df.columns)
        X = df[cols].astype(float).copy()
        for c in self.log1p_cols & set(cols):
            X[c] = np.log1p(X[c].clip(lower=0))
        return X

    def fit(self, df):
        if self.numeric is None:
            self.numeric = list(df.columns)
        X = self._base(df)
        self.flag_cols_ = [c for c in self.numeric if X[c].isna().any()]
        self.median_ = X.median().fillna(0.0)
        Xf = X.fillna(self.median_)
        self.mean_ = Xf.mean().fillna(0.0)
        self.std_ = Xf.std(ddof=0).replace(0, 1.0).fillna(1.0)
        self.fitted_ = True
        return self

    def transform(self, df):
        X = self._base(df)
        flags = pd.DataFrame({c + "_missing": X[c].isna().astype(float) for c in self.flag_cols_}, index=X.index)
        X = (X.fillna(self.median_) - self.mean_) / self.std_
        return pd.concat([X, flags], axis=1)
