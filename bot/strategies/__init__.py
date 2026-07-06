"""Registre des stratégies — ajouter une stratégie = une entrée ici."""

from __future__ import annotations

from bot.strategies.base import Strategy
from bot.strategies.breakout import BreakoutStrategy
from bot.strategies.ema_cross import EmaCrossStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    EmaCrossStrategy.name: EmaCrossStrategy,
    BreakoutStrategy.name: BreakoutStrategy,
}


def create_strategy(name: str, params: dict | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"Stratégie inconnue : {name!r} — disponibles : {sorted(STRATEGIES)}")
    return STRATEGIES[name](params)
