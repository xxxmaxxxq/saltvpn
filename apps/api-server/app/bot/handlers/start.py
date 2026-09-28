"""
/start — точка входа. Здесь решается судьба конверсии.

Сценарий для новичка: приветствие -> одна кнопка «Попробовать бесплатно» ->
через 10 секунд рабочий ключ. Никаких вопросов, форм и выбора тарифа
до того, как человек увидел, что сервис работает.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import calc_price, text
from app.db import session_scope
from app.models import SubscriptionStatus
from app.services import referrals as ref_service
from app.services import subscriptions as sub_service
from app.services import users as user_service

log = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    tg_user = message.from_user
    if tg_user is None:
        return

    async with session_scope() as session:
        user, is_new = await user_service.get_or_create(
            session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            language_code=tg_user.language_code,
        )

        # Реферальная ссылка вида ?start=ref_a1b2c3
        if is_new and command.args and command.args.startswith("ref_"):
            await ref_service.attach_referrer(session, user, command.args[4:])

        name = user_service.display_name(tg_user.first_name, tg_user.username)

        # Новичку связь с подпиской не нужна — проверяем флаг до обращения к ней
        if is_new:
            await message.answer(text("start_new", name=name), reply_markup=kb.start_new_user())
            return

        subscription = user.subscription
        if subscription is None and not user.trial_used:
            await message.answer(text("start_new", name=name), reply_markup=kb.start_new_user())
            return

        # Заморозил сам и вернулся: напоминаем, что дни целы
        if subscription is not None and subscription.status == SubscriptionStatus.PAUSED:
            until = subscription.paused_until
            await message.answer(
                text("start_paused", name=name, until_date=until.strftime("%d.%m.%Y")),
                reply_markup=kb.start_returning_user(),
            )
            return

        # Подписка закончилась: снимаем страх «настраивать заново» и зовём обратно
        if subscription is None or not subscription.is_active:
            expires = (
                subscription.expires_at.strftime("%d.%m.%Y")
                if subscription
                else "некоторое время назад"
            )
            await message.answer(
                text("start_expired", name=name, expires_date=expires),
                reply_markup=kb.start_returning_user(),
            )
            return

        await message.answer(
            text("start_returning", name=name, status_line=_status_line(user)),
            reply_markup=kb.start_returning_user(),
        )


def _status_line(user) -> str:
    """Короткая строка о состоянии подписки для приветствия."""
    subscription = user.subscription
    if subscription is None:
        return "У вас пока нет активной подписки."
    if subscription.status == SubscriptionStatus.PAUSED:
        return "⏸ Подписка заморожена. Разморозить можно в кабинете."
    if subscription.is_active:
        suffix = " (бесплатный период)" if subscription.is_trial else ""
        return f"✅ Подписка активна: осталось <b>{subscription.days_left} дн.</b>{suffix}"
    return "❌ Подписка истекла. Продлите — всё заработает сразу."


@router.callback_query(F.data == "trial:activate")
@answered_callback
async def activate_trial(callback: CallbackQuery) -> None:
    """Выдача бесплатного периода по одной кнопке."""
    async with session_scope() as session:
        user, _ = await user_service.get_or_create(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )

        can_take, reason = await user_service.can_take_trial(user)
        if not can_take:
            min_price = min(calc_price(t, 1) for t in ("nl", "ru", "multi"))
            await callback.message.answer(
                text("trial_already_used", min_price=min_price),
                reply_markup=kb.start_returning_user(),
            )
            log.info("Триал отклонён tg=%s: %s", user.telegram_id, reason)
            return

        subscription = await user_service.activate_trial(session, user)
        sub_url = sub_service.subscription_url(user)

    await callback.message.answer(
        text("trial_activated", days=subscription.days_left),
        reply_markup=kb.connect_keyboard(sub_url),
    )
