"""Tests du module de risque — le module non contournable."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from bot.risk.manager import MAX_RISK_PER_TRADE_PCT, RiskManager


def make_rm(tmp_path, **overrides) -> RiskManager:
    cfg = {
        "risk_per_trade_pct": 1.0,
        "max_position_pct": 25.0,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "min_risk_reward": 2.0,
        "daily_loss_limit_pct": 5.0,
        "max_open_positions": 3,
    }
    resume = overrides.pop("resume_next_day", False)
    cfg.update(overrides)
    return RiskManager(cfg, resume_next_day=resume, state_dir=tmp_path)


def entry_kwargs(**overrides) -> dict:
    base = dict(
        equity=10_000.0,
        cash=10_000.0,
        entry_price=100.0,
        atr_value=2.5,
        open_positions=0,
        confidence=0.9,
        min_confidence=0.5,
    )
    base.update(overrides)
    return base


# ------------------------------------------------------------ taille & stops

def test_position_size_risks_configured_fraction(tmp_path):
    rm = make_rm(tmp_path)
    qty = rm.position_size(equity=10_000, entry_price=100, stop_price=95)
    # risque = 1% de 10 000 = 100 ; distance au stop = 5 → qty = 20
    assert math.isclose(qty, 20.0)


def test_position_size_capped_by_max_position_pct(tmp_path):
    rm = make_rm(tmp_path, max_position_pct=10.0)
    # stop très serré → qty théorique énorme, plafonnée à 10% du capital
    qty = rm.position_size(equity=10_000, entry_price=100, stop_price=99.9)
    assert math.isclose(qty * 100, 1_000.0)  # 10% de 10 000


def test_risk_per_trade_hard_capped_at_2_pct(tmp_path):
    rm = make_rm(tmp_path, risk_per_trade_pct=10.0)
    assert rm.risk_per_trade_pct == MAX_RISK_PER_TRADE_PCT


def test_stop_uses_atr_and_rr_at_least_1_to_2(tmp_path):
    rm = make_rm(tmp_path, min_risk_reward=1.0)  # tentative de RR 1:1…
    stop, target = rm.stop_and_target(entry_price=100.0, atr_value=2.0)
    assert math.isclose(stop, 96.0)  # 100 − 2×ATR
    # …refusée : le RR reste ≥ 1:2
    assert (target - 100.0) >= 2.0 * (100.0 - stop) - 1e-9


def test_approved_plan_has_stop_and_target(tmp_path):
    rm = make_rm(tmp_path)
    plan = rm.evaluate_entry(**entry_kwargs())
    assert plan.approved
    assert 0 < plan.stop_loss < 100.0 < plan.take_profit
    assert plan.qty > 0
    # risque effectif ≤ 1% du capital
    assert plan.risk_amount <= 10_000 * 0.01 + 1e-6


def test_qty_never_exceeds_cash(tmp_path):
    rm = make_rm(tmp_path)
    plan = rm.evaluate_entry(**entry_kwargs(cash=500.0))
    assert plan.approved
    assert plan.qty * 100.0 <= 500.0


# --------------------------------------------------------------------- refus

def test_rejects_when_max_positions_reached(tmp_path):
    rm = make_rm(tmp_path)
    plan = rm.evaluate_entry(**entry_kwargs(open_positions=3))
    assert not plan.approved
    assert "positions" in plan.reason


def test_rejects_low_confidence(tmp_path):
    rm = make_rm(tmp_path)
    plan = rm.evaluate_entry(**entry_kwargs(confidence=0.3))
    assert not plan.approved


def test_rejects_without_atr(tmp_path):
    rm = make_rm(tmp_path)
    assert not rm.evaluate_entry(**entry_kwargs(atr_value=float("nan"))).approved
    assert not rm.evaluate_entry(**entry_kwargs(atr_value=0.0)).approved


# ------------------------------------------------------ limite de perte/jour

def test_daily_loss_halts_trading(tmp_path):
    rm = make_rm(tmp_path)
    t0 = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    assert rm.check_daily_loss(t0, 10_000) is False  # début de journée
    assert rm.check_daily_loss(t0 + timedelta(hours=1), 9_600) is False  # −4%
    assert rm.check_daily_loss(t0 + timedelta(hours=2), 9_400) is True  # −6% → arrêt
    assert rm.halted
    assert not rm.evaluate_entry(**entry_kwargs()).approved


def test_daily_loss_alert_fires_only_once(tmp_path):
    rm = make_rm(tmp_path)
    t0 = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    rm.check_daily_loss(t0, 10_000)
    assert rm.check_daily_loss(t0 + timedelta(hours=1), 9_000) is True
    assert rm.check_daily_loss(t0 + timedelta(hours=2), 8_900) is False  # déjà arrêté


def test_daily_loss_stays_halted_without_resume(tmp_path):
    rm = make_rm(tmp_path, resume_next_day=False)
    t0 = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    rm.check_daily_loss(t0, 10_000)
    rm.check_daily_loss(t0 + timedelta(hours=1), 9_000)
    rm.check_daily_loss(t0 + timedelta(days=1), 9_000)
    assert rm.halted  # en réel : redémarrage manuel obligatoire


def test_daily_loss_resumes_next_day_in_backtest(tmp_path):
    rm = make_rm(tmp_path, resume_next_day=True)
    t0 = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    rm.check_daily_loss(t0, 10_000)
    rm.check_daily_loss(t0 + timedelta(hours=1), 9_000)
    assert rm.halted
    rm.check_daily_loss(t0 + timedelta(days=1), 9_000)
    assert not rm.halted


# ----------------------------------------------------------------- kill switch

def test_kill_switch_blocks_entries(tmp_path):
    rm = make_rm(tmp_path)
    assert not rm.kill_switch_active()
    rm.activate_kill_switch("test")
    assert rm.kill_switch_active()
    assert not rm.evaluate_entry(**entry_kwargs()).approved
    rm.clear_kill_switch()
    assert rm.evaluate_entry(**entry_kwargs()).approved
