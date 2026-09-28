"""
Запуск фоновых воркеров.

Один процесс с планировщиком APScheduler:
  * notifier — каждые 5 минут (уведомления, биллинг, разморозка пауз);
  * health   — каждые HEALTH_CHECK_INTERVAL_SEC (Auto-Healing).

Запуск: python -m app.workers.main
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.notifications import create_bot
from app.config import get_settings
from app.db import close_connections
from app.utils.logging_setup import setup_logging
from app.workers import health, notifier

log = logging.getLogger(__name__)
settings = get_settings()


async def main() -> None:
    setup_logging("worker")
    bot = create_bot()
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def notifier_job() -> None:
        try:
            await notifier.run_once(bot)
        except Exception:
            # Воркер не должен умирать из-за одной неудачной итерации
            log.exception("Ошибка в воркере уведомлений")

    async def health_job() -> None:
        try:
            await health.run_once(bot)
        except Exception:
            log.exception("Ошибка в воркере Auto-Healing")

    scheduler.add_job(notifier_job, "interval", minutes=5, id="notifier", max_instances=1)
    scheduler.add_job(
        health_job,
        "interval",
        seconds=settings.health_check_interval_sec,
        id="health",
        max_instances=1,
    )
    scheduler.start()

    log.info(
        "Воркеры запущены: уведомления каждые 5 мин, проверка нод каждые %s c",
        settings.health_check_interval_sec,
    )

    try:
        # Держим процесс живым до сигнала остановки
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await close_connections()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Воркеры остановлены")
