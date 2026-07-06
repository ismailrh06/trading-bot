"""Brokers : même interface pour le paper trading et le réel.

PaperBroker : données de marché réelles (endpoints publics), fills simulés
avec slippage et frais — aucun ordre n'atteint l'exchange.
LiveBroker  : ordres réels via ccxt. Construit uniquement après la double
confirmation du mode live (config + CONFIRM_LIVE_TRADING=true).
"""

from __future__ import annotations

import logging

from bot.exchange import ExchangeClient
from bot.models import Fill

log = logging.getLogger(__name__)


class PaperBroker:
    """Portefeuille fictif. Le cash est géré ici ; les positions par l'engine."""

    def __init__(self, exchange: ExchangeClient, *, initial_capital: float,
                 fee_pct: float, slippage_bps: float):
        self.exchange = exchange
        self.cash = float(initial_capital)
        self.fee_rate = float(fee_pct) / 100
        self.slippage = float(slippage_bps) / 10_000

    def last_price(self, symbol: str) -> float:
        return self.exchange.last_price(symbol)

    def market_buy(self, symbol: str, qty: float) -> Fill:
        price = self.last_price(symbol) * (1 + self.slippage)
        fee = qty * price * self.fee_rate
        cost = qty * price + fee
        if cost > self.cash:
            qty = self.cash / (price * (1 + self.fee_rate))
            fee = qty * price * self.fee_rate
            cost = qty * price + fee
        self.cash -= cost
        log.info("[PAPER] ACHAT %s qty=%.6f à %.4f (frais %.4f)", symbol, qty, price, fee)
        return Fill(price=price, qty=qty, fee=fee)

    def market_sell(self, symbol: str, qty: float) -> Fill:
        price = self.last_price(symbol) * (1 - self.slippage)
        fee = qty * price * self.fee_rate
        self.cash += qty * price - fee
        log.info("[PAPER] VENTE %s qty=%.6f à %.4f (frais %.4f)", symbol, qty, price, fee)
        return Fill(price=price, qty=qty, fee=fee)


class LiveBroker:
    """Ordres réels. Le cash est lu sur l'exchange à chaque évaluation."""

    def __init__(self, exchange: ExchangeClient, quote_currency: str = "USDT"):
        self.exchange = exchange
        self.quote_currency = quote_currency

    @property
    def cash(self) -> float:
        return self.exchange.free_balance(self.quote_currency)

    def last_price(self, symbol: str) -> float:
        return self.exchange.last_price(symbol)

    def market_buy(self, symbol: str, qty: float) -> Fill:
        fill = self.exchange.market_buy(symbol, qty)
        log.info("[LIVE] ACHAT %s qty=%.6f à %.4f", symbol, fill.qty, fill.price)
        return fill

    def market_sell(self, symbol: str, qty: float) -> Fill:
        fill = self.exchange.market_sell(symbol, qty)
        log.info("[LIVE] VENTE %s qty=%.6f à %.4f", symbol, fill.qty, fill.price)
        return fill
