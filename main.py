"""Point d'entrée du bot de trading.

Commandes :
  python main.py backtest [--symbol BTC/USDT] [--strategy ema_cross]
                          [--start 2023-01-01] [--end 2024-12-31] [--no-plot]
  python main.py download           # pré-télécharge les données historiques
  python main.py run                # paper ou live selon config.yaml
  python main.py kill               # KILL SWITCH : tout couper, fermer les positions
  python main.py kill --clear       # réarmer après un kill
  python main.py dashboard          # dashboard Streamlit
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from bot.config import ConfigError, load_config
from bot.monitoring.logger import setup_logging

log = logging.getLogger("main")


def cmd_backtest(cfg: dict, args: argparse.Namespace) -> None:
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.metrics import compute_stats, format_report
    from bot.backtest.plots import plot_backtest
    from bot.data import load_data
    from bot.risk.manager import RiskManager
    from bot.strategies import create_strategy

    bt_cfg = cfg.get("backtest", {})
    strat_cfg = cfg.get("strategy", {})
    strategy_name = args.strategy or strat_cfg.get("name", "ema_cross")
    symbols = [args.symbol] if args.symbol else cfg["symbols"]
    start = args.start or bt_cfg.get("start", "2023-01-01")
    end = args.end or bt_cfg.get("end", datetime.now().strftime("%Y-%m-%d"))
    timeframe = cfg.get("timeframe", "1h")
    reports_dir = Path(bt_cfg.get("reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        strategy = create_strategy(
            strategy_name, strat_cfg.get("params", {}).get(strategy_name, {})
        )
        risk = RiskManager(
            cfg.get("risk", {}),
            resume_next_day=bool(bt_cfg.get("daily_loss_resume_next_day", True)),
            state_dir=cfg.get("monitoring", {}).get("state_dir", "state"),
        )
        engine = BacktestEngine(
            strategy,
            risk,
            initial_capital=float(bt_cfg.get("initial_capital", 10_000)),
            fee_pct=float(bt_cfg.get("fee_pct", 0.1)),
            slippage_bps=float(bt_cfg.get("slippage_bps", 5)),
            min_confidence=float(strat_cfg.get("min_confidence", 0.5)),
        )

        df = load_data(cfg["exchange"]["id"], symbol, timeframe, start, end)
        log.info("Backtest %s : %d bougies %s (%s → %s)", symbol, len(df), timeframe, start, end)

        result = engine.run(df, symbol)
        result.stats = compute_stats(
            result.equity_curve, result.trades, engine.initial_capital, timeframe
        )

        report = format_report(result.stats, symbol, strategy_name)
        print(report)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{symbol.replace('/', '-')}_{strategy_name}_{stamp}"
        (reports_dir / f"{base}.txt").write_text(report, encoding="utf-8")
        if not args.no_plot:
            png = plot_backtest(result, reports_dir / f"{base}.png")
            print(f"\nGraphiques : {png}")
        print(f"Rapport    : {reports_dir / f'{base}.txt'}\n")


def cmd_download(cfg: dict, args: argparse.Namespace) -> None:
    from bot.data import load_data

    bt_cfg = cfg.get("backtest", {})
    start = args.start or bt_cfg.get("start", "2023-01-01")
    end = args.end or bt_cfg.get("end", datetime.now().strftime("%Y-%m-%d"))
    for symbol in cfg["symbols"]:
        df = load_data(cfg["exchange"]["id"], symbol, cfg.get("timeframe", "1h"), start, end)
        print(f"{symbol} : {len(df)} bougies en cache ({df.index[0]} → {df.index[-1]})")


def cmd_run(cfg: dict, args: argparse.Namespace) -> None:
    from bot.config import assert_live_confirmed
    from bot.exchange import ExchangeClient
    from bot.execution.brokers import LiveBroker, PaperBroker
    from bot.execution.engine import TradingEngine
    from bot.monitoring.notifier import Notifier
    from bot.monitoring.store import Store
    from bot.risk.manager import RiskManager
    from bot.strategies import create_strategy

    mode = cfg["mode"]
    if mode == "backtest":
        cmd_backtest(cfg, args)
        return
    if mode == "live":
        assert_live_confirmed()  # re-vérifié ici : double sécurité
        print("⚠️  MODE LIVE — ordres réels sur l'exchange. Ctrl+C pour arrêter.")

    mon_cfg = cfg.get("monitoring", {})
    strat_cfg = cfg.get("strategy", {})
    strategy = create_strategy(
        strat_cfg.get("name", "ema_cross"),
        strat_cfg.get("params", {}).get(strat_cfg.get("name", "ema_cross"), {}),
    )
    risk = RiskManager(
        cfg.get("risk", {}),
        resume_next_day=False,  # en réel, un arrêt journalier exige un redémarrage manuel
        state_dir=mon_cfg.get("state_dir", "state"),
    )
    if risk.kill_switch_active():
        raise SystemExit(
            "Kill switch actif (state/KILL). Réarmez avec : python main.py kill --clear"
        )

    exchange = ExchangeClient(cfg, mode)
    quote = cfg["symbols"][0].split("/")[-1]
    if mode == "paper" and not cfg["exchange"].get("use_testnet"):
        # simulation interne : prix réels, fills simulés, aucune clé requise
        paper_cfg = cfg.get("paper", {})
        broker = PaperBroker(
            exchange,
            initial_capital=float(paper_cfg.get("initial_capital", 10_000)),
            fee_pct=float(paper_cfg.get("fee_pct", 0.1)),
            slippage_bps=float(paper_cfg.get("slippage_bps", 5)),
        )
    else:
        # paper + use_testnet : les ordres partent RÉELLEMENT sur le compte démo
        # (argent fictif) — mêmes chemins de code que le live, risque zéro
        broker = LiveBroker(exchange, quote_currency=quote)

    engine = TradingEngine(
        cfg,
        broker,
        strategy,
        risk,
        Notifier(mon_cfg),
        Store(mon_cfg.get("db_path", "state/bot.db")),
    )
    engine.run()


def cmd_kill(cfg: dict, args: argparse.Namespace) -> None:
    from bot.monitoring.notifier import Notifier
    from bot.risk.manager import RiskManager

    mon_cfg = cfg.get("monitoring", {})
    risk = RiskManager(cfg.get("risk", {}), state_dir=mon_cfg.get("state_dir", "state"))
    if args.clear:
        risk.clear_kill_switch()
        print("Kill switch réarmé — le bot peut redémarrer.")
        return
    risk.activate_kill_switch()
    Notifier(mon_cfg).alert(
        "Kill switch activé",
        "Le bot fermera toutes les positions et s'arrêtera à sa prochaine itération "
        f"(≤ {cfg.get('execution', {}).get('poll_seconds', 30)}s s'il tourne).",
    )
    print("KILL SWITCH activé (state/KILL).")
    print("Si le bot tourne, il ferme toutes les positions et s'arrête à la prochaine itération.")
    print("S'il ne tourne pas, vérifiez vos positions directement sur l'exchange.")


def _bot_process_running(state_dir: Path) -> tuple[bool, int | None]:
    import os

    pid_file = state_dir / "bot.pid"
    if not pid_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # signal 0 : teste l'existence sans rien envoyer
        return True, pid
    except (ValueError, OSError):
        return False, None


def _format_age(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _print_db_status(db_path: Path, running: bool) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc)

    # dernier signe de vie : un snapshot d'équité toutes les ~30 s quand ça tourne
    row = conn.execute("SELECT ts, equity FROM equity ORDER BY ts DESC LIMIT 1").fetchone()
    if row:
        age_s = (now - datetime.fromisoformat(row[0])).total_seconds()
        print(f"Dernier signe de vie : il y a {_format_age(age_s)} — équité {row[1]:,.2f}")
        if running and age_s > 300:
            print("⚠️  Processus vivant mais plus de snapshot depuis 5 min — "
                  "probablement bloqué, vérifier logs/bot.log")

    positions = conn.execute(
        "SELECT symbol, qty, entry_price, stop_loss, take_profit FROM positions"
    ).fetchall()
    if positions:
        print(f"Positions ouvertes ({len(positions)}) :")
        for sym, qty, entry, sl, tp in positions:
            print(f"  {sym}: qty={qty:.6f} entrée={entry:.2f} stop={sl:.2f} cible={tp:.2f}")
    else:
        print("Positions ouvertes : aucune")

    trades = conn.execute(
        "SELECT exit_time, symbol, pnl, exit_reason FROM trades ORDER BY exit_time DESC LIMIT 3"
    ).fetchall()
    if trades:
        print("Derniers trades :")
        for ts, sym, pnl, reason in trades:
            print(f"  {ts[:16]}  {sym}  {pnl:+.2f}  ({reason})")


def cmd_status(cfg: dict, args: argparse.Namespace) -> None:
    mon_cfg = cfg.get("monitoring", {})
    state_dir = Path(mon_cfg.get("state_dir", "state"))
    db_path = Path(mon_cfg.get("db_path", "state/bot.db"))

    running, pid = _bot_process_running(state_dir)
    print(f"✅ BOT EN MARCHE (PID {pid})" if running else "❌ BOT ARRÊTÉ (aucun processus actif)")

    if (state_dir / "KILL").exists():
        print("🔴 Kill switch ACTIF — réarmer avec : python main.py kill --clear")

    if not db_path.exists():
        print("(aucune base de données encore — le bot n'a jamais tourné ?)")
        return
    _print_db_status(db_path, running)

    decisions_log = Path(mon_cfg.get("log_dir", "logs")) / "decisions.log"
    if decisions_log.exists():
        lines = decisions_log.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            print(f"Dernière décision : {lines[-1].split('| decisions |')[-1].strip()}")


def cmd_dashboard(cfg: dict, args: argparse.Namespace) -> None:
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "bot/monitoring/dashboard.py"],
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot de trading algorithmique")
    parser.add_argument("--config", default="config.yaml", help="chemin du config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bt = sub.add_parser("backtest", help="backtest sur données historiques")
    p_bt.add_argument("--symbol")
    p_bt.add_argument("--strategy", choices=["ema_cross", "breakout"])
    p_bt.add_argument("--start")
    p_bt.add_argument("--end")
    p_bt.add_argument("--no-plot", action="store_true")

    p_dl = sub.add_parser("download", help="pré-télécharger les données historiques")
    p_dl.add_argument("--start")
    p_dl.add_argument("--end")

    sub.add_parser("run", help="lancer le bot (mode selon config.yaml)")

    sub.add_parser("status", help="le bot tourne-t-il ? positions, derniers trades")

    p_kill = sub.add_parser("kill", help="kill switch : tout couper")
    p_kill.add_argument("--clear", action="store_true", help="réarmer le kill switch")

    sub.add_parser("dashboard", help="dashboard Streamlit")

    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"ERREUR DE CONFIGURATION : {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(cfg.get("monitoring", {}).get("log_dir", "logs"))

    commands = {
        "backtest": cmd_backtest,
        "download": cmd_download,
        "run": cmd_run,
        "status": cmd_status,
        "kill": cmd_kill,
        "dashboard": cmd_dashboard,
    }
    commands[args.command](cfg, args)


if __name__ == "__main__":
    main()
