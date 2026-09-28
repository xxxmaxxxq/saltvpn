"""
Общий guard для callback-хендлеров.

"await callback.answer(); если нет from_user или message — выйти" было
скопировано в каждый файл по отдельности (11 раз в 6 файлах). Порядок двух
строк в одной копии (start.py) оказался перепутан: guard стоял раньше
answer(), и когда message пуст, кнопка у пользователя крутится (спиннер)
до таймаута Telegram вместо мгновенного снятия. Один декоратор — один
порядок операций, копировать больше нечего.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable

from aiogram.types import CallbackQuery

CallbackHandler = Callable[[CallbackQuery], Awaitable[None]]


def answered_callback(handler: CallbackHandler) -> CallbackHandler:
    """
    Снять "часики" с кнопки сразу, затем отсечь апдейты без пользователя
    или без сообщения (бывает у инлайн-режима и у очень старых клиентов).
    """

    @functools.wraps(handler)
    async def wrapper(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.from_user is None or callback.message is None:
            return
        await handler(callback)

    return wrapper
