"""Graphiques du backtest : prix + trades, courbe de capital, drawdown.

Palette et règles : marques fines, grille discrète, une seule échelle par
axe, entrées/sorties en marqueurs directionnels avec légende.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # rendu fichier, pas de fenêtre
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from bot.backtest.engine import BacktestResult

INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
PRICE = "#2a78d6"
EQUITY = "#2a78d6"
DRAWDOWN = "#e34948"
ENTRY = "#008300"
EXIT = "#d03b3b"


def plot_backtest(result: BacktestResult, out_path: str | Path) -> Path:
    df, equity = result.df, result.equity_curve
    drawdown = result.stats["drawdown_series"] * 100

    fig, (ax_price, ax_equity, ax_dd) = plt.subplots(
        3, 1, figsize=(12, 9), sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 1], "hspace": 0.12},
    )
    fig.patch.set_facecolor(SURFACE)

    # -- 1) prix + points d'entrée/sortie
    ax_price.plot(df.index, df["close"], color=PRICE, linewidth=1.4, label="Clôture")
    entries_x = [t.entry_time for t in result.trades]
    entries_y = [t.entry_price for t in result.trades]
    exits_x = [t.exit_time for t in result.trades]
    exits_y = [t.exit_price for t in result.trades]
    ax_price.scatter(entries_x, entries_y, marker="^", s=48, color=ENTRY,
                     zorder=3, label="Achat", edgecolors=SURFACE, linewidths=0.8)
    ax_price.scatter(exits_x, exits_y, marker="v", s=48, color=EXIT,
                     zorder=3, label="Vente", edgecolors=SURFACE, linewidths=0.8)
    ax_price.set_ylabel("Prix", color=MUTED)
    ax_price.legend(loc="upper left", frameon=False, labelcolor=INK)
    ax_price.set_title(
        f"{result.symbol} — {result.strategy_name} — "
        f"{result.stats['n_trades']} trades, rendement {result.stats['total_return_pct']:+.1f} %",
        color=INK, loc="left", fontsize=12,
    )

    # -- 2) courbe de capital
    ax_equity.plot(equity.index, equity.values, color=EQUITY, linewidth=1.6)
    ax_equity.axhline(result.initial_capital, color=BASELINE, linewidth=1, linestyle="--")
    ax_equity.annotate(
        f"{equity.iloc[-1]:,.0f}", xy=(equity.index[-1], equity.iloc[-1]),
        xytext=(6, 0), textcoords="offset points", color=INK, fontsize=9, va="center",
    )
    ax_equity.set_ylabel("Capital", color=MUTED)

    # -- 3) drawdown
    ax_dd.fill_between(drawdown.index, drawdown.values, 0, color=DRAWDOWN, alpha=0.35, linewidth=0)
    ax_dd.plot(drawdown.index, drawdown.values, color=DRAWDOWN, linewidth=1.0)
    ax_dd.set_ylabel("Drawdown (%)", color=MUTED)

    for ax in (ax_price, ax_equity, ax_dd):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.6)
        ax.tick_params(colors=MUTED, labelsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(BASELINE)
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out_path
