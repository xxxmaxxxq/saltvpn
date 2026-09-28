"""
Отправка сообщений пользователям из любого процесса (бот, API, воркер).

Отдельный модуль нужен, потому что уведомления шлёт не только бот: воркер
рассылает напоминания, API — подтверждения оплат по вебхукам.
Везде соблюдается троттлинг, иначе Telegram выдаст 429.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.bot import keyboards as kb
from app.config import get_settings, text
from app.services import users as user_service

log = logging.getLogger(__name__)
settings = get_settings()

# Telegram допускает ~30 сообщений в секунду; берём с запасом
BROADCAST_DELAY = 0.05


def create_bot() -> Bot:
    """Бот с HTML-разметкой по умолчанию — тексты в messages.yml пишутся в HTML."""
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def safe_send(bot: Bot, telegram_id: int, body: str, reply_markup=None) -> bool:
    """
    Отправить сообщение, не роняя вызывающий код.

    Возвращает True при успехе. Блокировка бота пользователем — штатная
    ситуация, а не ошибка: просто пропускаем.
    """
    try:
        await bot.send_message(telegram_id, body, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        log.info("tg=%s заблокировал бота — пропускаем", telegram_id)
        return False
    except TelegramRetryAfter as err:
        log.warning("Rate-limit Telegram, ждём %s c", err.retry_after)
        await asyncio.sleep(err.retry_after)
        return await safe_send(bot, telegram_id, body, reply_markup)
    except Exception as err:
        log.error("Ошибка отправки tg=%s: %s", telegram_id, err)
        return False


async def send_payment_success(bot: Bot, result: dict) -> None:
    """Чек об оплате + уведомление пригласившему, если он получил слот."""
    user = result["user"]
    subscription = result["subscription"]

    from app.config import tariff_by_code

    tariff = tariff_by_code(subscription.tariff_code)
    bonus_days = result.get("bonus_days", 0)
    streak = result.get("streak_count", 0)

    bonus_text = ""
    if bonus_days:
        bonus_text += f" (включая {bonus_days} бонусных)"
    if streak > 1:
        bonus_text += f"\n🔥 Серия продлений: <b>{streak}</b>"

    await safe_send(
        bot,
        user.telegram_id,
        text(
            "payment_success",
            tariff_title=tariff["title"] if tariff else subscription.tariff_code,
            days=result["days_granted"],
            bonus_text=bonus_text,
            expires_date=subscription.expires_at.strftime("%d.%m.%Y"),
        ),
        reply_markup=kb.account_keyboard(),
    )

    # Уведомление пригласившему
    referrer = result.get("referrer")
    if referrer is not None:
        reward = (
            "🎉 Вам начислен <b>+1 постоянный слот устройства</b>!"
            if result.get("referrer_slot_granted")
            else "Ещё немного — и вы получите дополнительный слот устройства."
        )
        await safe_send(
            bot,
            referrer.telegram_id,
            text(
                "referral_qualified",
                name=user_service.display_name(user.first_name, user.username),
                reward_text=reward,
            ),
        )


async def send_expiry_notice(
    bot: Bot,
    telegram_id: int,
    kind: str,
    expires_at: datetime,
    discount: int = 0,
    promo_code: str | None = None,
    bonus: int = 1,
) -> bool:
    """Одно из триггерных напоминаний о продлении."""
    body = text(
        f"notify_{kind}",
        expires_date=expires_at.strftime("%d.%m.%Y"),
        discount=discount,
        bonus=bonus,
    )
    return await safe_send(bot, telegram_id, body, reply_markup=kb.renew_keyboard(promo_code))


async def send_trial_offer(
    bot: Bot,
    telegram_id: int,
    discount: int,
    price_old: int,
    price_new: int,
    valid_hours: int,
    promo_code: str,
) -> bool:
    """Таймер-оффер за 2 часа до конца триала — самый конверсионный триггер."""
    body = text(
        "notify_trial_offer",
        discount=discount,
        price_old=price_old,
        price_new=price_new,
        valid_hours=valid_hours,
    )
    return await safe_send(bot, telegram_id, body, reply_markup=kb.renew_keyboard(promo_code))


async def send_healed_notice(bot: Bot, telegram_id: int, subscription_url: str) -> bool:
    """«Мы уже всё починили» — сообщение после автоматического восстановления."""
    return await safe_send(
        bot,
        telegram_id,
        text("notify_healed"),
        reply_markup=kb.healing_result_keyboard(subscription_url),
    )


async def notify_admins(bot: Bot, body: str) -> None:
    for admin_id in settings.admin_id_list:
        await safe_send(bot, admin_id, body)
