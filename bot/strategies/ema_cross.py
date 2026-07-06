"""Croisement EMA 9/21, filtre de tendance EMA 200, confirmation RSI.

Achat  : croisement haussier EMA rapide/lente, clôture au-dessus de
         l'EMA 200, RSI dans la zone de momentum sain [rsi_min, rsi_max].
Vente  : croisement baissier, ou RSI en surchauffe (> rsi_exit).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.indicators import ema, rsi
from bot.strategies.base import Strategy


class EmaCrossStrategy(Strategy):
    name = "ema_cross"

    @classmethod
    def default_params(cls) -> dict:
        return {
            "fast": 9,
            "slow": 21,
            "trend": 200,
            "rsi_period": 14,
            "rsi_min": 50,
            "rsi_max": 70,
            "rsi_exit": 75,
        }

    @property
    def warmup(self) -> int:
        return int(self.params["trend"]) + 10

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        fast = ema(close, p["fast"])
        slow = ema(close, p["slow"])
        trend = ema(close, p["trend"])
        rsi_v = rsi(close, p["rsi_period"])

        cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        uptrend = close > trend
        rsi_ok = rsi_v.between(p["rsi_min"], p["rsi_max"])

        buy = cross_up & uptrend & rsi_ok
        sell = cross_down | (rsi_v > p["rsi_exit"])

        signal = pd.Series(0, index=df.index, dtype=int)
        signal[buy] = 1
        signal[sell] = -1

        # Confiance : force de la tendance + position du RSI + écartement des EMAs
        trend_strength = ((close - trend) / close).clip(0, 0.05) / 0.05
        rsi_pos = ((rsi_v - p["rsi_min"]) / (p["rsi_max"] - p["rsi_min"])).clip(0, 1)
        separation = ((fast - slow).abs() / close / 0.005).clip(0, 1)
        confidence = (0.4 + 0.2 * trend_strength + 0.2 * rsi_pos + 0.2 * separation).fillna(0.0)
        confidence = confidence.where(signal != 0, 0.0)

        reason = np.select(
            [buy, sell],
            [
                "croisement haussier EMA" + f"{p['fast']}/{p['slow']}"
                + ", clôture > EMA" + str(p["trend"]) + ", RSI en zone momentum",
                "croisement baissier EMA ou RSI > " + str(p["rsi_exit"]),
            ],
            default="",
        )

        return pd.DataFrame(
            {"signal": signal, "confidence": confidence, "reason": reason}, index=df.index
        )
