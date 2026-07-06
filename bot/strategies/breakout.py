"""Breakout de range avec confirmation de volume.

Achat  : clôture au-dessus du plus haut des `lookback` dernières bougies
         (bougie courante exclue), avec un volume ≥ volume_mult × la
         moyenne des volumes précédents.
Vente  : clôture sous le plus bas du range (cassure invalidée).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.strategies.base import Strategy


class BreakoutStrategy(Strategy):
    name = "breakout"

    @classmethod
    def default_params(cls) -> dict:
        return {
            "lookback": 20,
            "volume_lookback": 20,
            "volume_mult": 1.5,
        }

    @property
    def warmup(self) -> int:
        return max(int(self.params["lookback"]), int(self.params["volume_lookback"])) + 5

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        lb, vlb = int(p["lookback"]), int(p["volume_lookback"])

        # shift(1) : le range et le volume moyen excluent la bougie courante,
        # sinon un plus haut de la bougie elle-même masquerait sa propre cassure.
        range_high = df["high"].rolling(lb).max().shift(1)
        range_low = df["low"].rolling(lb).min().shift(1)
        vol_avg = df["volume"].rolling(vlb).mean().shift(1)
        vol_ratio = df["volume"] / vol_avg.mask(vol_avg == 0)

        buy = (df["close"] > range_high) & (vol_ratio >= p["volume_mult"])
        sell = df["close"] < range_low

        signal = pd.Series(0, index=df.index, dtype=int)
        signal[buy] = 1
        signal[sell] = -1

        # Confiance : excès de volume + marge de cassure relative à la largeur du range
        range_width = (range_high - range_low).mask(range_high == range_low)
        vol_excess = ((vol_ratio - p["volume_mult"]) / p["volume_mult"]).clip(0, 1)
        margin = ((df["close"] - range_high) / range_width).clip(0, 1)
        confidence = (0.4 + 0.3 * vol_excess + 0.3 * margin).fillna(0.0)
        confidence = confidence.where(signal != 0, 0.0)

        reason = np.select(
            [buy, sell],
            [
                f"cassure du plus haut {lb} bougies avec volume > "
                f"{p['volume_mult']}x la moyenne",
                f"clôture sous le plus bas {lb} bougies (cassure invalidée)",
            ],
            default="",
        )

        return pd.DataFrame(
            {"signal": signal, "confidence": confidence, "reason": reason}, index=df.index
        )
