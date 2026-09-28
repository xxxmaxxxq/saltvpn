"""
Поддержка и кнопка «🆘 Не работает».

Кнопка — не тикет, а действие: сначала пробуем починить автоматически
(переключить протокол/SNI), и только если не помогло — зовём человека.
Это снимает большую часть обращений и возвращает пользователю доступ за секунды.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import text
from app.db import session_scope
from app.services import healing
from app.services import subscriptions as sub_service
from app.services import users as user_service

log = logging.getLogger(__name__)
router = Router(name="support")


@router.callback_query(F.data == "heal:start")
@answered_callback
async def heal_me(callback: CallbackQuery) -> None:
    """Персональное восстановление доступа."""
    status_msg = await callback.message.answer(text("healing_started"))

    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, callback.from_user.id)
        if user is None:
            await status_msg.edit_text(text("error_generic"))
            return
        result = await healing.heal_user(session, user)
        sub_url = sub_service.subscription_url(user)

    if result.get("ok"):
        log.info("Персональное лечение tg=%s: %s", callback.from_user.id, result.get("action"))
        await status_msg.edit_text(
            text("healing_done"), reply_markup=kb.healing_result_keyboard(sub_url)
        )
    else:
        await status_msg.edit_text(text("healing_failed"), reply_markup=kb.support_keyboard())


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    await message.answer(text("support_hint"), reply_markup=kb.support_keyboard())


@router.callback_query(F.data == "support:contact")
async def show_support(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await callback.message.answer(text("support_hint"), reply_markup=kb.support_keyboard())
