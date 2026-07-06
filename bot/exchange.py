"""Client exchange ccxt avec reconnexion automatique.

Changer d'exchange = changer exchange.id dans config.yaml, rien d'autre.
Toutes les requêtes passent par _call(), qui réessaie avec backoff
exponentiel sur les erreurs réseau — jamais de crash silencieux.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import ccxt

from bot.config import api_credentials
from bot.models import Fill

log = logging.getLogger(__name__)

MAX_RETRIES = 8
BASE_BACKOFF_S = 2.0


class ExchangeError(Exception):
    """Erreur définitive après épuisement des tentatives."""


class ExchangeClient:
    def __init__(self, cfg: dict, mode: str):
        ex_cfg = cfg["exchange"]
        ex_id = ex_cfg["id"]
        if not hasattr(ccxt, ex_id):
            raise ValueError(f"Exchange inconnu de ccxt : {ex_id!r}")

        params: dict[str, Any] = {"enableRateLimit": True}
        needs_keys = mode == "live" or (mode == "paper" and ex_cfg.get("use_testnet"))
        if needs_keys:
            creds = api_credentials()
            if not creds["apiKey"] or not creds["secret"]:
                raise ExchangeError(
                    "Clés API manquantes dans .env (EXCHANGE_API_KEY / EXCHANGE_API_SECRET)"
                )
            params.update({k: v for k, v in creds.items() if v})

        self.client = getattr(ccxt, ex_id)(params)
        self.id = ex_id

        if mode == "paper" and ex_cfg.get("use_testnet"):
            self.client.set_sandbox_mode(True)
            log.info("Mode testnet/sandbox activé sur %s", ex_id)

    # ------------------------------------------------------------ résilience

    def _call(self, fn: Callable, *args, **kwargs):
        """Exécute un appel ccxt avec reconnexion automatique."""
        delay = BASE_BACKOFF_S
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except ccxt.RateLimitExceeded:
                log.warning("Rate limit %s — pause %.0fs (tentative %d/%d)",
                            self.id, delay, attempt, MAX_RETRIES)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as exc:
                log.warning("Erreur réseau %s : %s — nouvel essai dans %.0fs (%d/%d)",
                            self.id, exc, delay, attempt, MAX_RETRIES)
            except ccxt.ExchangeError as exc:
                # erreur métier (solde insuffisant, symbole invalide…) : ne pas réessayer
                raise ExchangeError(f"Erreur exchange {self.id} : {exc}") from exc
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise ExchangeError(
            f"{self.id} injoignable après {MAX_RETRIES} tentatives — intervention requise"
        )

    # ------------------------------------------------------------- lectures

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int = 400):
        import pandas as pd

        rows = self._call(self.client.fetch_ohlcv, symbol, timeframe, limit=limit)
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").sort_index()

    def last_price(self, symbol: str) -> float:
        ticker = self._call(self.client.fetch_ticker, symbol)
        return float(ticker["last"])

    def free_balance(self, currency: str) -> float:
        balance = self._call(self.client.fetch_balance)
        return float(balance.get("free", {}).get(currency, 0.0))

    # --------------------------------------------------------------- ordres

    def market_buy(self, symbol: str, qty: float) -> Fill:
        order = self._call(self.client.create_market_buy_order, symbol, qty)
        return self._to_fill(order, symbol, side="buy")

    def market_sell(self, symbol: str, qty: float) -> Fill:
        order = self._call(self.client.create_market_sell_order, symbol, qty)
        return self._to_fill(order, symbol, side="sell")

    def _to_fill(self, order: dict, symbol: str, side: str) -> Fill:
        """Confirme l'exécution réelle d'un ordre au marché avant de la retourner.

        Beaucoup d'exchanges (Bybit v5 compris) répondent à la création d'un
        ordre au marché sans encore connaître son exécution (champs filled/
        average à None) — l'appariement est asynchrone, même s'il ne prend
        souvent qu'une fraction de seconde. On ne doit JAMAIS supposer un
        remplissage complet : un ordre au marché peut être annulé faute de
        liquidité (statut "canceled", filled=0), auquel cas fabriquer un
        Fill fictif ferait croire au bot qu'il détient une position qui
        n'existe pas réellement sur l'exchange.
        """
        order = self._await_fill(order, symbol)
        filled = float(order.get("filled") or 0.0)
        if filled <= 0:
            raise ExchangeError(
                f"Ordre {order.get('id')} sur {symbol} non exécuté "
                f"(statut={order.get('status')!r}) — probablement aucune liquidité "
                "disponible pour un ordre au marché sur ce carnet."
            )
        price = order.get("average") or order.get("price")
        if not price:
            raise ExchangeError(
                f"Ordre {order.get('id')} sur {symbol} rempli (qty={filled}) mais "
                "prix d'exécution introuvable dans la réponse de l'exchange."
            )
        price = float(price)

        fee_cost, fee_currency = 0.0, None
        if order.get("fee") and order["fee"].get("cost"):
            fee_cost = float(order["fee"]["cost"])
            fee_currency = order["fee"].get("currency")
        elif order.get("fees"):
            fee_cost = sum(float(f.get("cost") or 0.0) for f in order["fees"])
            fee_currency = next((f.get("currency") for f in order["fees"] if f.get("currency")), None)

        # Bybit (et beaucoup d'exchanges) prélèvent parfois les frais d'un ACHAT
        # dans l'actif reçu (la base, ex. BTC), pas dans la devise de cotation :
        # le solde réellement détenu est alors < qty exécutée. Observé de façon
        # incohérente sur ce testnet (tantôt oui, tantôt non pour un même
        # symbole) — on ne peut donc pas se fier uniquement au champ "fee" de
        # la réponse. On l'utilise comme première estimation pour le P&L, puis
        # on la corrige avec le solde RÉEL de l'exchange, seule source fiable.
        base_currency, quote_currency = (symbol.split("/", 1) + [""])[:2]
        qty = filled
        fee_in_quote = fee_cost
        if fee_currency == base_currency and side == "buy":
            qty = filled - fee_cost
            fee_in_quote = fee_cost * price  # normalisé en devise de cotation pour le P&L
        elif fee_currency and fee_currency not in (base_currency, quote_currency):
            log.warning(
                "Frais dans une devise inattendue (%s) pour %s — traité comme %s",
                fee_currency, symbol, quote_currency or "devise de cotation",
            )

        if side == "buy" and base_currency:
            # Ne jamais enregistrer plus que ce que l'exchange peut réellement
            # revendre : sous-estimer une position est sans danger, la
            # surestimer fait échouer la clôture avec "Insufficient balance".
            try:
                real_balance = self.free_balance(base_currency)
            except Exception as exc:
                log.warning("Impossible de vérifier le solde réel de %s : %s", base_currency, exc)
            else:
                if real_balance < qty:
                    log.warning(
                        "Solde réel de %s (%.10f) < quantité calculée (%.10f) — "
                        "ajustement à la baisse pour rester cohérent avec l'exchange",
                        base_currency, real_balance, qty,
                    )
                    qty = real_balance

        return Fill(price=price, qty=qty, fee=fee_in_quote)

    def _await_fill(self, order: dict, symbol: str, attempts: int = 5, delay_s: float = 1.0) -> dict:
        """Interroge l'exchange jusqu'à obtenir un statut définitif de l'ordre."""
        terminal = {"closed", "canceled", "rejected", "expired"}
        if order.get("status") in terminal or (order.get("filled") or 0) > 0:
            return order
        order_id = order.get("id")
        if not order_id:
            return order
        for _ in range(attempts):
            time.sleep(delay_s)
            try:
                refreshed = self.client.fetch_order(order_id, symbol, {"acknowledged": True})
            except Exception as exc:
                log.warning("Impossible de vérifier l'ordre %s sur %s : %s", order_id, symbol, exc)
                break
            if refreshed.get("status") in terminal or (refreshed.get("filled") or 0) > 0:
                return refreshed
        log.warning("Statut de l'ordre %s toujours indéterminé après %d tentatives", order_id, attempts)
        return order
