"""Dashboard Streamlit : positions ouvertes, P&L jour/mois, historique.

Lancement :  streamlit run bot/monitoring/dashboard.py
(ou :  python main.py dashboard)

Nécessite :  pip install streamlit
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB_PATH = Path("state/bot.db")

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    raise SystemExit(
        "Streamlit n'est pas installé. Installez-le avec : pip install streamlit"
    )


def read_table(query: str, params: tuple = ()) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=params)


st.set_page_config(page_title="Bot de trading", layout="wide")
st.title("📈 Bot de trading — supervision")

now = datetime.now(timezone.utc)
today = now.strftime("%Y-%m-%d")
month = now.strftime("%Y-%m")

trades = read_table("SELECT * FROM trades ORDER BY exit_time DESC")
positions = read_table("SELECT * FROM positions")
equity = read_table("SELECT * FROM equity ORDER BY ts")

pnl_day = trades[trades["exit_time"].str.startswith(today)]["pnl"].sum() if not trades.empty else 0.0
pnl_month = trades[trades["exit_time"].str.startswith(month)]["pnl"].sum() if not trades.empty else 0.0
last_equity = equity["equity"].iloc[-1] if not equity.empty else 0.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Équité actuelle", f"{last_equity:,.2f}")
col2.metric("P&L du jour", f"{pnl_day:+,.2f}")
col3.metric("P&L du mois", f"{pnl_month:+,.2f}")
col4.metric("Positions ouvertes", len(positions))

st.subheader("Positions ouvertes")
if positions.empty:
    st.info("Aucune position ouverte.")
else:
    st.dataframe(positions, use_container_width=True)

st.subheader("Courbe d'équité")
if equity.empty:
    st.info("Pas encore de données d'équité.")
else:
    equity["ts"] = pd.to_datetime(equity["ts"])
    st.line_chart(equity.set_index("ts")["equity"])

st.subheader("Historique des trades")
if trades.empty:
    st.info("Aucun trade enregistré.")
else:
    st.dataframe(trades.head(100), use_container_width=True)
    wins = (trades["pnl"] > 0).sum()
    st.caption(
        f"{len(trades)} trades — {wins} gagnants "
        f"({wins / len(trades) * 100:.1f}%) — P&L cumulé {trades['pnl'].sum():+,.2f}"
    )
