"""Structures de données partagées par tous les modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """Signal produit par une stratégie sur une bougie clôturée."""

    action: Action
    confidence: float  # 0.0 → 1.0
    reason: str
    timestamp: datetime
    price: float


@dataclass
class Fill:
    """Résultat d'un ordre exécuté (réel ou simulé)."""

    price: float
    qty: float
    fee: float


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_time: datetime
    stop_loss: float
    take_profit: float
    strategy: str
    entry_fee: float = 0.0

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.qty - self.entry_fee

    def notional(self, price: float) -> float:
        return self.qty * price


@dataclass
class Trade:
    """Trade clôturé, prêt à être journalisé et analysé."""

    symbol: str
    qty: float
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    fees: float
    exit_reason: str  # signal | stop_loss | take_profit | daily_loss_limit | kill_switch | end_of_backtest
    strategy: str

    @property
    def pnl(self) -> float:
        """P&L net de frais."""
        return (self.exit_price - self.entry_price) * self.qty - self.fees

    @property
    def pnl_pct(self) -> float:
        invested = self.entry_price * self.qty
        return self.pnl / invested * 100 if invested else 0.0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class OrderPlan:
    """Verdict du gestionnaire de risque sur une entrée proposée.

    Toute entrée en position DOIT passer par un OrderPlan approuvé —
    aucun module d'exécution ne calcule de taille lui-même.
    """

    approved: bool
    reason: str
    qty: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_amount: float = 0.0
