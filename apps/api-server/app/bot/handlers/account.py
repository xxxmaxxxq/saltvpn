"""Личный кабинет в чате (для тех, кто не открывает Mini App)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import text
from app.db import session_scope
from app.models import SubscriptionStatus, User
from app.services import subscriptions as sub_service
from app.services import users as user_service

router = Router(name="account")

STATUS_TEXT = {
    SubscriptionStatus.TRIAL: "🎁 бесплатный период",
    SubscriptionStatus.ACTIVE: "✅ активна",
    SubscriptionStatus.PAUSED: "⏸ заморожена",
    SubscriptionStatus.EXPIRED: "❌ истекла",
}


def _tariff_title(code: str) -> str:
    from app.config import tariff_by_code

    tariff = tariff_by_code(code)
    return tariff["title"] if tariff else code


def render_account(user: User, devices_online: int = 0) -> str:
    subscription = user.subscription
    if subscription is None:
        return text("account_expired", expires_date="—")

    if not subscription.is_active and subscription.status != SubscriptionStatus.PAUSED:
        return text("account_expired", expires_date=subscription.expires_at.strftime("%d.%m.%Y"))

    streak_line = ""
    if subscription.streak_count > 0:
        from app.services.subscriptions import calc_streak_bonus

        _, next_bonus = calc_streak_bonus(subscription)
        streak_line = (
            text(
                "streak_line",
                streak=subscription.streak_count,
                bonus=max(next_bonus, 1),
            )
            + "\n"
        )

    pause_line = ""
    if not subscription.is_trial:
        pause_line = text("pause_line", pause_left=sub_service.pause_days_left(subscription)) + "\n"

    return text(
        "account_active",
        tariff_title=_tariff_title(subscription.tariff_code),
        status_text=STATUS_TEXT.get(subscription.status, subscription.status),
        days_left=subscription.days_left,
        expires_date=subscription.expires_at.strftime("%d.%m.%Y"),
        devices_used=devices_online,
        device_limit=user_service.effective_device_limit(user, subscription),
        traffic_gb=round(subscription.traffic_used_bytes / 1024**3, 1),
        streak_line=streak_line,
        pause_line=pause_line,
    )


@router.message(Command("account"))
async def cmd_account(message: Message) -> None:
    if message.from_user is None:
        return
    await _send_account(message.from_user.id, message)


@router.callback_query(F.data == "account:show")
@answered_callback
async def show_account(callback: CallbackQuery) -> None:
    await _send_account(callback.from_user.id, callback.message)


async def _send_account(telegram_id: int, message: Message) -> None:
    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, telegram_id)
        if user is None:
            await message.answer(text("error_generic"))
            return
        # Трафик подтягиваем из панели — в кабинете важны свежие цифры
        await sub_service.refresh_traffic(session, user)
        body = render_account(user)
        is_paused = (
            user.subscription is not None and user.subscription.status == SubscriptionStatus.PAUSED
        )
        has_sub = user.subscription is not None

    await message.answer(
        body, reply_markup=kb.account_keyboard(is_paused=is_paused, has_subscription=has_sub)
    )
