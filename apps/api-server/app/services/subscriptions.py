"""
Сервис подписок: продление, streak-бонусы, пауза, синхронизация с Marzban.

Главные инварианты:
  * У пользователя одна подписка. Смена тарифа меняет её, а не создаёт вторую.
  * Ссылка-подписка не меняется никогда — меняется только её содержимое.
  * Все операции с деньгами и днями идут через этот модуль, чтобы бонусы
    и streak считались в одном месте.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, get_tariffs, limits, tariff_by_code
from app.marzban import MarzbanClient, MarzbanError, build_inbounds, filter_available
from app.models import (
    Node,
    NodeStatus,
    PausePeriod,
    Subscription,
    SubscriptionStatus,
    User,
    utcnow,
)

log = logging.getLogger(__name__)
settings = get_settings()


# ── Выбор нод под тариф ────────────────────────────────────────────────────


async def nodes_for_tariff(session: AsyncSession, tariff_code: str) -> list[Node]:
    """
    Какие ноды входят в тариф.

    Берём локации из tariffs.yml, затем живые ноды этих локаций.
    Если в локации несколько нод — выбираем наименее загруженную
    (простая балансировка, которой хватает до нескольких тысяч юзеров).
    """
    tariff = tariff_by_code(tariff_code)
    if not tariff:
        raise ValueError(f"Неизвестный тариф: {tariff_code}")

    chosen: list[Node] = []
    for location in tariff["locations"]:
        result = await session.execute(
            select(Node)
            .where(Node.location == location, Node.status != NodeStatus.DISABLED)
            .order_by(Node.status.asc(), Node.users_count.asc())
        )
        nodes = list(result.scalars())
        if nodes:
            chosen.append(nodes[0])
        else:
            log.warning("Для локации %s нет доступных нод", location)
    return chosen


async def sync_to_marzban(
    session: AsyncSession, user: User, subscription: Subscription
) -> str | None:
    """
    Привести аккаунт в панели в соответствие с подпиской в нашей БД.

    Создаёт аккаунт, если его нет; иначе обновляет срок и набор inbounds.
    Возвращает ссылку-подписку либо None, если панель недоступна
    (тогда операция попадёт в очередь повтора — деньги при этом уже учтены).
    """
    from app.services import users as user_service

    nodes = await nodes_for_tariff(session, subscription.tariff_code)
    if not nodes:
        log.error("Нет нод под тариф %s — аккаунт не синхронизирован", subscription.tariff_code)
        return None

    inbounds = build_inbounds([n.location for n in nodes], [n.code for n in nodes])
    device_limit = user_service.effective_device_limit(user, subscription)

    try:
        async with MarzbanClient() as mz:
            # Сверяемся с панелью: просим только те inbounds, которые в ней есть
            available = await mz.list_inbounds()
            inbounds = filter_available(inbounds, available)
            if not inbounds:
                log.error(
                    "В панели нет ни одного нужного inbound (ожидали %s). "
                    "Проверьте конфигурацию Xray.",
                    [n.code for n in nodes],
                )
                return None

            existing = await mz.get_user(user.marzban_username)
            note = f"tg={user.telegram_id} tariff={subscription.tariff_code} devices={device_limit}"

            if existing is None:
                await mz.create_user(
                    username=user.marzban_username,
                    inbounds=inbounds,
                    expire_at=subscription.expires_at,
                    data_limit_bytes=int(limits().get("traffic_limit_gb", 0)) * 1024**3,
                    note=note,
                )
            else:
                await mz.modify_user(
                    user.marzban_username,
                    inbounds=inbounds,
                    proxies={proto: {} for proto in inbounds},
                    expire=int(subscription.expires_at.timestamp()),
                    status="active" if subscription.is_active else "disabled",
                    note=note,
                )

            return await mz.get_subscription_url(user.marzban_username)
    except MarzbanError as err:
        log.error("Marzban недоступен при синхронизации tg=%s: %s", user.telegram_id, err)
        return None


def panel_is_stale(panel_user: dict | None, subscription: Subscription) -> bool:
    """
    Разошёлся ли срок в панели с тем, что записано у нас.

    Оплата и синхронизация с панелью — два разных шага, и второй может
    не состояться: панель перезагружается, сеть моргнула. Деньги при этом
    уже зачтены, дни в нашей базе добавлены, а в панели остался старый
    срок — пользователь заплатил и сидит без доступа. Поэтому при каждой
    выдаче подписки сверяем сроки и при расхождении синхронизируем заново.

    Минута допуска — на округление: панель хранит срок целыми секундами.
    """
    if not panel_user:
        return True
    expire = panel_user.get("expire")
    if not expire:
        # Бессрочный аккаунт в панели при срочной подписке у нас — тоже расхождение
        return True
    return abs(int(expire) - int(subscription.expires_at.timestamp())) > 60


def subscription_url(user: User) -> str:
    """Наша собственная ссылка-подписка (проксирует Marzban через /sub/{token})."""
    return f"{settings.public_base_url.rstrip('/')}/sub/{user.subscription_token}"


# ── Продление и streak ─────────────────────────────────────────────────────


def calc_streak_bonus(subscription: Subscription, now: datetime | None = None) -> tuple[int, int]:
    """
    Посчитать новую серию продлений и бонусные дни.

    Логика: продлил до истечения или в течение grace_hours после — серия растёт
    и даёт min(серия, max_bonus) бонусных дней. Опоздал — серия обнуляется.

    Возвращает (новый_streak, бонусные_дни).
    """
    cfg = get_tariffs().get("streak", {})
    if not cfg.get("enabled", True):
        return 0, 0

    now = now or utcnow()
    grace = timedelta(hours=int(cfg.get("grace_hours", 24)))

    if subscription.is_trial:
        # Первая покупка после триала открывает серию
        return 1, 0

    if now <= subscription.expires_at + grace:
        new_streak = subscription.streak_count + 1
    else:
        new_streak = 1  # серия порвана, начинаем заново

    bonus_per_step = int(cfg.get("bonus_days_per_step", 1))
    max_bonus = int(cfg.get("max_bonus_days", 7))
    bonus = min(new_streak * bonus_per_step, max_bonus) if new_streak > 1 else 0
    return new_streak, bonus


async def extend(
    session: AsyncSession,
    user: User,
    tariff_code: str,
    months: int,
    extra_days: int = 0,
) -> tuple[Subscription, int]:
    """
    Продлить (или создать) подписку после успешной оплаты.

    extra_days — бонусы от промокода или реферальной программы.
    Возвращает (подписка, всего_начислено_дней).
    """
    now = utcnow()
    base_days = months * 30
    subscription = user.subscription

    if subscription is None:
        subscription = Subscription(
            user_id=user.id,
            tariff_code=tariff_code,
            status=SubscriptionStatus.ACTIVE,
            expires_at=now,
            is_trial=False,
            device_limit=int(limits().get("device_limit_default", 2)),
        )
        session.add(subscription)
        await session.flush()
        streak, streak_bonus = 1, 0
    else:
        streak, streak_bonus = calc_streak_bonus(subscription, now)

    total_days = base_days + extra_days + streak_bonus

    # Если подписка ещё жива — добавляем к остатку, иначе считаем от «сейчас».
    start_point = subscription.expires_at if subscription.expires_at > now else now
    subscription.expires_at = start_point + timedelta(days=total_days)
    subscription.tariff_code = tariff_code
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.is_trial = False
    subscription.streak_count = streak
    subscription.device_limit = int(limits().get("device_limit_default", 2))
    subscription.paused_until = None
    await session.flush()

    await sync_to_marzban(session, user, subscription)

    log.info(
        "Продление tg=%s тариф=%s дней=%s (база %s + бонус %s + streak %s) до %s",
        user.telegram_id,
        tariff_code,
        total_days,
        base_days,
        extra_days,
        streak_bonus,
        subscription.expires_at.date(),
    )
    return subscription, total_days


async def change_tariff(session: AsyncSession, user: User, new_tariff: str) -> Subscription:
    """Сменить локации без изменения срока (апгрейд/даунгрейд внутри периода)."""
    subscription = user.subscription
    if subscription is None:
        raise ValueError("У пользователя нет подписки")
    subscription.tariff_code = new_tariff
    await session.flush()
    await sync_to_marzban(session, user, subscription)
    return subscription


async def expire(session: AsyncSession, user: User, subscription: Subscription) -> None:
    """Отключить доступ по истечении срока."""
    subscription.status = SubscriptionStatus.EXPIRED
    await session.flush()
    try:
        async with MarzbanClient() as mz:
            await mz.disable_user(user.marzban_username)
    except MarzbanError as err:
        log.error("Не удалось отключить tg=%s в Marzban: %s", user.telegram_id, err)


# ── Пауза подписки ─────────────────────────────────────────────────────────


def pause_days_left(subscription: Subscription) -> int:
    """Сколько дней заморозки осталось в текущем календарном году."""
    cfg = get_tariffs().get("pause", {})
    max_days = int(cfg.get("max_days_per_year", 30))
    current_year = utcnow().year
    used = subscription.pause_days_used if subscription.pause_year == current_year else 0
    return max(0, max_days - used)


def can_pause(subscription: Subscription, days: int) -> tuple[bool, str]:
    """Проверить возможность заморозки. Возвращает (можно, причина_отказа)."""
    cfg = get_tariffs().get("pause", {})
    if not cfg.get("enabled", True):
        return False, "функция временно отключена"
    if subscription.is_trial and not cfg.get("allowed_for_trial", False):
        return False, "на бесплатном периоде заморозка недоступна"
    if subscription.status == SubscriptionStatus.PAUSED:
        return False, "подписка уже заморожена"
    if not subscription.is_active:
        return False, "подписка неактивна"
    if days < int(cfg.get("min_days", 1)):
        return False, "минимум 1 день"
    if days > int(cfg.get("max_days_at_once", 30)):
        return False, f"максимум {cfg.get('max_days_at_once', 30)} дней за раз"
    if days > pause_days_left(subscription):
        return False, f"в этом году осталось {pause_days_left(subscription)} дн."
    return True, ""


async def pause(session: AsyncSession, user: User, days: int) -> Subscription:
    """
    Заморозить подписку.

    Дни НЕ сгорают: срок окончания сдвигается вперёд ровно на срок паузы,
    а доступ к VPN временно выключается в панели.
    """
    subscription = user.subscription
    if subscription is None:
        raise ValueError("У пользователя нет подписки")

    ok, reason = can_pause(subscription, days)
    if not ok:
        raise ValueError(reason)

    now = utcnow()
    until = now + timedelta(days=days)

    current_year = now.year
    if subscription.pause_year != current_year:
        subscription.pause_year = current_year
        subscription.pause_days_used = 0

    subscription.status = SubscriptionStatus.PAUSED
    subscription.paused_until = until
    subscription.expires_at = subscription.expires_at + timedelta(days=days)
    subscription.pause_days_used += days

    session.add(
        PausePeriod(
            subscription_id=subscription.id,
            started_at=now,
            planned_until=until,
            days_requested=days,
        )
    )
    await session.flush()

    try:
        async with MarzbanClient() as mz:
            await mz.disable_user(user.marzban_username)
    except MarzbanError as err:
        log.error("Не удалось заморозить tg=%s в Marzban: %s", user.telegram_id, err)

    log.info("Пауза tg=%s на %s дн. до %s", user.telegram_id, days, until.date())
    return subscription


async def resume(session: AsyncSession, user: User, early: bool = True) -> Subscription:
    """
    Разморозить подписку.

    При досрочной разморозке неиспользованные дни паузы возвращаются
    в годовой лимит, а срок подписки укорачивается на неиспользованный остаток.
    """
    subscription = user.subscription
    if subscription is None or subscription.status != SubscriptionStatus.PAUSED:
        raise ValueError("подписка не на паузе")

    now = utcnow()
    result = await session.execute(
        select(PausePeriod)
        .where(PausePeriod.subscription_id == subscription.id, PausePeriod.ended_at.is_(None))
        .order_by(PausePeriod.started_at.desc())
    )
    period = result.scalars().first()

    if period and early and subscription.paused_until and subscription.paused_until > now:
        unused_days = max(0, (subscription.paused_until - now).days)
        subscription.expires_at -= timedelta(days=unused_days)
        subscription.pause_days_used = max(0, subscription.pause_days_used - unused_days)
        period.days_actual = period.days_requested - unused_days
    elif period:
        period.days_actual = period.days_requested

    if period:
        period.ended_at = now

    subscription.status = (
        SubscriptionStatus.ACTIVE if subscription.expires_at > now else SubscriptionStatus.EXPIRED
    )
    subscription.paused_until = None
    await session.flush()

    if subscription.status == SubscriptionStatus.ACTIVE:
        try:
            async with MarzbanClient() as mz:
                await mz.enable_user(user.marzban_username)
        except MarzbanError as err:
            log.error("Не удалось разморозить tg=%s в Marzban: %s", user.telegram_id, err)

    log.info("Разморозка tg=%s, действует до %s", user.telegram_id, subscription.expires_at.date())
    return subscription


# ── Вспомогательное ────────────────────────────────────────────────────────


async def refresh_traffic(session: AsyncSession, user: User) -> int:
    """Подтянуть из панели использованный трафик (для кабинета)."""
    if not user.subscription:
        return 0
    try:
        async with MarzbanClient() as mz:
            used = await mz.get_user_usage(user.marzban_username)
    except MarzbanError:
        return user.subscription.traffic_used_bytes
    user.subscription.traffic_used_bytes = used
    await session.flush()
    return used
