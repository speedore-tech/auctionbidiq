"""Small, dependency-free statistics: percentiles, ECDF, OLS with prediction intervals."""
from __future__ import annotations

import math


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile, q in [0, 100]."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def ecdf(values: list[float], x: float) -> float:
    """Share of values <= x."""
    if not values:
        return 0.0
    return sum(1 for v in values if v <= x) / len(values)


def norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _invert(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            return None
        a[c], a[piv] = a[piv], a[c]
        p = a[c][c]
        a[c] = [x / p for x in a[c]]
        for r in range(n):
            if r != c and a[r][c] != 0:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [row[n:] for row in a]


class OLS:
    """y = X·b (X includes the intercept column). Exposes residual std and leverage-aware PIs."""

    def __init__(self, X: list[list[float]], y: list[float]):
        self.n = len(y)
        self.k = len(X[0])
        xtx = [[sum(r[i] * r[j] for r in X) for j in range(self.k)] for i in range(self.k)]
        self.xtx_inv = _invert(xtx)
        if self.xtx_inv is None:
            raise ValueError("singular design matrix")
        xty = [sum(r[i] * yy for r, yy in zip(X, y)) for i in range(self.k)]
        self.coef = [sum(self.xtx_inv[i][j] * xty[j] for j in range(self.k)) for i in range(self.k)]
        resid = [yy - self.predict_point(r) for r, yy in zip(X, y)]
        dof = max(1, self.n - self.k)
        self.sigma = math.sqrt(sum(e * e for e in resid) / dof)
        ybar = sum(y) / self.n
        sst = sum((yy - ybar) ** 2 for yy in y) or 1.0
        self.r2 = 1 - sum(e * e for e in resid) / sst

    def predict_point(self, x: list[float]) -> float:
        return sum(c * v for c, v in zip(self.coef, x))

    def predict_se(self, x: list[float]) -> float:
        h = sum(x[i] * self.xtx_inv[i][j] * x[j] for i in range(self.k) for j in range(self.k))
        return self.sigma * math.sqrt(1 + max(0.0, h))
