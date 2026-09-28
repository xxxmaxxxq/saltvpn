"""
Заморозка подписки — фишка, которой нет у конкурентов.

Смысл: человек уезжает или временно не пользуется → вместо отмены
он замораживает. Дни не сгорают, отток превращается в паузу.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import text
from app.db import session_scope
from app.services import subscriptions as sub_service
from app.services import users as user_service

log = logging.getLogger(__name__)
router = Router(name="pause")


@router.callback_query(F.data == "pause:menu")
@answered_callback
async def pause_menu(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, callback.from_user.id)
        if user is None or user.subscription is None:
            await callback.message.answer(text("error_generic"))
            return

        ok, reason = sub_service.can_pause(user.subscription, 1)
        if not ok:
            await callback.message.answer(text("pause_unavailable", reason=reason))
            return

        days_left = sub_service.pause_days_left(user.subscription)

    await callback.message.answer(
        text("pause_choose", pause_left=days_left),
        reply_markup=kb.pause_days_keyboard(days_left),
    )


@router.callback_query(F.data.startswith("pause:set:"))
@answered_callback
async def do_pause(callback: CallbackQuery) -> None:
    days = int(callback.data.split(":")[2])

    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, callback.from_user.id)
        if user is None:
            return
        try:
            subscription = await sub_service.pause(session, user, days)
        except ValueError as err:
            await callback.message.answer(text("pause_unavailable", reason=str(err)))
            return
        until = subscription.paused_until

    await callback.message.answer(
        text("pause_done", until_date=until.strftime("%d.%m.%Y")),
        reply_markup=kb.account_keyboard(is_paused=True),
    )


@router.callback_query(F.data == "pause:resume")
@answered_callback
async def do_resume(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, callback.from_user.id)
        if user is None:
            return
        try:
            subscription = await sub_service.resume(session, user, early=True)
        except ValueError as err:
            await callback.message.answer(text("pause_unavailable", reason=str(err)))
            return
        expires = subscription.expires_at

    await callback.message.answer(
        text("pause_resumed", expires_date=expires.strftime("%d.%m.%Y")),
        reply_markup=kb.account_keyboard(is_paused=False),
    )
