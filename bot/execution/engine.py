"""Boucle de trading temps réel (paper et live).

Garanties :
- chaque décision est journalisée avec sa raison (logger "decisions") ;
- toute exception inattendue est loguée ET notifiée — jamais de crash
  silencieux avec une position ouverte ;
- les positions sont persistées en SQLite : un redémarrage les recharge ;
- le kill switch (fichier state/KILL) est vérifié à chaque itération ;
- la limite de perte journalière arrête tout trading et alerte.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pandas as pd

from bot.exchange import ExchangeError
from bot.indicators import atr
from bot.models import Action, Position, Trade
from bot.monitoring.notifier import Notifier
from bot.monitoring.store import Store
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy
from bot.utils import now_utc, timeframe_to_minutes

log = logging.getLogger(__name__)
decisions = logging.getLogger("decisions")


class TradingEngine:
    def __init__(
        self,
        cfg: dict,
        broker,
        strategy: Strategy,
        risk: RiskManager,
        notifier: Notifier,
        store: Store,
    ):
        self.cfg = cfg
        self.mode = cfg["mode"]
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.notifier = notifier
        self.store = store

        self.symbols: list[str] = cfg["symbols"]
        self.timeframe: str = cfg.get("timeframe", "1h")
        self.min_confidence = float(cfg.get("strategy", {}).get("min_confidence", 0.5))
        exec_cfg = cfg.get("execution", {})
        self.poll_seconds = int(exec_cfg.get("poll_seconds", 30))
        self.candles_history = max(
            int(exec_cfg.get("candles_history", 400)), strategy.warmup + 20
        )

        mon_cfg = cfg.get("monitoring", {})
        self.heartbeat_hours = float(mon_cfg.get("heartbeat_hours", 12))
        self._last_heartbeat = now_utc()
        self._state_dir = Path(mon_cfg.get("state_dir", "state"))

        self.positions: dict[str, Position] = {}
        self._last_candle_ts: dict[str, pd.Timestamp] = {}
        self._running = False

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        self._running = True
        self._write_pid()
        self.positions = self.store.load_positions()
        if self.positions:
            log.info("Positions restaurées après redémarrage : %s", list(self.positions))

        self.notifier.send(
            f"🤖 Bot démarré — mode {self.mode.upper()}, stratégie {self.strategy.name}, "
            f"{', '.join(self.symbols)} en {self.timeframe}"
        )
        log.info("Boucle de trading démarrée (poll %ds)", self.poll_seconds)

        consecutive_errors = 0
        while self._running:
            try:
                self._tick()
                consecutive_errors = 0
            except KeyboardInterrupt:
                self._shutdown("arrêt manuel (Ctrl+C)")
                return
            except SystemExit:
                raise
            except ExchangeError as exc:
                consecutive_errors += 1
                log.exception("Exchange injoignable : %s", exc)
                if self.positions:
                    self.notifier.alert(
                        "Connexion exchange perdue",
                        f"{exc}\nPositions ouvertes : {list(self.positions)} — "
                        "le bot continue d'essayer de se reconnecter.",
                    )
            except Exception:
                consecutive_errors += 1
                log.exception("Erreur inattendue dans la boucle de trading")
                self.notifier.alert(
                    "Erreur inattendue",
                    f"Voir logs. Positions ouvertes : {list(self.positions) or 'aucune'}. "
                    "Le bot continue.",
                )
            # backoff progressif si les erreurs s'enchaînent, sans jamais s'arrêter
            time.sleep(min(self.poll_seconds * (1 + consecutive_errors), 300))

    def _shutdown(self, reason: str) -> None:
        self._running = False
        log.info("Arrêt : %s — positions ouvertes : %s", reason, list(self.positions) or "aucune")
        self.notifier.send(
            f"🛑 Bot arrêté ({reason}). Positions encore ouvertes : "
            f"{', '.join(self.positions) or 'aucune'}"
        )

    def _write_pid(self) -> None:
        """PID écrit par le bot lui-même — utilisé par `python main.py status`."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            (self._state_dir / "bot.pid").write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            log.warning("Impossible d'écrire state/bot.pid : %s", exc)

    def _heartbeat(self, equity: float) -> None:
        """Battement de cœur périodique : prouve que le bot est vivant.

        Si l'utilisateur ne reçoit plus ce message aux heures prévues,
        c'est que le bot est tombé (crash machine, veille, kill -9…).
        """
        if self.heartbeat_hours <= 0:
            return
        now = now_utc()
        if (now - self._last_heartbeat).total_seconds() < self.heartbeat_hours * 3600:
            return
        self._last_heartbeat = now
        if self.positions:
            pos_txt = ", ".join(
                f"{sym} (entrée {pos.entry_price:.2f})" for sym, pos in self.positions.items()
            )
        else:
            pos_txt = "aucune"
        halted_txt = f" — ⚠️ TRADING ARRÊTÉ : {self.risk.halt_reason}" if self.risk.halted else ""
        self.notifier.send(
            f"💓 Bot vivant — équité {equity:,.2f}, positions : {pos_txt}{halted_txt}"
        )

    # ------------------------------------------------------------------ tick

    def _tick(self) -> None:
        # 1) kill switch : tout couper, fermer les positions, sortir
        if self.risk.kill_switch_active():
            log.critical("KILL SWITCH détecté — fermeture de toutes les positions")
            self._close_all("kill_switch")
            self.notifier.alert("Kill switch", "Toutes les positions ont été fermées. Bot arrêté.")
            self._running = False
            raise SystemExit(0)

        prices = {s: self.broker.last_price(s) for s in self.symbols}
        equity = self._equity(prices)
        self.store.snapshot_equity(now_utc(), equity, self.mode)
        self._heartbeat(equity)

        # 2) limite de perte journalière
        if self.risk.check_daily_loss(now_utc(), equity):
            self.notifier.alert(
                "Limite de perte journalière atteinte",
                f"{self.risk.halt_reason}. Trading arrêté"
                + (" — positions fermées." if self.risk.close_positions_on_halt else "."),
            )
            if self.risk.close_positions_on_halt:
                self._close_all("daily_loss_limit")

        # 3) stops et take-profits des positions ouvertes
        for symbol, pos in list(self.positions.items()):
            price = prices[symbol]
            if price <= pos.stop_loss:
                self._close(symbol, "stop_loss")
            elif price >= pos.take_profit:
                self._close(symbol, "take_profit")

        # 4) signaux sur les nouvelles bougies clôturées
        if self.risk.halted:
            return
        for symbol in self.symbols:
            self._process_symbol(symbol, equity)

    def _process_symbol(self, symbol: str, equity: float) -> None:
        df = self.broker.exchange.fetch_ohlcv_df(symbol, self.timeframe, self.candles_history)
        if len(df) < 2:
            return
        df = df.iloc[:-1]  # la dernière bougie est en cours : on l'ignore

        last_ts = df.index[-1]
        if self._last_candle_ts.get(symbol) == last_ts:
            return  # pas de nouvelle bougie clôturée depuis le dernier tick
        self._last_candle_ts[symbol] = last_ts

        signal = self.strategy.latest_signal(df)
        decisions.info(
            "%s | %s | %s | confiance=%.2f | prix=%.4f | %s",
            symbol, self.strategy.name, signal.action.value,
            signal.confidence, signal.price, signal.reason,
        )

        if signal.action is Action.BUY and symbol not in self.positions:
            atr_value = float(atr(df, self.risk.atr_period).iloc[-1])
            plan = self.risk.evaluate_entry(
                equity=equity,
                cash=self.broker.cash,
                entry_price=signal.price,
                atr_value=atr_value,
                open_positions=len(self.positions),
                confidence=signal.confidence,
                min_confidence=self.min_confidence,
            )
            if not plan.approved:
                decisions.info("%s | achat refusé par le risque : %s", symbol, plan.reason)
                return
            fill = self.broker.market_buy(symbol, plan.qty)
            pos = Position(
                symbol=symbol,
                qty=fill.qty,
                entry_price=fill.price,
                entry_time=now_utc(),
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
                strategy=self.strategy.name,
                entry_fee=fill.fee,
            )
            self.positions[symbol] = pos
            self.store.save_position(pos)
            self.notifier.send(
                f"🟢 ACHAT {symbol} qty={fill.qty:.6f} à {fill.price:.4f}\n"
                f"stop {plan.stop_loss:.4f} | cible {plan.take_profit:.4f}\n"
                f"{signal.reason} (confiance {signal.confidence:.2f})"
            )
        elif signal.action is Action.SELL and symbol in self.positions:
            self._close(symbol, "signal")

    # ------------------------------------------------------------- clôtures

    def _close(self, symbol: str, reason: str) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        # vendre d'abord, retirer du suivi ensuite : si l'ordre échoue,
        # la position reste surveillée au lieu de devenir orpheline
        fill = self.broker.market_sell(symbol, pos.qty)
        self.positions.pop(symbol, None)
        trade = Trade(
            symbol=symbol,
            qty=pos.qty,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=fill.price,
            exit_time=now_utc(),
            fees=pos.entry_fee + fill.fee,
            exit_reason=reason,
            strategy=pos.strategy,
        )
        self.store.delete_position(symbol)
        self.store.record_trade(trade, self.mode)
        decisions.info("%s | VENTE (%s) pnl=%.2f (%.2f%%)", symbol, reason, trade.pnl, trade.pnl_pct)
        emoji = "✅" if trade.is_win else "🔻"
        self.notifier.send(
            f"{emoji} VENTE {symbol} à {fill.price:.4f} ({reason})\n"
            f"P&L : {trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)"
        )

    def _close_all(self, reason: str) -> None:
        for symbol in list(self.positions):
            try:
                self._close(symbol, reason)
            except Exception:
                log.exception("Échec de fermeture de %s — À FERMER MANUELLEMENT", symbol)
                self.notifier.alert(
                    "Échec de fermeture de position",
                    f"{symbol} n'a pas pu être fermée ({reason}). Fermez-la manuellement.",
                )

    def _equity(self, prices: dict[str, float]) -> float:
        return self.broker.cash + sum(
            pos.qty * prices[sym] for sym, pos in self.positions.items()
        )
