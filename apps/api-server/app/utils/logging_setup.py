"""Единая настройка логов для всех трёх процессов (bot, api, worker)."""

from __future__ import annotations

import logging
import sys

from app.config import get_settings


def setup_logging(component: str) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{component}] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # Библиотеки шумят на INFO — приглушаем, чтобы в логах были видны наши события
    for noisy in ("httpx", "httpcore", "aiogram.event", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
