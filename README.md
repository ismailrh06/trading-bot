# Bot de trading algorithmique (crypto, ccxt)

Bot de trading modulaire en Python 3.11+ : backtest → paper trading → live,
avec gestion du risque non contournable, notifications et dashboard.

> ⚠️ **Avertissement** : aucun backtest ne garantit la rentabilité future.
> Les deux stratégies fournies sont des points de départ pédagogiques — sur
> BTC/USDT 2024 en 1h, elles sont **perdantes** (voir `reports/`). Validez
> toujours en backtest puis en paper pendant plusieurs semaines avant
> d'envisager le live, et ne risquez jamais d'argent dont vous avez besoin.

## Architecture

```
├── main.py                  # point d'entrée CLI
├── config.yaml              # toute la configuration (mode, exchange, risque…)
├── .env                     # clés API et confirmation live (jamais commité)
└── bot/
    ├── config.py            # chargement config + garde-fou du mode live
    ├── data.py              # téléchargement/cache OHLCV via ccxt
    ├── exchange.py          # client ccxt : reconnexion auto, backoff
    ├── indicators.py        # EMA, RSI, ATR (pandas pur)
    ├── strategies/          # Strategy (abstraite), ema_cross, breakout
    ├── risk/                # RiskManager : sizing, stops ATR, limites, kill switch
    ├── backtest/            # moteur, métriques, graphiques
    ├── execution/           # brokers paper/live + boucle temps réel
    └── monitoring/          # logs, Telegram/Discord, SQLite, dashboard
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate        # fish : source .venv/bin/activate.fish
pip install -r requirements.txt
cp .env.example .env             # puis remplir si besoin (inutile pour le backtest)
```

## 1. Backtest — TOUJOURS commencer ici

```bash
python main.py backtest                          # tous les symboles du config
python main.py backtest --symbol BTC/USDT --strategy breakout \
                        --start 2024-01-01 --end 2024-12-31
```

Sortie : rapport complet en console (rendement total et mensuel, drawdown max,
Sharpe, taux de réussite, profit factor, frais payés…) + graphiques PNG
(prix avec entrées/sorties, courbe de capital, drawdown) dans `reports/`.
Les données sont mises en cache dans `data/` (`python main.py download` pour
pré-télécharger).

Le backtest est volontairement pessimiste : exécution à l'open de la bougie
suivante (aucun lookahead), frais sur chaque ordre, slippage, stop prioritaire
sur le take-profit quand les deux sont touchés dans la même bougie, gaps
exécutés au prix d'ouverture. La limite de perte journalière s'y applique
aussi : le résultat reflète le comportement réel du bot.

## 2. Paper trading (mode par défaut)

```bash
python main.py run
```

Avec `mode: paper` (défaut), deux variantes :

- **Simulation interne** (`use_testnet: false`) : prix réels, ordres simulés
  en interne avec frais et slippage. Aucune clé API requise — fonctionne
  immédiatement.
