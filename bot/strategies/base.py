"""Classe abstraite Strategy — contrat commun backtest / temps réel.

Une stratégie reçoit un DataFrame OHLCV (index datetime UTC, colonnes
open/high/low/close/volume, uniquement des bougies CLÔTURÉES) et produit
pour chaque bougie : signal (1=BUY, -1=SELL, 0=HOLD), confidence (0..1)
et reason (texte expliquant la décision, pour les logs).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from bot.models import Action, Signal


class Strategy(ABC):
    name: str = "base"

    def __init__(self, params: dict | None = None):
        self.params = {**self.default_params(), **(params or {})}

    @classmethod
    @abstractmethod
    def default_params(cls) -> dict:
        """Paramètres par défaut, surchargés par config.yaml."""

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Nombre de bougies nécessaires avant le premier signal fiable."""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Version vectorisée pour le backtest.

        Retourne un DataFrame aligné sur df avec les colonnes :
        signal (int), confidence (float), reason (str).
        Ne doit utiliser QUE des données passées ou courantes pour
        chaque ligne — jamais de shift(-n) (biais de lookahead).
        """

    def latest_signal(self, df: pd.DataFrame) -> Signal:
        """Signal sur la dernière bougie clôturée — utilisé en temps réel."""
        if len(df) < self.warmup:
            return Signal(
                action=Action.HOLD,
                confidence=0.0,
                reason=f"historique insuffisant ({len(df)}/{self.warmup} bougies)",
                timestamp=df.index[-1].to_pydatetime(),
                price=float(df["close"].iloc[-1]),
            )
        out = self.generate_signals(df)
        row = out.iloc[-1]
        action = {1: Action.BUY, -1: Action.SELL}.get(int(row["signal"]), Action.HOLD)
        return Signal(
            action=action,
            confidence=float(row["confidence"]),
            reason=str(row["reason"]) or "aucune condition remplie",
            timestamp=df.index[-1].to_pydatetime(),
            price=float(df["close"].iloc[-1]),
        )
