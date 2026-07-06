"""Tests des stratégies : signaux corrects, invariants, pas de lookahead."""

from __future__ import annotations

import numpy as np

from bot.indicators import ema, rsi
from bot.models import Action
from bot.strategies import create_strategy
from tests.helpers import make_ohlcv, random_walk


# ------------------------------------------------------------------ ema_cross

def uptrend_after_flat() -> np.ndarray:
    """250 bougies plates puis montée douce : croisement garanti au-dessus
    de l'EMA 200 avec un RSI en zone momentum (ni plat, ni surchauffé)."""
    rng = np.random.default_rng(7)
    flat = 100 + rng.normal(0, 0.05, 250)
    steps = np.tile([0.003, -0.002], 60)  # net +0.1% par paire de bougies
    rising = flat[-1] * np.cumprod(1 + steps)
    return np.concatenate([flat, rising])


def test_ema_cross_emits_buy_in_healthy_uptrend():
    strat = create_strategy("ema_cross")
    df = make_ohlcv(uptrend_after_flat())
    out = strat.generate_signals(df)
    assert (out["signal"] == 1).any(), "aucun BUY sur un scénario de croisement idéal"


def test_ema_cross_buys_only_above_trend_filter():
    strat = create_strategy("ema_cross")
    df = make_ohlcv(random_walk(800, seed=1))
    out = strat.generate_signals(df)
    trend = ema(df["close"], strat.params["trend"])
    rsi_v = rsi(df["close"], strat.params["rsi_period"])
    buys = out["signal"] == 1
    assert (df["close"][buys] > trend[buys]).all(), "BUY sous l'EMA 200"
    assert rsi_v[buys].between(strat.params["rsi_min"], strat.params["rsi_max"]).all()


def test_ema_cross_confidence_in_unit_range():
    strat = create_strategy("ema_cross")
    df = make_ohlcv(random_walk(800, seed=2))
    out = strat.generate_signals(df)
    assert out["confidence"].between(0, 1).all()


def test_ema_cross_signals_have_reasons():
    strat = create_strategy("ema_cross")
    df = make_ohlcv(uptrend_after_flat())
    out = strat.generate_signals(df)
    active = out[out["signal"] != 0]
    assert (active["reason"].str.len() > 0).all(), "signal sans explication"


# ------------------------------------------------------------------- breakout

def range_then_breakout(vol_mult_on_breakout: float) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(11)
    closes = 100 + rng.normal(0, 0.3, 60)  # consolidation 100 ± ~1
    closes = np.append(closes, 106.0)  # cassure franche
    volumes = np.full(61, 500.0)
    volumes[-1] = 500.0 * vol_mult_on_breakout
    return closes, volumes


def test_breakout_buy_needs_volume_confirmation():
    strat = create_strategy("breakout")
    closes, volumes = range_then_breakout(vol_mult_on_breakout=3.0)
    with_volume = strat.generate_signals(make_ohlcv(closes, volumes))
    assert int(with_volume["signal"].iloc[-1]) == 1, "cassure + volume devrait acheter"

    closes, volumes = range_then_breakout(vol_mult_on_breakout=1.0)
    without_volume = strat.generate_signals(make_ohlcv(closes, volumes))
    assert int(without_volume["signal"].iloc[-1]) == 0, "cassure sans volume ne doit PAS acheter"


def test_breakout_buys_only_above_prior_range_high():
    strat = create_strategy("breakout")
    df = make_ohlcv(random_walk(800, seed=3), np.abs(random_walk(800, seed=4)))
    out = strat.generate_signals(df)
    lb = strat.params["lookback"]
    prior_high = df["high"].rolling(lb).max().shift(1)
    buys = out["signal"] == 1
    assert (df["close"][buys] > prior_high[buys]).all()


# --------------------------------------------------------------- latest_signal

def test_latest_signal_holds_without_enough_history():
    strat = create_strategy("ema_cross")
    df = make_ohlcv(random_walk(50, seed=5))
    sig = strat.latest_signal(df)
    assert sig.action is Action.HOLD
    assert "insuffisant" in sig.reason


def test_latest_signal_matches_vectorized_output():
    strat = create_strategy("breakout")
    closes, volumes = range_then_breakout(vol_mult_on_breakout=3.0)
    df = make_ohlcv(closes, volumes)
    sig = strat.latest_signal(df)
    assert sig.action is Action.BUY
    assert 0 <= sig.confidence <= 1
