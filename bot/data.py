"""Téléchargement et cache des données OHLCV via ccxt."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from bot.utils import timeframe_to_minutes, to_utc_ms

log = logging.getLogger(__name__)

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int, until_ms: int) -> pd.DataFrame:
    """Télécharge les bougies par pages de 1000 en respectant le rate limit."""
    step_ms = timeframe_to_minutes(timeframe) * 60_000
    all_rows: list[list] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:  # l'exchange ne progresse plus
            break
        cursor = last_ts + step_ms
        log.info("  %s : %d bougies (jusqu'à %s)", symbol, len(all_rows),
                 pd.Timestamp(last_ts, unit="ms", tz="UTC"))
        time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(all_rows, columns=COLUMNS).drop_duplicates("timestamp")
    df = df[(df["timestamp"] >= since_ms) & (df["timestamp"] <= until_ms)]
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").sort_index()


def load_data(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    cache_dir: str | Path = "data",
) -> pd.DataFrame:
    """Charge depuis le cache CSV local, sinon télécharge et met en cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "-").replace(":", "_")
    cache_file = cache_dir / f"{exchange_id}_{safe_symbol}_{timeframe}.csv"

    since_ms, until_ms = to_utc_ms(start), to_utc_ms(end)

    if cache_file.exists():
        cached = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        cached.index = pd.to_datetime(cached.index, utc=True)
        first, last = cached.index[0].value // 10**6, cached.index[-1].value // 10**6
        step_ms = timeframe_to_minutes(timeframe) * 60_000
        if first <= since_ms and last >= until_ms - step_ms:
            log.info("Cache local utilisé : %s", cache_file)
            mask = (cached.index >= pd.Timestamp(since_ms, unit="ms", tz="UTC")) & (
                cached.index <= pd.Timestamp(until_ms, unit="ms", tz="UTC")
            )
            return cached[mask]

    import ccxt

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    log.info("Téléchargement %s %s %s → %s…", symbol, timeframe, start, end)
    df = fetch_ohlcv(exchange, symbol, timeframe, since_ms, until_ms)
    if df.empty:
        raise RuntimeError(f"Aucune donnée reçue pour {symbol} ({exchange_id})")
    df.to_csv(cache_file)
    log.info("%d bougies mises en cache dans %s", len(df), cache_file)
    return df
