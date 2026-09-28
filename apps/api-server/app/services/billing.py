"""
Биллинг: создание счетов, обработка успешных оплат, промокоды и офферы.

Самое важное здесь — идемпотентность. Платёжные провайдеры повторяют вебхуки
при сетевых сбоях, и без защиты пользователь получил бы дни дважды.
Защита двухслойная:
  1. UNIQUE(provider, external_id) в БД;
  2. проверка статуса платежа перед начислением.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import calc_price, get_tariffs, tariff_by_code
from app.models import Payment, PaymentStatus, PromoCode, User, utcnow
from app.services import referrals as ref_service
from app.services import subscriptions as sub_service
from app.utils.security import generate_referral_code

log = logging.getLogger(__name__)


class BillingError(RuntimeError):
    pass


# ── Промокоды и персональные офферы ────────────────────────────────────────


async def get_promo(session: AsyncSession, code: str, user: User) -> PromoCode | None:
    """Найти действующий промокод (в т.ч. персональный для этого пользователя)."""
    if not code:
        return None
    result = await session.execute(select(PromoCode).where(PromoCode.code == code.strip().upper()))
    promo = result.scalar_one_or_none()
    if not promo or not promo.is_usable:
        return None
    if promo.is_personal_for_user_id and promo.is_personal_for_user_id != user.id:
        return None
    return promo


async def create_personal_offer(
    session: AsyncSession,
    user: User,
    discount_percent: int,
    valid_hours: int,
    bonus_days: int = 0,
) -> PromoCode:
    """
    Создать одноразовый персональный промокод.

    Используется таймер-оффером в конце триала и winback-кампанией:
    скидка привязана к человеку и сгорает по времени — это и создаёт
    ощущение «предложение не повторится».
    """
    promo = PromoCode(
        code=f"P{generate_referral_code().upper()}",
        discount_percent=discount_percent,
        bonus_days=bonus_days,
        max_uses=1,
        valid_until=utcnow() + timedelta(hours=valid_hours),
        is_personal_for_user_id=user.id,
    )
    session.add(promo)
    await session.flush()
    return promo


async def active_personal_offer(session: AsyncSession, user: User) -> PromoCode | None:
    """Действующий персональный оффер пользователя (для показа в кабинете)."""
    result = await session.execute(
        select(PromoCode)
        .where(
            PromoCode.is_personal_for_user_id == user.id,
            PromoCode.is_active.is_(True),
            PromoCode.used_count == 0,
        )
        .order_by(PromoCode.created_at.desc())
    )
    promo = result.scalars().first()
    return promo if promo and promo.is_usable else None


# ── Создание заказа ────────────────────────────────────────────────────────


async def create_order(
    session: AsyncSession,
    user: User,
    tariff_code: str,
    months: int,
    provider: str,
    promo_code: str | None = None,
) -> tuple[Payment, int]:
    """
    Создать платёж в статусе pending и посчитать сумму.

    Возвращает (платёж, сумма_в_рублях). Ссылку на оплату создаёт уже
    конкретный провайдер — биллинг про них ничего не знает.
    """
    if not tariff_by_code(tariff_code):
        raise BillingError(f"Неизвестный тариф: {tariff_code}")

    promo = await get_promo(session, promo_code, user) if promo_code else None
    discount = promo.discount_percent if promo else 0
    amount = calc_price(tariff_code, months, discount)

    payment = Payment(
        user_id=user.id,
        provider=provider,
        amount=Decimal(amount),
        currency="RUB",
        status=PaymentStatus.PENDING,
        tariff_code=tariff_code,
        period_months=months,
        promo_code=promo.code if promo else None,
    )
    session.add(payment)
    await session.flush()

    log.info(
        "Заказ #%s: tg=%s %s на %s мес = %s ₽ (скидка %s%%)",
        payment.id,
        user.telegram_id,
        tariff_code,
        months,
        amount,
        discount,
    )
    return payment, amount


# ── Обработка успешной оплаты ──────────────────────────────────────────────


async def apply_payment(
    session: AsyncSession,
    payment: Payment,
    external_id: str,
    raw_payload: dict | None = None,
) -> dict:
    """
    Начислить дни по успешной оплате.

    Идемпотентно: повторный вызов для уже оплаченного платежа ничего
    не начислит и вернёт already_processed=True.

    Возвращает словарь с результатом — бот по нему формирует сообщение.
    """
    if payment.status == PaymentStatus.PAID:
        log.info("Повторный вебхук по платежу #%s — начисление пропущено", payment.id)
        return {"already_processed": True, "payment_id": payment.id}

    user = await session.get(User, payment.user_id)
    if user is None:
        raise BillingError(f"Пользователь платежа #{payment.id} не найден")

    # 1. Бонусные дни: промокод + бонус приглашённому за первую оплату
    extra_days = 0
    promo = None
    if payment.promo_code:
        result = await session.execute(
            select(PromoCode).where(PromoCode.code == payment.promo_code)
        )
        promo = result.scalar_one_or_none()
        if promo:
            extra_days += promo.bonus_days
            promo.used_count += 1

    was_first_payment = user.referred_by_id is not None and not await _has_paid_before(
        session, user
    )
    if was_first_payment:
        extra_days += ref_service.invited_bonus_days()

    # 2. Фиксируем платёж ДО начисления — если что-то упадёт дальше,
    #    двойного начисления при повторе вебхука не будет.
    payment.status = PaymentStatus.PAID
    payment.external_id = external_id
    payment.paid_at = utcnow()
    if raw_payload:
        payment.payload = raw_payload
    await session.flush()

    # 3. Продлеваем подписку (внутри считается streak-бонус)
    subscription, total_days = await sub_service.extend(
        session, user, payment.tariff_code, payment.period_months, extra_days
    )
    payment.days_granted = total_days
    await session.flush()

    # 4. Реферальная программа
    referrer, slot_granted = await ref_service.on_first_payment(session, user)

    log.info("Платёж #%s проведён: tg=%s +%s дн.", payment.id, user.telegram_id, total_days)

    return {
        "already_processed": False,
        "payment_id": payment.id,
        "user": user,
        "subscription": subscription,
        "days_granted": total_days,
        "bonus_days": extra_days,
        "streak_count": subscription.streak_count,
        "referrer": referrer,
        "referrer_slot_granted": slot_granted,
    }


async def _has_paid_before(session: AsyncSession, user: User) -> bool:
    """Были ли успешные оплаты до текущей."""
    result = await session.execute(
        select(Payment.id).where(
            Payment.user_id == user.id,
            Payment.status == PaymentStatus.PAID,
        )
    )
    return result.first() is not None


def invoice_belongs_to_payment(stored_external_id: str | None, webhook_external_id: str) -> bool:
    """
    Тот ли это счёт, который мы выставляли под этот заказ.

    external_id записывается в момент создания счёта. Если уведомление
    принесло другой — это не наш платёж: кто-то подставил id настоящей,
    но чужой (и, скорее всего, куда более дешёвой) оплаты.

    Пустой stored_external_id означает, что записать его не успели
    (оборвалась связь сразу после выставления счёта) — тогда сверять
    нечего и платёж ищется по external_id из уведомления.
    """
    if not stored_external_id or not webhook_external_id:
        return True
    return stored_external_id == webhook_external_id


async def find_payment_by_external_id(
    session: AsyncSession, provider: str, external_id: str
) -> Payment | None:
    result = await session.execute(
        select(Payment).where(Payment.provider == provider, Payment.external_id == external_id)
    )
    return result.scalar_one_or_none()


async def mark_failed(session: AsyncSession, payment: Payment, reason: str = "") -> None:
    payment.status = PaymentStatus.FAILED
    payment.payload = {**(payment.payload or {}), "fail_reason": reason}
    await session.flush()


# ── Витрина тарифов ────────────────────────────────────────────────────────


def tariff_showcase(discount_percent: int = 0) -> list[dict]:
    """
    Тарифы с рассчитанными ценами — для клавиатур бота и Mini App.

    discount_percent — активная персональная скидка, если есть.
    """
    showcase = []
    for tariff in get_tariffs().get("tariffs", []):
        periods = []
        for period in get_tariffs().get("periods", []):
            months = period["months"]
            price = calc_price(tariff["code"], months, discount_percent)
            full_price = calc_price(tariff["code"], months, 0)
            periods.append(
                {
                    "months": months,
                    "title": period["title"],
                    "badge": period.get("badge"),
                    "price": price,
                    "full_price": full_price,
                    "price_per_month": round(price / months),
                }
            )
        showcase.append(
            {
                "code": tariff["code"],
                "title": tariff["title"],
                "description": tariff.get("description", ""),
                "badge": tariff.get("badge"),
                "periods": periods,
            }
        )
    return showcase
