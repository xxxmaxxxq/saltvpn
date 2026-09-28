"""
Покупка и продление: выбор тарифа -> периода -> способа оплаты.

Тарифы и цены берутся из config/tariffs.yml, поэтому добавление нового
тарифа не требует правки этого файла.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import get_settings, limits, text
from app.db import session_scope
from app.payments.registry import enabled_providers, get_provider
from app.payments.stars import StarsProvider
from app.services import billing
from app.services import users as user_service

log = logging.getLogger(__name__)
router = Router(name="buy")
settings = get_settings()


async def _personal_discount(session, user) -> tuple[int, str | None]:
    """Действующий персональный оффер: (процент скидки, код)."""
    promo = await billing.active_personal_offer(session, user)
    return (promo.discount_percent, promo.code) if promo else (0, None)


@router.callback_query(F.data.startswith("buy:menu"))
@answered_callback
async def show_tariffs(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user, _ = await user_service.get_or_create(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        discount, _code = await _personal_discount(session, user)

    showcase = billing.tariff_showcase(discount)
    await callback.message.answer(
        text("choose_tariff", device_limit=limits().get("device_limit_default", 2)),
        reply_markup=kb.tariffs_keyboard(showcase),
    )


@router.callback_query(F.data.startswith("buy:tariff:"))
@answered_callback
async def show_periods(callback: CallbackQuery) -> None:
    tariff_code = callback.data.split(":")[2]
    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, callback.from_user.id)
        discount, _ = await _personal_discount(session, user) if user else (0, None)

    showcase = billing.tariff_showcase(discount)
    tariff = next((t for t in showcase if t["code"] == tariff_code), None)
    if tariff is None:
        await callback.message.answer(text("error_generic"))
        return

    await callback.message.answer(
        text("choose_period", tariff_title=tariff["title"]),
        reply_markup=kb.periods_keyboard(tariff),
    )


@router.callback_query(F.data.startswith("buy:period:"))
@answered_callback
async def show_providers(callback: CallbackQuery) -> None:
    _, _, tariff_code, months = callback.data.split(":")
    months = int(months)

    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, callback.from_user.id)
        discount, promo_code = await _personal_discount(session, user) if user else (0, None)

    from app.config import calc_price

    amount = calc_price(tariff_code, months, discount)
    promo_line = f"🎁 Применена скидка <b>−{discount}%</b>\n" if discount else ""

    providers = enabled_providers()
    if not providers:
        await callback.message.answer(
            "⚠️ Способы оплаты временно недоступны. Напишите в поддержку — выдадим доступ вручную."
        )
        return

    await callback.message.answer(
        text("choose_provider", amount=amount, promo_line=promo_line),
        reply_markup=kb.providers_keyboard(providers, tariff_code, months),
    )


@router.callback_query(F.data.startswith("buy:pay:"))
@answered_callback
async def create_payment(callback: CallbackQuery) -> None:
    """Создать счёт у выбранного провайдера и отдать пользователю ссылку."""
    _, _, provider_code, tariff_code, months = callback.data.split(":")
    months = int(months)

    async with session_scope() as session:
        user, _ = await user_service.get_or_create(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        discount, promo_code = await _personal_discount(session, user)
        payment, amount = await billing.create_order(
            session, user, tariff_code, months, provider_code, promo_code
        )
        payment_id = payment.id

    provider = get_provider(provider_code)
    description = f"VPN {tariff_code.upper()} на {months} мес."

    try:
        invoice = await provider.create_invoice(
            payment_id=payment_id,
            amount_rub=amount,
            description=description,
            telegram_id=callback.from_user.id,
        )
    except Exception as err:
        log.exception("Не удалось создать счёт (%s): %s", provider_code, err)
        await callback.message.answer(text("payment_failed"))
        return

    # Telegram Stars: счёт отправляется сообщением, а не ссылкой
    if invoice.is_telegram_invoice:
        stars = StarsProvider.rub_to_stars(amount)
        await callback.message.answer_invoice(
            title=f"VPN · {months} мес.",
            description=description,
            payload=f"{payment_id}:{tariff_code}:{months}",
            currency="XTR",
            prices=[LabeledPrice(label=description, amount=stars)],
        )
        return

    # Сохраняем external_id, чтобы связать вебхук с нашим платежом
    async with session_scope() as session:
        from app.models import Payment

        payment = await session.get(Payment, payment_id)
        if payment:
            payment.external_id = invoice.external_id

    await callback.message.answer(
        text("invoice_created", amount=amount),
        reply_markup=kb.payment_link_keyboard(invoice.payment_url),
    )


# ── Telegram Stars: подтверждение оплаты ───────────────────────────────────


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    """Telegram спрашивает, готовы ли мы принять платёж. Отвечаем сразу."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_stars_payment(message: Message) -> None:
    """Звёзды пришли — начисляем дни тем же кодом, что и для вебхуков."""
    if message.successful_payment is None or message.from_user is None:
        return

    payload = message.successful_payment.invoice_payload
    try:
        payment_id = int(payload.split(":")[0])
    except (ValueError, IndexError):
        log.error("Некорректный payload Stars: %s", payload)
        return

    charge_id = message.successful_payment.telegram_payment_charge_id

    async with session_scope() as session:
        from app.models import Payment

        payment = await session.get(Payment, payment_id)
        if payment is None:
            log.error("Платёж #%s из Stars не найден", payment_id)
            return

        result = await billing.apply_payment(
            session,
            payment,
            external_id=charge_id,
            raw_payload={"provider": "stars", "charge_id": charge_id},
        )

    if result.get("already_processed"):
        return

    from app.bot.notifications import send_payment_success

    await send_payment_success(message.bot, result)
