"""
Точка входа бота.

Два режима:
  polling  — для локальной разработки (сервер и домен не нужны);
  webhook  — для прода (Telegram сам шлёт апдейты на наш API).

Запуск:  python -m app.bot.main
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from app.bot.handlers import router
from app.bot.middlewares import RateLimitMiddleware
from app.bot.notifications import create_bot
from app.config import get_settings
from app.db import close_connections, create_all_tables
from app.utils.logging_setup import setup_logging

log = logging.getLogger(__name__)
settings = get_settings()


def build_dispatcher() -> Dispatcher:
    """
    Диспетчер с хранилищем состояний.

    На проде состояния лежат в Redis: диалоги не теряются при перезапуске
    контейнера и работают при нескольких копиях бота. В локальном режиме
    Redis не нужен — состояния держим в памяти процесса.
    """
    if settings.local_mode:
        storage = MemoryStorage()
    else:
        storage = RedisStorage.from_url(settings.redis_url)
    dispatcher = Dispatcher(storage=storage)
    # outer_middleware — до FSM и фильтров: превышающий лимит апдейт не должен
    # тратить время хендлеров и не должен обходить лимит через другой router
    dispatcher.message.outer_middleware(RateLimitMiddleware())
    dispatcher.callback_query.outer_middleware(RateLimitMiddleware())
    dispatcher.include_router(router)
    return dispatcher


# Пункты синего меню «Меню» рядом с полем ввода.
# Так сделано у конкурентов (Atom, Quattro): человек видит список разделов
# и не ищет нужную кнопку в переписке.
BOT_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню"),
    BotCommand(command="account", description="👤 Личный кабинет"),
    BotCommand(command="buy", description="💳 Покупка и продление"),
    BotCommand(command="connect", description="⚡ Подключение и ключи"),
    BotCommand(command="ref", description="👥 Пригласить друзей"),
    BotCommand(command="help", description="📖 Помощь и инструкции"),
    BotCommand(command="support", description="💬 Поддержка"),
]


async def setup_bot_menu(bot: Bot) -> None:
    """
    Зарегистрировать команды и включить кнопку «Меню».

    Вызывается при каждом старте: список команд хранится на стороне Telegram,
    и после правки BOT_COMMANDS достаточно перезапустить бота.
    """
    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    log.info("Меню бота обновлено: %s команд", len(BOT_COMMANDS))


async def main() -> None:
    setup_logging("bot")

    if settings.local_mode:
        # Локальный запуск: создаём таблицы в файле SQLite, чтобы бот
        # стартовал одной командой, без Postgres и миграций
        await create_all_tables()
        log.info(
            "Режим хранения: SQLite %s, состояния бота в памяти (Postgres и Redis не используются)",
            settings.sqlite_path,
        )

    bot = create_bot()
    dispatcher = build_dispatcher()

    me = await bot.get_me()
    await setup_bot_menu(bot)
    log.info("Бот запущен: @%s (режим %s)", me.username, settings.bot_mode)

    # Частая причина «кнопка подключения не появилась»: адрес по умолчанию
    if not settings.public_base_url.startswith("https://"):
        log.warning(
            "PUBLIC_BASE_URL=%s — не https. Telegram не пропускает такие адреса "
            "в кнопки, поэтому ссылка-подписка будет отправляться текстом. "
            "Укажите свой домен в .env, чтобы появилась кнопка «Подключить в 1 клик».",
            settings.public_base_url,
        )

    try:
        if settings.bot_mode == "webhook":
            # В режиме webhook апдейты принимает FastAPI (app/main.py),
            # здесь только регистрируем адрес в Telegram.
            webhook_url = f"{settings.public_base_url.rstrip('/')}/webhook/telegram"
            await bot.set_webhook(
                webhook_url,
                secret_token=settings.telegram_webhook_secret,
                drop_pending_updates=True,
            )
            log.info("Webhook установлен: %s", webhook_url)
            # Процесс остаётся живым: он обслуживает воркер-задачи бота
            # Держим процесс живым до сигнала остановки
            await asyncio.Event().wait()
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await close_connections()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")
