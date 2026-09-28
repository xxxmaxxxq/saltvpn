"""
Реферальная программа на слотах устройств.

Почему слоты, а не деньги: кэшбэк 25% режет выручку с каждого платежа навсегда,
а слот устройства стоит нам около нуля и при этом сильнее привязывает
пользователя (на ключе уже сидит семья — уходить дорого).

Правила:
  * реферал засчитывается только после ПЕРВОЙ ОПЛАТЫ приглашённого;
  * каждые N оплативших (tariffs.yml -> referral.qualified_per_slot) = +1 слот;
  * приглашённый получает бонусные дни к первой оплате;
  * самоприглашение и смена реферера запрещены.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, get_tariffs
from app.models import Referral, User, utcnow

log = logging.getLogger(__name__)
settings = get_settings()


def _cfg() -> dict:
    return get_tariffs().get("referral", {})


def referral_link(user: User) -> str:
    return f"https://t.me/{settings.bot_username}?start=ref_{user.referral_code}"


async def attach_referrer(session: AsyncSession, new_user: User, referral_code: str) -> bool:
    """
    Привязать пригласившего при первом /start с реферальным кодом.

    Возвращает True, если привязка состоялась.
    """
    if not _cfg().get("enabled", True):
        return False
    if new_user.referred_by_id:
        return False  # реферер назначается один раз и навсегда

    from app.services import users as user_service

    referrer = await user_service.get_by_referral_code(session, referral_code)
    if not referrer or referrer.id == new_user.id:
        return False

    new_user.referred_by_id = referrer.id
    session.add(Referral(referrer_id=referrer.id, referred_id=new_user.id))
    await session.flush()
    log.info("Реферал: tg=%s пришёл от tg=%s", new_user.telegram_id, referrer.telegram_id)
    return True


def invited_bonus_days() -> int:
    """Бонусные дни, которые получает приглашённый при первой оплате."""
    return int(_cfg().get("invited_bonus_days", 0)) if _cfg().get("enabled", True) else 0


async def is_first_payment(session: AsyncSession, user: User) -> bool:
    """Была ли у пользователя хоть одна оплата раньше."""
    from app.models import Payment, PaymentStatus

    result = await session.execute(
        select(func.count(Payment.id)).where(
            Payment.user_id == user.id, Payment.status == PaymentStatus.PAID
        )
    )
    return (result.scalar() or 0) <= 1  # текущий платёж уже записан как paid


async def on_first_payment(session: AsyncSession, user: User) -> tuple[User | None, bool]:
    """
    Отметить реферала как оплатившего и, если набралось нужное число,
    выдать пригласившему дополнительный слот устройства.

    Возвращает (пригласивший, выдан_ли_новый_слот) — бот по этим данным
    отправляет уведомление.
    """
    if not _cfg().get("enabled", True) or not user.referred_by_id:
        return None, False

    result = await session.execute(select(Referral).where(Referral.referred_id == user.id))
    referral = result.scalar_one_or_none()
    if referral is None or referral.is_qualified:
        return None, False

    referral.is_qualified = True
    referral.qualified_at = utcnow()
    await session.flush()

    referrer = await session.get(User, user.referred_by_id)
    if referrer is None:
        return None, False

    # Сколько всего оплативших рефералов у пригласившего
    result = await session.execute(
        select(func.count(Referral.id)).where(
            Referral.referrer_id == referrer.id, Referral.is_qualified.is_(True)
        )
    )
    qualified_count = result.scalar() or 0

    per_slot = int(_cfg().get("qualified_per_slot", 2))
    max_slots = int(_cfg().get("max_slots", 3))
    deserved_slots = min(qualified_count // per_slot, max_slots)

    slot_granted = False
    if deserved_slots > referrer.device_slots_bonus:
        referrer.device_slots_bonus = deserved_slots
        slot_granted = True
        referral.reward_granted = True
        await session.flush()

        # Сразу применяем новый лимит устройств в панели
        if referrer.subscription:
            from app.services import subscriptions as sub_service

            await sub_service.sync_to_marzban(session, referrer, referrer.subscription)

        log.info(
            "Реферальный слот выдан: tg=%s теперь имеет %s доп. слотов",
            referrer.telegram_id,
            deserved_slots,
        )

    return referrer, slot_granted


async def stats(session: AsyncSession, user: User) -> dict:
    """Статистика для кабинета и экрана «Пригласить друзей»."""
    result = await session.execute(
        select(
            func.count(Referral.id),
            func.count(Referral.id).filter(Referral.is_qualified.is_(True)),
        ).where(Referral.referrer_id == user.id)
    )
    total, qualified = result.one()
    per_slot = int(_cfg().get("qualified_per_slot", 2))
    max_slots = int(_cfg().get("max_slots", 3))
    slots = min((qualified or 0) // per_slot, max_slots)
    next_in = 0 if slots >= max_slots else per_slot - ((qualified or 0) % per_slot)

    return {
        "total": total or 0,
        "qualified": qualified or 0,
        "slots_earned": slots,
        "next_slot_in": next_in,
        "per_slot": per_slot,
        "link": referral_link(user),
    }
