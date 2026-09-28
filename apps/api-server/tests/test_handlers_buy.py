"""
Покупка: выбор тарифа -> периода -> провайдера -> счёт.

Отдельно проверяется пересчёт цены со скидкой (персональный промокод от
таймер-оффера) — это единственное место, где ошибка бьёт по деньгам сразу
в обе стороны: слишком низкая цена — убыток, слишком высокая — жалоба.
"""

from app.bot.handlers.buy import create_payment, show_periods, show_providers, show_tariffs
from app.config import calc_price
from app.db import create_all_tables, session_scope
from app.services import billing
from tests.bot_support import fake_callback, fake_tg_user


async def _setup():
    await create_all_tables()


async def test_show_tariffs_lists_all_three():
    await _setup()
    user = fake_tg_user(2001)
    callback = fake_callback("buy:menu", user)

    await show_tariffs(callback)

    callback.message.answer.assert_awaited_once()
    _, kwargs = callback.message.answer.call_args
    markup = kwargs["reply_markup"]
    codes = {btn.callback_data for row in markup.inline_keyboard for btn in row}
    assert "buy:tariff:nl" in codes
    assert "buy:tariff:ru" in codes
    assert "buy:tariff:multi" in codes


async def test_show_periods_for_known_tariff():
    await _setup()
    callback = fake_callback("buy:tariff:nl", fake_tg_user(2002))

    await show_periods(callback)

    callback.message.answer.assert_awaited_once()
    text_sent = callback.message.answer.call_args[0][0]
    assert "Европа" in text_sent or "nl" in text_sent.lower()


async def test_show_periods_unknown_tariff_shows_error_not_crash():
    await _setup()
    callback = fake_callback("buy:tariff:zz", fake_tg_user(2003))

    await show_periods(callback)

    callback.message.answer.assert_awaited_once()


async def test_show_providers_computes_price_without_discount():
    await _setup()
    callback = fake_callback("buy:period:nl:1", fake_tg_user(2004))

    await show_providers(callback)

    text_sent = callback.message.answer.call_args[0][0]
    expected = calc_price("nl", 1, 0)
    assert str(expected) in text_sent


async def test_show_providers_applies_personal_discount():
    await _setup()
    async with session_scope() as session:
        from app.services import users as user_service

        user, _ = await user_service.get_or_create(session, telegram_id=2005, first_name="Промо")
        await billing.create_personal_offer(session, user, discount_percent=30, valid_hours=24)

    callback = fake_callback("buy:period:nl:1", fake_tg_user(2005))
    await show_providers(callback)

    text_sent = callback.message.answer.call_args[0][0]
    discounted = calc_price("nl", 1, 30)
    full = calc_price("nl", 1, 0)
    assert str(discounted) in text_sent
    assert discounted < full


async def test_create_payment_stars_sends_invoice_not_link():
    await _setup()
    callback = fake_callback("buy:pay:stars:nl:1", fake_tg_user(2006))

    await create_payment(callback)

    callback.message.answer_invoice.assert_awaited_once()
    callback.message.answer.assert_not_awaited()


async def test_create_payment_records_order_in_db():
    await _setup()
    callback = fake_callback("buy:pay:stars:multi:3", fake_tg_user(2007))

    await create_payment(callback)

    async with session_scope() as session:
        from sqlalchemy import select

        from app.models import Payment, User

        user = (await session.execute(select(User).where(User.telegram_id == 2007))).scalar_one()
        payment = (
            await session.execute(select(Payment).where(Payment.user_id == user.id))
        ).scalar_one()
        assert payment.tariff_code == "multi"
        assert payment.period_months == 3


async def test_create_payment_provider_failure_notifies_user_not_crash(monkeypatch):
    await _setup()

    async def boom(*args, **kwargs):
        raise RuntimeError("шлюз недоступен")

    from app.payments.stars import StarsProvider

    monkeypatch.setattr(StarsProvider, "create_invoice", boom)

    callback = fake_callback("buy:pay:stars:nl:1", fake_tg_user(2008))
    await create_payment(callback)

    callback.message.answer.assert_awaited_once()
    text_sent = callback.message.answer.call_args[0][0]
    assert "не прошла" in text_sent.lower() or "не удал" in text_sent.lower()
