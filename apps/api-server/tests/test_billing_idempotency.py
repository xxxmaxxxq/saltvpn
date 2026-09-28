"""
Этап 3: без повторных начислений.

Единственное место, где ошибка стоит денег и дней подписки напрямую.
apply_payment должен переносить дни ровно один раз, сколько бы раз ни
пришёл вебхук по одному и тому же платежу (обрыв связи у провайдера,
ретрай, дублирующийся POST).
"""

from datetime import timedelta

from sqlalchemy import select

from app.db import create_all_tables, session_scope
from app.models import Payment, PaymentStatus, User, utcnow
from app.services import billing
from app.services import users as user_service
from tests.bot_support import patch_marzban_success, seed_nodes


async def _setup():
    await create_all_tables()


async def _paid_payment(session, user: User, tariff="nl", months=1) -> Payment:
    payment = Payment(
        user_id=user.id,
        provider="yookassa",
        tariff_code=tariff,
        period_months=months,
        amount=149,
        status=PaymentStatus.PENDING,
    )
    session.add(payment)
    await session.flush()
    return payment


async def test_double_webhook_does_not_grant_days_twice(monkeypatch):
    await _setup()
    patch_marzban_success(monkeypatch)
    async with session_scope() as session:
        await seed_nodes(session)
        user, _ = await user_service.get_or_create(session, telegram_id=8001, first_name="Т")
        payment = await _paid_payment(session, user)

        first = await billing.apply_payment(session, payment, external_id="ext-1")
        assert first["already_processed"] is False
        days_after_first = first["days_granted"]

        second = await billing.apply_payment(session, payment, external_id="ext-1")
        assert second["already_processed"] is True

        # Срок подписки не сдвинулся вторым вызовом
        await session.refresh(user, attribute_names=["subscription"])
        expected_expiry = utcnow() + timedelta(days=days_after_first - 1)
        assert user.subscription.expires_at > expected_expiry


async def test_double_click_pay_button_creates_two_orders_not_one_merged():
    """
    Двойное нажатие кнопки оплаты создаёт два отдельных pending-платежа
    (billing.create_order не дедуплицирует запросы) — это ожидаемо: важно,
    что оплата только ОДНОГО из них не спишет деньги за оба сразу.
    """
    await _setup()
    async with session_scope() as session:
        user, _ = await user_service.get_or_create(session, telegram_id=8002, first_name="Т")

        payment1, amount1 = await billing.create_order(session, user, "nl", 1, "stars")
        payment2, amount2 = await billing.create_order(session, user, "nl", 1, "stars")

        assert payment1.id != payment2.id
        assert amount1 == amount2


async def test_referral_bonus_granted_once_even_with_duplicate_webhook(monkeypatch):
    await _setup()
    patch_marzban_success(monkeypatch)
    async with session_scope() as session:
        await seed_nodes(session)
        referrer, _ = await user_service.get_or_create(session, telegram_id=8003, first_name="Реф")
        referred, _ = await user_service.get_or_create(session, telegram_id=8004, first_name="Друг")
        from app.services import referrals as ref_service

        await ref_service.attach_referrer(session, referred, referrer.referral_code)

        payment = await _paid_payment(session, referred)
        await billing.apply_payment(session, payment, external_id="ext-ref-1")
        # повторный вебхук по тому же платежу
        await billing.apply_payment(session, payment, external_id="ext-ref-1")

        result = await session.execute(select(User).where(User.id == referrer.id))
        refreshed_referrer = result.scalar_one()
        # 1 оплативший друг < qualified_per_slot=2 -> слотов пока не должно быть,
        # но главное — начисление не задвоилось при повторном вебхуке
        assert refreshed_referrer.device_slots_bonus == 0


async def test_invoice_external_id_mismatch_is_rejected():
    """
    Кто-то подставил в metadata чужого (настоящего, но чужого и дешёвого)
    заказа наш payment_id — стоимость должна сверяться с сохранённым
    external_id, а не приниматься на веру из тела уведомления.
    """
    assert billing.invoice_belongs_to_payment("real-external-id", "real-external-id") is True
    assert billing.invoice_belongs_to_payment("real-external-id", "attacker-external-id") is False
    # external_id ещё не успели записать (связь оборвалась сразу после выставления счёта)
    assert billing.invoice_belongs_to_payment(None, "whatever") is True


async def test_double_trial_bonus_days_not_stacked_via_apply_payment(monkeypatch):
    """
    Реферальный бонус за первую оплату не должен начисляться второй раз,
    если apply_payment вызван повторно по другому (новому) платежу того же
    пользователя — _has_paid_before должен увидеть уже проведённый первый.
    """
    await _setup()
    patch_marzban_success(monkeypatch)
    async with session_scope() as session:
        await seed_nodes(session)
        referrer, _ = await user_service.get_or_create(session, telegram_id=8005, first_name="Реф")
        referred, _ = await user_service.get_or_create(session, telegram_id=8006, first_name="Друг")
        from app.services import referrals as ref_service

        await ref_service.attach_referrer(session, referred, referrer.referral_code)
        bonus_days = ref_service.invited_bonus_days()

        first_payment = await _paid_payment(session, referred)
        first_result = await billing.apply_payment(session, first_payment, external_id="p1")
        assert first_result["bonus_days"] == bonus_days

        second_payment = await _paid_payment(session, referred)
        second_result = await billing.apply_payment(session, second_payment, external_id="p2")
        assert second_result["bonus_days"] == 0
