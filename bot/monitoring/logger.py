"""Configuration des logs : console + fichiers avec rotation.

- logs/bot.log       : tout, niveau DEBUG (rotation 5 Mo × 5)
- logs/decisions.log : chaque décision de trading et sa raison
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(log_dir: str | Path = "logs", console_level: int = logging.INFO) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_all = RotatingFileHandler(
        log_dir / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_all.setLevel(logging.DEBUG)
    file_all.setFormatter(formatter)
    root.addHandler(file_all)

    decisions = logging.getLogger("decisions")
    file_decisions = RotatingFileHandler(
        log_dir / "decisions.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_decisions.setFormatter(formatter)
    decisions.addHandler(file_decisions)

    # bruit des libs externes hors de la console
    for noisy in ("urllib3", "ccxt"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
