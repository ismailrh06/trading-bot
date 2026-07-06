"""Tests du moteur de backtest : exécution réaliste, comptabilité exacte."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bot.backtest.engine import BacktestEngine
from bot.backtest.metrics import compute_stats
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy
from tests.helpers import make_ohlcv


class ScriptedStrategy(Strategy):
    """Stratégie de test : achète et vend à des index précis."""

    name = "scripted"

    def __init__(self, buy_at: list[int], sell_at: list[int], warmup_bars: int = 20):
        super().__init__({})
        self.buy_at = set(buy_at)
        self.sell_at = set(sell_at)
        self._warmup = warmup_bars

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @property
    def warmup(self) -> int:
        return self._warmup

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signal = pd.Series(0, index=df.index, dtype=int)
        for i in self.buy_at:
            signal.iloc[i] = 1
        for i in self.sell_at:
            signal.iloc[i] = -1
        return pd.DataFrame(
            {"signal": signal, "confidence": 1.0, "reason": "scripted"}, index=df.index
        )


def make_engine(tmp_path, strategy, **risk_overrides) -> BacktestEngine:
    cfg = {
        "risk_per_trade_pct": 1.0,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "min_risk_reward": 2.0,
        "daily_loss_limit_pct": 5.0,
        "max_open_positions": 3,
    }
    cfg.update(risk_overrides)
    risk = RiskManager(cfg, resume_next_day=True, state_dir=tmp_path)
    return BacktestEngine(
        strategy, risk, initial_capital=10_000, fee_pct=0.1, slippage_bps=5,
        min_confidence=0.5,
    )


def flat_series(n: int, price: float = 100.0, wiggle: float = 0.2) -> np.ndarray:
    rng = np.random.default_rng(9)
    return price + rng.normal(0, wiggle, n)


# --------------------------------------------------------------- exécution

def test_signal_executes_next_bar_open_with_slippage(tmp_path):
    """Pas de lookahead : signal au close de i → exécution à l'open de i+1."""
    df = make_ohlcv(flat_series(100))
    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50], sell_at=[60]))
    result = engine.run(df, "TEST/USDT")

    assert len(result.trades) == 1
    trade = result.trades[0]
    expected_entry = df["open"].iloc[51] * (1 + 5 / 10_000)
    assert math.isclose(trade.entry_price, expected_entry, rel_tol=1e-9)
    assert trade.entry_time == df.index[51].to_pydatetime()


def test_accounting_final_equity_equals_initial_plus_pnl(tmp_path):
    df = make_ohlcv(flat_series(200))
    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50, 120], sell_at=[80, 150]))
    result = engine.run(df, "TEST/USDT")

    assert len(result.trades) == 2
    expected = 10_000 + sum(t.pnl for t in result.trades)
    assert math.isclose(result.equity_curve.iloc[-1], expected, rel_tol=1e-9)


def test_fees_charged_both_sides(tmp_path):
    df = make_ohlcv(flat_series(100))
    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50], sell_at=[60]))
    result = engine.run(df, "TEST/USDT")

    trade = result.trades[0]
    expected_fees = trade.qty * (trade.entry_price + trade.exit_price) * 0.001
    assert math.isclose(trade.fees, expected_fees, rel_tol=1e-9)
    assert trade.fees > 0


def test_no_trades_before_warmup(tmp_path):
    df = make_ohlcv(flat_series(100))
    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[5], sell_at=[10], warmup_bars=50))
    result = engine.run(df, "TEST/USDT")
    assert result.trades == []


# ----------------------------------------------------------------- stops

def test_stop_loss_checked_before_take_profit_same_bar(tmp_path):
    """Bougie touchant SL et TP : hypothèse pessimiste, le stop gagne."""
    closes = flat_series(80, wiggle=0.1)
    df = make_ohlcv(closes)
    # bougie 60 : range énorme qui traverse à la fois le stop et la cible
    df.iloc[60, df.columns.get_loc("high")] = 130.0
    df.iloc[60, df.columns.get_loc("low")] = 70.0

    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50], sell_at=[]))
    result = engine.run(df, "TEST/USDT")

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].pnl < 0


def test_take_profit_hit(tmp_path):
    closes = flat_series(80, wiggle=0.1)
    df = make_ohlcv(closes)
    df.iloc[60, df.columns.get_loc("high")] = 130.0  # cible touchée, stop intact

    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50], sell_at=[]))
    result = engine.run(df, "TEST/USDT")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.pnl > 0
    # le TP est à ≥ 2× la distance du stop (2 × atr_stop_mult × ATR sous l'entrée)
    assert trade.exit_price > trade.entry_price


def test_open_position_closed_at_end_of_backtest(tmp_path):
    # série strictement plate : ni le stop ni la cible ne seront jamais touchés
    df = make_ohlcv(np.full(100, 100.0))
    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50], sell_at=[]))
    result = engine.run(df, "TEST/USDT")

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end_of_backtest"
    assert math.isclose(
        result.equity_curve.iloc[-1], 10_000 + result.trades[0].pnl, rel_tol=1e-9
    )


# ------------------------------------------------------------- perte du jour

def test_daily_loss_limit_halts_backtest_trading(tmp_path):
    """Krach intrajournalier : le bot coupe et n'ouvre plus de trade ce jour-là."""
    n = 120
    closes = flat_series(n, wiggle=0.05)
    closes[60:] = closes[59] * 0.80  # −20% d'un coup
    df = make_ohlcv(closes)
    # vrai gap baissier à l'ouverture de la bougie 60 : le prix saute PAR-DESSUS
    # le stop, la perte réelle dépasse donc largement le risque prévu
    gap_price = closes[59] * 0.80
    df.iloc[60, df.columns.get_loc("open")] = gap_price
    df.iloc[60, df.columns.get_loc("high")] = gap_price * 1.001
    df.iloc[60, df.columns.get_loc("low")] = gap_price * 0.999

    engine = make_engine(
        tmp_path,
        # achat à chaque bougie si possible → sans halte, il rentrerait sans cesse
        ScriptedStrategy(buy_at=list(range(20, n - 1)), sell_at=[]),
        risk_per_trade_pct=2.0,
        max_position_pct=100.0,
    )
    engine.risk.resume_next_day = False
    result = engine.run(df, "TEST/USDT")

    assert engine.risk.halted, "la limite de perte journalière n'a pas déclenché"
    crash_day = df.index[60].date()
    reentries_after_halt = [
        t for t in result.trades
        if t.entry_time.date() == crash_day and t.entry_time > df.index[61].to_pydatetime()
    ]
    assert reentries_after_halt == [], "le bot a continué à trader après l'arrêt"


# ----------------------------------------------------------------- métriques

def test_stats_report_losses_honestly(tmp_path):
    """Un backtest perdant doit afficher un rendement négatif, pas le masquer."""
    n = 300
    closes = np.linspace(100, 60, n) + np.random.default_rng(3).normal(0, 0.3, n)
    df = make_ohlcv(closes)
    engine = make_engine(tmp_path, ScriptedStrategy(buy_at=[50, 150], sell_at=[100, 250]))
    result = engine.run(df, "TEST/USDT")
    stats = compute_stats(result.equity_curve, result.trades, 10_000, "1h")

    assert stats["total_return_pct"] < 0
    assert stats["max_drawdown_pct"] < 0
    assert stats["n_trades"] == len(result.trades)
    assert (stats["monthly_returns"] < 0).any()
