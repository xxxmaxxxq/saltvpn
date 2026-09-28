"""
Middleware диспетчера.

Один слой на все апдейты — надёжнее, чем звать check_rate_limit в каждом
хендлере руками: забудешь один раз, и лимит перестанет действовать
незаметно для всех, кроме того, кто найдёт баг постфактум.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import text
from app.services.users import check_rate_limit

log = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """
    Ограничивает, сколько апдейтов в минуту принимаем от одного telegram_id.

    Считает и сообщения, и нажатия кнопок вместе — иначе быстрый клик по
    инлайн-клавиатуре в цикле обходил бы лимит, построенный только на
    Message.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_id = None
        if isinstance(event, Message) and event.from_user:
            telegram_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            telegram_id = event.from_user.id

        if telegram_id is None:
            return await handler(event, data)

        allowed = await check_rate_limit(telegram_id)
        if not allowed:
            log.warning("rate limit hit: telegram_id=%s", telegram_id)
            warning = text("rate_limited")
            if isinstance(event, CallbackQuery):
                await event.answer(warning, show_alert=False)
            elif isinstance(event, Message):
                await event.answer(warning)
            return None

        return await handler(event, data)
