"""Chargement et validation de la configuration.

Deux garde-fous vivent ici :
- le mode par défaut est "paper" ;
- le mode "live" est refusé tant que CONFIRM_LIVE_TRADING=true n'est pas
  présent dans l'environnement (.env), en plus de mode: live dans config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

VALID_MODES = {"backtest", "paper", "live"}
DEFAULT_MODE = "paper"


class ConfigError(Exception):
    """Configuration invalide — le bot refuse de démarrer."""


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    load_dotenv()
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {path}")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    mode = str(cfg.get("mode", DEFAULT_MODE)).lower()
    if mode not in VALID_MODES:
        raise ConfigError(f"Mode invalide '{mode}' — attendu : {sorted(VALID_MODES)}")
    cfg["mode"] = mode

    if mode == "live":
        assert_live_confirmed()

    if not cfg.get("symbols"):
        raise ConfigError("Aucun symbole configuré (clé 'symbols')")
    if not cfg.get("exchange", {}).get("id"):
        raise ConfigError("Exchange non configuré (clé 'exchange.id')")

    return cfg


def assert_live_confirmed() -> None:
    confirm = os.getenv("CONFIRM_LIVE_TRADING", "").strip().lower()
    if confirm != "true":
        raise ConfigError(
            "Mode LIVE refusé : CONFIRM_LIVE_TRADING=true est requis dans "
            "l'environnement (.env) EN PLUS de mode: live dans config.yaml. "
            "Le bot ne trade JAMAIS en réel sans cette double confirmation."
        )


def api_credentials() -> dict[str, str]:
    """Clés API lues depuis l'environnement (.env) — jamais depuis le code."""
    return {
        "apiKey": os.getenv("EXCHANGE_API_KEY", ""),
        "secret": os.getenv("EXCHANGE_API_SECRET", ""),
        "password": os.getenv("EXCHANGE_API_PASSWORD", ""),
    }