- **Compte démo de l'exchange** (`use_testnet: true`) : les ordres sont
  réellement envoyés au testnet (argent fictif). Le bot emprunte exactement
  les mêmes chemins de code que le live — dernière étape avant le réel.
  Nécessite des clés créées sur le testnet (ex. <https://testnet.bybit.com>)
  dans `.env`.

## 3. Live — double confirmation obligatoire

Le bot refuse de démarrer en live tant que **les deux** conditions ne sont pas
réunies :

1. `mode: live` dans `config.yaml` (modification manuelle) ;
2. `CONFIRM_LIVE_TRADING=true` dans `.env`.

Clés API : créez-les avec les droits lecture + trade **sans droit de retrait**.

## Kill switch

```bash
python main.py kill          # ferme toutes les positions, arrête le bot (≤ poll_seconds)
python main.py kill --clear  # réarme après un kill
```

Le kill s'appuie sur le fichier `state/KILL` : si le bot ne tourne pas au
moment du kill, vérifiez vos positions directement sur l'exchange.

## Gestion du risque (non contournable)

Toute entrée passe par le `RiskManager` :

| Règle | Défaut | Config |
|---|---|---|
| Risque par trade | 1 % du capital (**plafond dur 2 %**) | `risk.risk_per_trade_pct` |
| Stop-loss | entrée − 2 × ATR(14), obligatoire | `risk.atr_stop_mult` |
| Take-profit | ≥ 2 × la distance du stop (**plancher dur 1:2**) | `risk.min_risk_reward` |
| Perte journalière max | 5 % → arrêt complet + alerte | `risk.daily_loss_limit_pct` |
| Positions simultanées | 3 | `risk.max_open_positions` |
| Taille max d'une position | 25 % du capital | `risk.max_position_pct` |

En paper/live, un arrêt sur perte journalière exige un redémarrage manuel.

## Changer d'exchange / de stratégie

- **Exchange** : `exchange.id` dans `config.yaml` (`bybit`, `kraken`, `okx`,
  `binance`… tout id ccxt). *Note : sur cette machine, `binance` est bloqué au
  niveau DNS (testé le 2026-07-03), d'où `bybit` par défaut.*
- **Stratégie** : `strategy.name` (`ema_cross` ou `breakout`), paramètres sous
  `strategy.params`. Pour en ajouter une : hériter de
  `bot/strategies/base.py::Strategy`, implémenter `generate_signals()` (colonnes
  `signal`/`confidence`/`reason`), puis l'enregistrer dans
  `bot/strategies/__init__.py`.

## Monitoring

- **Logs** : `logs/bot.log` (tout) et `logs/decisions.log` (chaque décision
  d'achat/vente/refus avec sa raison et son score de confiance).
- **Notifications** : activer `monitoring.telegram.enabled` ou
  `monitoring.discord.enabled` et renseigner les secrets dans `.env`
  (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, ou `DISCORD_WEBHOOK_URL`).
  Chaque trade, alerte de risque et erreur est notifié.
- **Dashboard** : `pip install streamlit` puis `python main.py dashboard` —
  positions ouvertes, P&L du jour/du mois, courbe d'équité, historique.
- **Persistance** : trades, positions et équité en SQLite (`state/bot.db`).
  Après un crash ou un redémarrage, les positions ouvertes sont rechargées
  et de nouveau surveillées.

## Tests

```bash
python -m pytest tests/ -v
```

31 tests couvrent la logique de risque (sizing, plafonds, limite journalière,
kill switch), les signaux des deux stratégies et la mécanique du backtest
(pas de lookahead, comptabilité exacte, frais, stops pessimistes).


## Lancer le bot en continu (arrière-plan)

```bash
# réarmer le kill switch s'il est actif, puis lancer en tâche de fond
ls state/KILL 2>/dev/null && .venv/bin/python main.py kill --clear
nohup .venv/bin/python main.py run > logs/run_stdout.log 2>&1 &
```

Le bot écrit lui-même son PID dans `state/bot.pid` au démarrage.

## Savoir si le bot tourne toujours

```bash
python main.py status
```

Affiche : processus vivant ou non, dernier signe de vie (snapshot d'équité
toutes les ~30 s), positions ouvertes, derniers trades, dernière décision.

En plus, un **battement de cœur** est envoyé sur Telegram/Discord toutes les
12 h (`monitoring.heartbeat_hours`, 0 pour désactiver) : « 💓 Bot vivant,
équité X, positions Y ». Si ce message n'arrive plus aux heures prévues,
le bot est tombé.

> ⚠️ **macOS** : si le Mac se met en veille, le processus est suspendu et le
> bot cesse de trader (positions non surveillées !). Pour une session longue,
> empêcher la veille : `caffeinate -is &` — ou héberger le bot sur une machine
> qui ne dort jamais (VPS, Raspberry Pi…).