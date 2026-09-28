"""
Воркер уведомлений и биллинга.

Раз в 5 минут:
  1. Ищет подписки, которым пора отправить триггер (за 3 дня / 1 день / 2 часа).
  2. За 2 часа до конца ТРИАЛА отправляет таймер-оффер с персональной скидкой —
     это самый конверсионный момент во всей воронке.
  3. Отключает истёкшие подписки.
  4. Размораживает подписки, у которых закончилась пауза.
  5. Через 7 дней после истечения отправляет winback-оффер.

Каждый триггер уходит ровно один раз: факт отправки пишется в notifications
с уникальным индексом (subscription_id, kind).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import calc_price, get_settings, get_tariffs
from app.db import session_scope
from app.models import (
    Notification,
    NotificationKind,
    Subscription,
    SubscriptionStatus,
    User,
    utcnow,
)
from app.services import billing
from app.services import subscriptions as sub_service

log = logging.getLogger(__name__)
settings = get_settings()


def _in_quiet_hours() -> bool:
    """Не беспокоим ночью — иначе люди отключают уведомления и уходят."""
    cfg = get_tariffs().get("notifications", {})
    quiet = cfg.get("quiet_hours")
    if not quiet or len(quiet) != 2:
        return False

    from zoneinfo import ZoneInfo

    local_hour = utcnow().astimezone(ZoneInfo(settings.tz)).hour
    start, end = quiet
    return local_hour >= start or local_hour < end


async def _already_sent(session: AsyncSession, subscription_id: int, kind: str) -> bool:
    result = await session.execute(
        select(Notification.id).where(
            Notification.subscription_id == subscription_id, Notification.kind == kind
        )
    )
    return result.first() is not None


async def _mark_sent(session: AsyncSession, subscription_id: int, kind: str) -> bool:
    """
    Записать факт отправки.

    При гонке двух воркеров сработает уникальный индекс — второй просто
    не отправит дубль.
    """
    session.add(Notification(subscription_id=subscription_id, kind=kind))
    try:
        await session.flush()
        return True
    except IntegrityError:
        await session.rollback()
        return False


# ── Триггеры окончания подписки ────────────────────────────────────────────


async def process_expiring(bot: Bot) -> int:
    """Напоминания за 3 дня / 1 день / 2 часа до окончания."""
    from app.bot.notifications import send_expiry_notice

    sent = 0
    now = utcnow()
    thresholds = [
        (72, NotificationKind.EXPIRE_3D, "expire_3d"),
        (24, NotificationKind.EXPIRE_1D, "expire_1d"),
        (2, NotificationKind.EXPIRE_2H, "expire_2h"),
    ]

    async with session_scope() as session:
        for hours, kind, text_key in thresholds:
            deadline = now + timedelta(hours=hours)
            result = await session.execute(
                select(Subscription, User)
                .join(User, User.id == Subscription.user_id)
                .where(
                    Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]),
                    Subscription.expires_at > now,
                    Subscription.expires_at <= deadline,
                )
            )

            for subscription, user in result.all():
                if await _already_sent(session, subscription.id, kind):
                    continue

                # Для триала за 2 часа работает отдельный, более сильный оффер
                if subscription.is_trial and hours == 2:
                    continue

                discount = 0
                promo_code = None
                if hours == 2:
                    cfg = get_tariffs().get("offers", {}).get("expire_urgent", {})
                    if cfg.get("enabled", True):
                        discount = int(cfg.get("discount_percent", 15))
                        promo = await billing.create_personal_offer(
                            session, user, discount, int(cfg.get("valid_hours", 24))
                        )
                        promo_code = promo.code

                _, next_bonus = sub_service.calc_streak_bonus(subscription)
                ok = await send_expiry_notice(
                    bot,
                    user.telegram_id,
                    text_key,
                    subscription.expires_at,
                    discount=discount,
                    promo_code=promo_code,
                    bonus=max(next_bonus, 1),
                )
                if ok and await _mark_sent(session, subscription.id, kind):
                    sent += 1

    return sent


async def process_trial_offers(bot: Bot) -> int:
    """
    Таймер-оффер за 2 часа до конца триала.

    Механика: персональная скидка с ограниченным сроком жизни. Человек уже
    попробовал сервис и понял, что он работает — это лучший момент для оффера.
    """
    from app.bot.notifications import send_trial_offer

    cfg = get_tariffs().get("offers", {}).get("trial_timer", {})
    if not cfg.get("enabled", True):
        return 0

    hours_before = int(cfg.get("hours_before_end", 2))
    discount = int(cfg.get("discount_percent", 40))
    valid_hours = int(cfg.get("valid_hours", 6))

    sent = 0
    now = utcnow()
    deadline = now + timedelta(hours=hours_before)

    async with session_scope() as session:
        result = await session.execute(
            select(Subscription, User)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.is_trial.is_(True),
                Subscription.status == SubscriptionStatus.TRIAL,
                Subscription.expires_at > now,
                Subscription.expires_at <= deadline,
            )
        )

        for subscription, user in result.all():
            if await _already_sent(session, subscription.id, NotificationKind.TRIAL_OFFER):
                continue

            promo = await billing.create_personal_offer(session, user, discount, valid_hours)
            tariff = subscription.tariff_code
            price_old = calc_price(tariff, 1, 0)
            price_new = calc_price(tariff, 1, discount)

            ok = await send_trial_offer(
                bot,
                user.telegram_id,
                discount=discount,
                price_old=price_old,
                price_new=price_new,
                valid_hours=valid_hours,
                promo_code=promo.code,
            )
            if ok and await _mark_sent(session, subscription.id, NotificationKind.TRIAL_OFFER):
                sent += 1
                log.info("Таймер-оффер отправлен tg=%s (−%s%%)", user.telegram_id, discount)

    return sent


async def process_expired(bot: Bot) -> int:
    """Отключить истёкшие подписки и сообщить об этом."""
    from app.bot.notifications import send_expiry_notice

    processed = 0
    now = utcnow()

    async with session_scope() as session:
        result = await session.execute(
            select(Subscription, User)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]),
                Subscription.expires_at <= now,
            )
        )

        for subscription, user in result.all():
            await sub_service.expire(session, user, subscription)
            if not await _already_sent(session, subscription.id, NotificationKind.EXPIRED):
                await send_expiry_notice(bot, user.telegram_id, "expired", subscription.expires_at)
                await _mark_sent(session, subscription.id, NotificationKind.EXPIRED)
            processed += 1

    return processed


async def process_winback(bot: Bot) -> int:
    """Вернуть ушедших: скидка через N дней после истечения."""
    from app.bot.notifications import send_expiry_notice

    cfg = get_tariffs().get("offers", {}).get("winback", {})
    if not cfg.get("enabled", True):
        return 0

    days_after = int(cfg.get("days_after_expire", 7))
    discount = int(cfg.get("discount_percent", 30))
    valid_hours = int(cfg.get("valid_hours", 48))

    sent = 0
    now = utcnow()
    window_start = now - timedelta(days=days_after, hours=6)
    window_end = now - timedelta(days=days_after)

    async with session_scope() as session:
        result = await session.execute(
            select(Subscription, User)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.expires_at.between(window_start, window_end),
            )
        )

        for subscription, user in result.all():
            if await _already_sent(session, subscription.id, NotificationKind.WINBACK):
                continue
            promo = await billing.create_personal_offer(session, user, discount, valid_hours)
            ok = await send_expiry_notice(
                bot,
                user.telegram_id,
                "winback",
                subscription.expires_at,
                discount=discount,
                promo_code=promo.code,
            )
            if ok and await _mark_sent(session, subscription.id, NotificationKind.WINBACK):
                sent += 1

    return sent


async def process_pause_expiry(bot: Bot) -> int:
    """Автоматически разморозить подписки, у которых закончилась пауза."""
    from app.bot.notifications import safe_send
    from app.config import text

    resumed = 0
    now = utcnow()

    async with session_scope() as session:
        result = await session.execute(
            select(Subscription, User)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == SubscriptionStatus.PAUSED,
                Subscription.paused_until <= now,
            )
        )

        for _subscription, user in result.all():
            try:
                subscription = await sub_service.resume(session, user, early=False)
            except ValueError:
                continue
            await safe_send(
                bot,
                user.telegram_id,
                text("pause_resumed", expires_date=subscription.expires_at.strftime("%d.%m.%Y")),
            )
            resumed += 1

    return resumed


async def run_once(bot: Bot) -> dict[str, int]:
    """Один полный проход воркера. Вызывается планировщиком каждые 5 минут."""
    if _in_quiet_hours():
        # Ночью не шлём напоминания, но истёкшие подписки отключаем —
        # это техническая операция, а не сообщение
        expired = await process_expired(bot)
        return {"expired": expired, "skipped_quiet_hours": 1}

    stats = {
        "trial_offers": await process_trial_offers(bot),
        "expiring": await process_expiring(bot),
        "expired": await process_expired(bot),
        "winback": await process_winback(bot),
        "resumed": await process_pause_expiry(bot),
    }
    if any(stats.values()):
        log.info("Уведомления: %s", stats)
    return stats
