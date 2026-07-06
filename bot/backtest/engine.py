"""Moteur de backtest.

Hypothèses d'exécution — volontairement pessimistes :
- un signal calculé à la clôture de la bougie i s'exécute à l'OPEN de la
  bougie i+1 (jamais au close de i : ce serait du lookahead) ;
- slippage appliqué contre nous sur chaque ordre au marché ;
- frais prélevés sur chaque côté (entrée et sortie) ;
- si le stop ET le take-profit sont touchés dans la même bougie, on
  suppose que le stop est touché en premier (hypothèse défavorable) ;
- gap sous le stop à l'ouverture → exécution au prix d'ouverture (pire) ;
- la limite de perte journalière du RiskManager s'applique aussi en
  backtest : les résultats reflètent le comportement réel du bot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from bot.indicators import atr
from bot.models import Position, Trade
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    symbol: str
    strategy_name: str
    initial_capital: float
    equity_curve: pd.Series
    trades: list[Trade]
    df: pd.DataFrame
    signals: pd.DataFrame
    stats: dict = field(default_factory=dict)


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        risk_manager: RiskManager,
        *,
        initial_capital: float = 10_000.0,
        fee_pct: float = 0.1,
        slippage_bps: float = 5.0,
        min_confidence: float = 0.5,
    ):
        self.strategy = strategy
        self.risk = risk_manager
        self.initial_capital = float(initial_capital)
        self.fee_rate = float(fee_pct) / 100
        self.slippage = float(slippage_bps) / 10_000
        self.min_confidence = float(min_confidence)

    # ------------------------------------------------------------------ run

    def run(self, df: pd.DataFrame, symbol: str) -> BacktestResult:
        if df.empty:
            raise ValueError("DataFrame OHLCV vide")

        signals = self.strategy.generate_signals(df)
        atr_series = atr(df, self.risk.atr_period)
        warmup = max(self.strategy.warmup, self.risk.atr_period + 1)

        cash = self.initial_capital
        position: Position | None = None
        trades: list[Trade] = []
        equity_points: list[float] = []

        pending_action = 0  # signal de la bougie précédente, exécuté à l'open
        pending_confidence = 0.0
        pending_reason = ""
        pending_atr = float("nan")

        index = df.index
        opens = df["open"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        sig_values = signals["signal"].to_numpy()
        sig_conf = signals["confidence"].to_numpy()
        atr_values = atr_series.to_numpy()

        for i in range(len(df)):
            ts = index[i]
            open_price = opens[i]

            # -- limite de perte journalière : nouvelle journée = nouveau point de départ
            equity_open = cash + (position.qty * open_price if position else 0.0)
            self.risk.check_daily_loss(ts, equity_open)

            # -- 1) exécuter le signal décidé à la clôture de la bougie précédente
            if pending_action == 1 and position is None and not self.risk.halted:
                fill_price = open_price * (1 + self.slippage)
                plan = self.risk.evaluate_entry(
                    equity=equity_open,
                    cash=cash,
                    entry_price=fill_price,
                    atr_value=pending_atr,
                    open_positions=0,
                    confidence=pending_confidence,
                    min_confidence=self.min_confidence,
                )
                if plan.approved:
                    fee = plan.qty * fill_price * self.fee_rate
                    cash -= plan.qty * fill_price + fee
                    position = Position(
                        symbol=symbol,
                        qty=plan.qty,
                        entry_price=fill_price,
                        entry_time=ts.to_pydatetime(),
                        stop_loss=plan.stop_loss,
                        take_profit=plan.take_profit,
                        strategy=self.strategy.name,
                        entry_fee=fee,
                    )
                    log.debug("%s ACHAT %s qty=%.6f à %.4f — %s | %s",
                              ts, symbol, plan.qty, fill_price, pending_reason, plan.reason)
                else:
                    log.debug("%s achat refusé par le risque : %s", ts, plan.reason)
            elif pending_action == -1 and position is not None:
                fill_price = open_price * (1 - self.slippage)
                cash, trade = self._close(position, fill_price, ts, "signal", cash)
                trades.append(trade)
                position = None
            pending_action = 0

            # -- 2) stop-loss / take-profit intrabar (stop vérifié en premier)
            if position is not None:
                exit_price, reason = None, None
                if open_price <= position.stop_loss:
                    exit_price, reason = open_price * (1 - self.slippage), "stop_loss"
                elif lows[i] <= position.stop_loss:
                    exit_price, reason = position.stop_loss * (1 - self.slippage), "stop_loss"
                elif highs[i] >= position.take_profit:
                    # ordre limite : exécuté au prix du TP, sans slippage
                    exit_price, reason = position.take_profit, "take_profit"
                if exit_price is not None:
                    cash, trade = self._close(position, exit_price, ts, reason, cash)
                    trades.append(trade)
                    position = None

            # -- 3) équité mark-to-market à la clôture
            equity = cash + (position.qty * closes[i] if position else 0.0)

            # -- 4) limite de perte journalière franchie → tout couper
            if self.risk.check_daily_loss(ts, equity) and position is not None:
                fill_price = closes[i] * (1 - self.slippage)
                cash, trade = self._close(position, fill_price, ts, "daily_loss_limit", cash)
                trades.append(trade)
                position = None
                equity = cash

            equity_points.append(equity)

            # -- 5) signal de cette bougie, pour exécution à l'open de la suivante
            if i >= warmup and sig_values[i] != 0:
                pending_action = int(sig_values[i])
                pending_confidence = float(sig_conf[i])
                pending_reason = str(signals["reason"].iloc[i])
                pending_atr = float(atr_values[i])

        # -- clôture forcée en fin de période (résultat honnête, pas de position fantôme)
        if position is not None:
            cash, trade = self._close(
                position, closes[-1] * (1 - self.slippage), index[-1], "end_of_backtest", cash
            )
            trades.append(trade)
            equity_points[-1] = cash

        equity_curve = pd.Series(equity_points, index=index, name="equity")
        return BacktestResult(
            symbol=symbol,
            strategy_name=self.strategy.name,
            initial_capital=self.initial_capital,
            equity_curve=equity_curve,
            trades=trades,
            df=df,
            signals=signals,
        )

    # ---------------------------------------------------------------- interne

    def _close(
        self, position: Position, price: float, ts: pd.Timestamp, reason: str, cash: float
    ) -> tuple[float, Trade]:
        exit_fee = position.qty * price * self.fee_rate
        cash += position.qty * price - exit_fee
        trade = Trade(
            symbol=position.symbol,
            qty=position.qty,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            exit_price=price,
            exit_time=ts.to_pydatetime(),
            fees=position.entry_fee + exit_fee,
            exit_reason=reason,
            strategy=position.strategy,
        )
        log.debug("%s VENTE %s qty=%.6f à %.4f (%s) pnl=%.2f",
                  ts, position.symbol, position.qty, price, reason, trade.pnl)
        return cash, trade
