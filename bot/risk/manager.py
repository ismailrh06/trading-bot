"""Gestion du risque — module obligatoire, non contournable.

Toute entrée en position passe par evaluate_entry(), qui rend un OrderPlan
approuvé ou rejeté. Ni le backtest ni l'exécution temps réel ne calculent
de taille de position eux-mêmes. Les règles :

- risque par trade plafonné en dur à MAX_RISK_PER_TRADE_PCT (2%) ;
- stop-loss ATR obligatoire sur chaque position ;
- take-profit à un ratio risque/rendement d'au moins 1:2 ;
- limite de perte journalière → arrêt complet du trading + alerte ;
- nombre maximal de positions simultanées ;
- kill switch (fichier state/KILL) → tout couper, fermer les positions.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from pathlib import Path

from bot.models import OrderPlan

log = logging.getLogger(__name__)

MAX_RISK_PER_TRADE_PCT = 2.0  # plafond dur — la config ne peut pas le dépasser
MIN_RISK_REWARD = 2.0  # ratio risque/rendement minimal — jamais en dessous

KILL_FILE = "KILL"


class RiskManager:
    def __init__(self, cfg: dict, *, resume_next_day: bool = False, state_dir: str = "state"):
        requested_risk = float(cfg.get("risk_per_trade_pct", 1.0))
        self.risk_per_trade_pct = min(requested_risk, MAX_RISK_PER_TRADE_PCT)
        if requested_risk > MAX_RISK_PER_TRADE_PCT:
            log.warning(
                "risk_per_trade_pct=%.2f%% dépasse le plafond dur — ramené à %.2f%%",
                requested_risk,
                MAX_RISK_PER_TRADE_PCT,
            )
        self.max_position_pct = float(cfg.get("max_position_pct", 25.0))
        self.atr_period = int(cfg.get("atr_period", 14))
        self.atr_stop_mult = float(cfg.get("atr_stop_mult", 2.0))
        self.min_risk_reward = max(float(cfg.get("min_risk_reward", MIN_RISK_REWARD)), MIN_RISK_REWARD)
        self.daily_loss_limit_pct = float(cfg.get("daily_loss_limit_pct", 5.0))
        self.max_open_positions = int(cfg.get("max_open_positions", 3))
        self.close_positions_on_halt = bool(cfg.get("close_positions_on_halt", True))

        # resume_next_day=True en backtest (sinon un seul mauvais jour arrête
        # tout le test) ; False en paper/live : l'arrêt exige un redémarrage manuel.
        self.resume_next_day = resume_next_day
        self.state_dir = Path(state_dir)

        self.halted = False
        self.halt_reason = ""
        self._day: date | None = None
        self._day_start_equity: float | None = None

    # ---------------------------------------------------------------- entrées

    def stop_and_target(self, entry_price: float, atr_value: float) -> tuple[float, float]:
        """Stop-loss ATR et take-profit au ratio risque/rendement minimal."""
        stop = entry_price - self.atr_stop_mult * atr_value
        risk_per_unit = entry_price - stop
        target = entry_price + self.min_risk_reward * risk_per_unit
        return stop, target

    def position_size(self, equity: float, entry_price: float, stop_price: float) -> float:
        """Quantité telle que (entrée − stop) × qty = risque autorisé,
        plafonnée à max_position_pct du capital."""
        risk_per_unit = entry_price - stop_price
        if risk_per_unit <= 0 or entry_price <= 0 or equity <= 0:
            return 0.0
        risk_amount = equity * self.risk_per_trade_pct / 100
        qty = risk_amount / risk_per_unit
        max_qty = equity * self.max_position_pct / 100 / entry_price
        return min(qty, max_qty)

    def evaluate_entry(
        self,
        *,
        equity: float,
        cash: float,
        entry_price: float,
        atr_value: float,
        open_positions: int,
        confidence: float,
        min_confidence: float,
    ) -> OrderPlan:
        """Point de passage obligatoire avant toute ouverture de position."""
        if self.halted:
            return OrderPlan(False, f"trading arrêté : {self.halt_reason}")
        if self.kill_switch_active():
            return OrderPlan(False, "kill switch actif (fichier state/KILL)")
        if open_positions >= self.max_open_positions:
            return OrderPlan(False, f"limite de {self.max_open_positions} positions atteinte")
        if confidence < min_confidence:
            return OrderPlan(
                False, f"confiance {confidence:.2f} < seuil {min_confidence:.2f}"
            )
        if atr_value is None or math.isnan(atr_value) or atr_value <= 0:
            return OrderPlan(False, "ATR indisponible — impossible de placer un stop")

        stop, target = self.stop_and_target(entry_price, atr_value)
        if stop <= 0:
            return OrderPlan(False, "stop calculé ≤ 0 (ATR aberrant)")

        qty = self.position_size(equity, entry_price, stop)
        # ne jamais engager plus que le cash disponible (marge de 1% pour les frais)
        if qty * entry_price > cash:
            qty = cash * 0.99 / entry_price
        if qty <= 0 or qty * entry_price < 1e-9:
            return OrderPlan(False, "taille de position nulle (capital insuffisant)")

        risk_amount = (entry_price - stop) * qty
        return OrderPlan(
            approved=True,
            reason=(
                f"risque {risk_amount:.2f} ({self.risk_per_trade_pct:.1f}% max), "
                f"stop {stop:.4f}, cible {target:.4f} (RR 1:{self.min_risk_reward:.1f})"
            ),
            qty=qty,
            stop_loss=stop,
            take_profit=target,
            risk_amount=risk_amount,
        )

    # ------------------------------------------------- limite de perte du jour

    def check_daily_loss(self, now: datetime, equity: float) -> bool:
        """À appeler à chaque évaluation d'équité.

        Retourne True au moment précis où la limite journalière est franchie
        (une seule fois), pour déclencher l'alerte et la fermeture des positions.
        """
        today = now.date()
        if self._day != today:
            self._day = today
            self._day_start_equity = equity
            if self.halted and self.halt_reason.startswith("perte journalière") and self.resume_next_day:
                log.info("Nouvelle journée — reprise du trading après limite journalière")
                self.halted = False
                self.halt_reason = ""
            return False

        if self.halted or not self._day_start_equity:
            return False

        drawdown_pct = (self._day_start_equity - equity) / self._day_start_equity * 100
        if drawdown_pct >= self.daily_loss_limit_pct:
            self.halted = True
            self.halt_reason = (
                f"perte journalière {drawdown_pct:.2f}% ≥ limite {self.daily_loss_limit_pct:.2f}%"
            )
            log.critical("ARRÊT DU TRADING — %s", self.halt_reason)
            return True
        return False

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        log.critical("ARRÊT DU TRADING — %s", reason)

    # ------------------------------------------------------------ kill switch

    def kill_switch_active(self) -> bool:
        return (self.state_dir / KILL_FILE).exists()

    def activate_kill_switch(self, reason: str = "demande manuelle") -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / KILL_FILE).write_text(reason, encoding="utf-8")

    def clear_kill_switch(self) -> None:
        (self.state_dir / KILL_FILE).unlink(missing_ok=True)
