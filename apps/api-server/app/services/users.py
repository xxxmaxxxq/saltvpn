"""
Сервис пользователей: регистрация, триал, антифрод.

Здесь живёт вся логика «первого касания» — от /start до выданного ключа.
Цель: уложиться в 60 секунд и не задать ни одного лишнего вопроса.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, limits
from app.db import get_redis
from app.models import Subscription, SubscriptionStatus, User, utcnow
from app.services import subscriptions as sub_service
from app.utils.security import generate_referral_code, generate_token, hash_ip

log = logging.getLogger(__name__)
settings = get_settings()

# Сколько помним «этот IP уже брал триал» (секунды)
TRIAL_IP_TTL = 30 * 24 * 3600


def display_name(first_name: str | None, username: str | None = None) -> str:
    """
    Как обратиться к человеку.

    У части аккаунтов имя скрыто настройками приватности, у ботов и вовсе
    пустое. Поэтому цепочка: имя → @username → нейтральное «друг».
    Без неё в приветствии появлялось бы «Привет, None!».
    """
    name = (first_name or "").strip()
    if name:
        return name
    if username:
        return f"@{username}"
    return "друг"


async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_by_referral_code(session: AsyncSession, code: str) -> User | None:
    result = await session.execute(select(User).where(User.referral_code == code))
    return result.scalar_one_or_none()


async def get_or_create(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    language_code: str | None = None,
) -> tuple[User, bool]:
    """
    Найти пользователя или создать нового.

    Возвращает (пользователь, создан_ли_сейчас) — второй флаг нужен, чтобы
    показать приветствие новичку и не показывать его вернувшемуся.
    """
    user = await get_by_telegram_id(session, telegram_id)
    if user:
        # Обновляем «косметику»: человек мог сменить username
        changed = False
        if username and user.username != username:
            user.username, changed = username, True
        if first_name and user.first_name != first_name:
            user.first_name, changed = first_name, True
        if changed:
            await session.flush()
        return user, False

    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        language_code=language_code,
        referral_code=generate_referral_code(),
        marzban_username=f"u{telegram_id}",
        subscription_token=generate_token(32),
        is_admin=telegram_id in settings.admin_id_list,
    )
    session.add(user)
    await session.flush()

    # Явно подгружаем связь: у только что созданного объекта она не загружена,
    # и первое же обращение к user.subscription попыталось бы сходить в базу
    # в неподходящий момент — asyncpg и aiosqlite отвечают на это
    # MissingGreenlet, а бот молча падает на /start
    await session.refresh(user, attribute_names=["subscription"])

    log.info("Новый пользователь: tg=%s", telegram_id)
    return user, True


# ── Антифрод триала ────────────────────────────────────────────────────────


async def can_take_trial(user: User, client_ip: str | None = None) -> tuple[bool, str]:
    """
    Можно ли выдать бесплатный период.

    Проверки:
      1. Триал уже брали этим аккаунтом.
      2. С этого IP триал уже брали за последние 30 дней (мягкая защита
         от фарма нескольких аккаунтов с одного устройства).

    Возвращает (можно, причина_отказа).
    """
    if user.trial_used:
        return False, "trial_already_used"

    if client_ip:
        redis = get_redis()
        key = f"trial_ip:{hash_ip(client_ip, settings.jwt_secret)}"
        if await redis.exists(key):
            return False, "trial_ip_used"

    return True, ""


async def mark_trial_ip(client_ip: str | None) -> None:
    if not client_ip:
        return
    redis = get_redis()
    key = f"trial_ip:{hash_ip(client_ip, settings.jwt_secret)}"
    await redis.set(key, "1", ex=TRIAL_IP_TTL)


async def activate_trial(
    session: AsyncSession, user: User, client_ip: str | None = None
) -> Subscription:
    """
    Выдать бесплатный период.

    Создаёт подписку в нашей БД и аккаунт в Marzban с доступом ко всем
    локациям (тариф из tariffs.yml -> limits.trial_tariff).
    """
    cfg = limits()
    days = int(cfg.get("trial_days", 3))
    tariff = cfg.get("trial_tariff", "multi")

    subscription = Subscription(
        user_id=user.id,
        tariff_code=tariff,
        status=SubscriptionStatus.TRIAL,
        expires_at=utcnow() + timedelta(days=days),
        is_trial=True,
        device_limit=int(cfg.get("device_limit_trial", 1)),
    )
    session.add(subscription)
    user.trial_used = True
    await session.flush()

    # Заводим аккаунт в панели и получаем ссылку-подписку
    await sub_service.sync_to_marzban(session, user, subscription)
    await mark_trial_ip(client_ip)

    log.info("Триал выдан: tg=%s на %s дн.", user.telegram_id, days)
    return subscription


# ── Лимиты устройств ───────────────────────────────────────────────────────


def effective_device_limit(user: User, subscription: Subscription) -> int:
    """
    Сколько устройств доступно пользователю.

    База из тарифа + слоты, заработанные приглашениями, но не выше потолка.
    """
    cfg = limits()
    base = subscription.device_limit
    total = base + user.device_slots_bonus
    return min(total, int(cfg.get("device_limit_max", 5)))


# ── Rate-limit ─────────────────────────────────────────────────────────────

# Фолбэк для LOCAL_MODE: на мастере Redis не поднят (см. HANDOFF.md), а
# без счётчика вообще панель Marzban можно было задолбать одним человеком.
# Память процесса хватает — бот сейчас работает в одном экземпляре.
_rate_limit_memory: dict[int, tuple[int, float]] = {}


async def check_rate_limit(telegram_id: int, limit: int = 20, window: int = 60) -> bool:
    """
    Счётчик действий пользователя за окно времени.

    True  — можно продолжать,
    False — превышен лимит (бот ответит вежливым «подождите минуту»).

    В проде счётчик живёт в Redis — общий на все копии бота при масштабировании.
    В LOCAL_MODE Redis не поднят, поэтому считаем в памяти процесса.
    """
    if settings.local_mode:
        now = time.monotonic()
        count, window_start = _rate_limit_memory.get(telegram_id, (0, now))
        if now - window_start > window:
            count, window_start = 0, now
        count += 1
        _rate_limit_memory[telegram_id] = (count, window_start)
        return count <= limit

    redis = get_redis()
    key = f"rl:{telegram_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    return count <= limit
