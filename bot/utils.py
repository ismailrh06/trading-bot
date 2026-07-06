"""Petites fonctions utilitaires partagées."""

from __future__ import annotations

from datetime import datetime, timezone

_TIMEFRAME_UNITS = {"m": 1, "h": 60, "d": 1440, "w": 10080}


def timeframe_to_minutes(timeframe: str) -> int:
    """'15m' → 15, '1h' → 60, '1d' → 1440."""
    unit = timeframe[-1]
    if unit not in _TIMEFRAME_UNITS:
        raise ValueError(f"Timeframe invalide : {timeframe!r}")
    return int(timeframe[:-1]) * _TIMEFRAME_UNITS[unit]


def periods_per_year(timeframe: str) -> float:
    """Nombre de bougies par an (marché crypto : 24/7, 365 jours)."""
    return 365 * 1440 / timeframe_to_minutes(timeframe)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_ms(date_str: str) -> int:
    """'2023-01-01' ou ISO complet → timestamp epoch en millisecondes UTC."""
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
