"""Persistance SQLite : trades, positions ouvertes, historique d'équité.

Les positions sont écrites à chaque changement — après un crash ou un
redémarrage, l'engine les recharge et continue de les surveiller.
Le dashboard lit cette même base.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from bot.models import Position, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TEXT NOT NULL,
    exit_price REAL NOT NULL,
    exit_time TEXT NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    fees REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TEXT NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    strategy TEXT NOT NULL,
    entry_fee REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT NOT NULL,
    equity REAL NOT NULL,
    mode TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path = "state/bot.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # --------------------------------------------------------------- trades

    def record_trade(self, trade: Trade, mode: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trades (symbol, qty, entry_price, entry_time, exit_price,"
                " exit_time, pnl, pnl_pct, fees, exit_reason, strategy, mode)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trade.symbol, trade.qty, trade.entry_price, trade.entry_time.isoformat(),
                    trade.exit_price, trade.exit_time.isoformat(), trade.pnl, trade.pnl_pct,
                    trade.fees, trade.exit_reason, trade.strategy, mode,
                ),
            )

    # ------------------------------------------------------------ positions

    def save_position(self, pos: Position) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?)",
                (
                    pos.symbol, pos.qty, pos.entry_price, pos.entry_time.isoformat(),
                    pos.stop_loss, pos.take_profit, pos.strategy, pos.entry_fee,
                ),
            )

    def delete_position(self, symbol: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

    def load_positions(self) -> dict[str, Position]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
        return {
            row[0]: Position(
                symbol=row[0], qty=row[1], entry_price=row[2],
                entry_time=datetime.fromisoformat(row[3]),
                stop_loss=row[4], take_profit=row[5], strategy=row[6], entry_fee=row[7],
            )
            for row in rows
        }

    # --------------------------------------------------------------- équité

    def snapshot_equity(self, ts: datetime, equity: float, mode: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO equity (ts, equity, mode) VALUES (?,?,?)",
                (ts.isoformat(), equity, mode),
            )
