"""Générateurs de données synthétiques pour les tests."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(closes: np.ndarray, volumes: np.ndarray | None = None,
               freq: str = "1h") -> pd.DataFrame:
    """Construit un DataFrame OHLCV cohérent à partir d'une série de clôtures."""
    n = len(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.001
    lows = np.minimum(opens, closes) * 0.999
    if volumes is None:
        volumes = np.full(n, 500.0)
    idx = pd.date_range("2023-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def random_walk(n: int = 600, seed: int = 42, drift: float = 0.0005) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
