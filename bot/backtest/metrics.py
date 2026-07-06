"""Métriques de performance du backtest — résultats bruts, sans embellissement."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.models import Trade
from bot.utils import periods_per_year


def compute_stats(
    equity: pd.Series, trades: list[Trade], initial_capital: float, timeframe: str
) -> dict:
    final_equity = float(equity.iloc[-1])
    total_return_pct = (final_equity / initial_capital - 1) * 100

    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_drawdown_pct = float(drawdown.min()) * 100

    returns = equity.pct_change().dropna()
    ppy = periods_per_year(timeframe)
    if len(returns) > 1 and returns.std() > 0:
        sharpe = float(returns.mean() / returns.std() * np.sqrt(ppy))
    else:
        sharpe = 0.0

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    n_days = max((equity.index[-1] - equity.index[0]).days, 1)

    return {
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe,
        "n_trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": profit_factor,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "best_trade": max((t.pnl for t in trades), default=0.0),
        "worst_trade": min((t.pnl for t in trades), default=0.0),
        "total_fees": sum(t.fees for t in trades),
        "duration_days": n_days,
        "monthly_returns": monthly_returns(equity, initial_capital),
        "drawdown_series": drawdown,
    }


def monthly_returns(equity: pd.Series, initial_capital: float) -> pd.Series:
    """Rendement de chaque mois calendaire, en % (mois de pertes inclus)."""
    month_end = equity.resample("ME").last()
    prev = month_end.shift(1)
    prev.iloc[0] = initial_capital
    out = (month_end / prev - 1) * 100
    out.index = out.index.strftime("%Y-%m")
    return out


def format_report(stats: dict, symbol: str, strategy_name: str) -> str:
    """Rapport texte affiché en console et sauvegardé dans reports/."""
    pf = stats["profit_factor"]
    pf_str = f"{pf:.2f}" if np.isfinite(pf) else "∞ (aucune perte)"
    lines = [
        "=" * 62,
        f"  BACKTEST — {symbol} — stratégie « {strategy_name} »",
        "=" * 62,
        f"  Capital initial        : {stats['initial_capital']:>14,.2f}",
        f"  Capital final          : {stats['final_equity']:>14,.2f}",
        f"  Rendement total        : {stats['total_return_pct']:>13.2f} %",
        f"  Drawdown maximum       : {stats['max_drawdown_pct']:>13.2f} %",
        f"  Ratio de Sharpe        : {stats['sharpe_ratio']:>14.2f}",
        f"  Nombre de trades       : {stats['n_trades']:>14d}",
        f"  Taux de réussite       : {stats['win_rate_pct']:>13.2f} %",
        f"  Profit factor          : {pf_str:>14}",
        f"  Gain moyen / trade     : {stats['avg_win']:>14,.2f}",
        f"  Perte moyenne / trade  : {stats['avg_loss']:>14,.2f}",
        f"  Meilleur trade         : {stats['best_trade']:>14,.2f}",
        f"  Pire trade             : {stats['worst_trade']:>14,.2f}",
        f"  Frais totaux payés     : {stats['total_fees']:>14,.2f}",
        f"  Durée                  : {stats['duration_days']:>9d} jours",
        "-" * 62,
        "  Rendements mensuels (%) — les mois négatifs comptent aussi :",
    ]
    for month, ret in stats["monthly_returns"].items():
        bar_len = min(int(abs(ret) * 2), 30)
        bar = ("+" if ret >= 0 else "-") * bar_len
        lines.append(f"    {month}  {ret:>+7.2f}  {bar}")
    lines.append("=" * 62)
    return "\n".join(lines)
