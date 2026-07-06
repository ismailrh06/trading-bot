"""Indicateurs techniques en pandas pur (aucune dépendance TA-Lib).

Toutes les fonctions renvoient des séries alignées sur l'index d'entrée,
avec NaN pendant la période de chauffe (warmup) — jamais de valeur
extrapolée qui créerait un biais de lookahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder (lissage exponentiel alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.mask(avg_loss == 0)
    out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)  # aucune perte sur la période → RSI 100
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)  # marché plat → neutre
    return out.where(avg_gain.notna() & avg_loss.notna())  # NaN pendant le warmup


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (lissage de Wilder). df : colonnes high/low/close."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
