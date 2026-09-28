"""
Реферальные слоты: начисление за оплативших друзей и потолок.

tariffs.yml: qualified_per_slot=2, max_slots=3 — за каждые 2 оплативших
друга даётся +1 слот, но не больше трёх суммарно.
"""

from app.db import create_all_tables, session_scope
from app.models import Payment, PaymentStatus, User
from app.services import referrals as ref_service
from app.services import users as user_service


async def _setup():
    await create_all_tables()


async def _make_referred_and_pay(session, referrer: User, telegram_id: int) -> User:
    referred, _ = await user_service.get_or_create(session, telegram_id=telegram_id)
    await ref_service.attach_referrer(session, referred, referrer.referral_code)
    session.add(
        Payment(
            user_id=referred.id,
            provider="stars",
            tariff_code="nl",
            period_months=1,
            amount=149,
            status=PaymentStatus.PAID,
        )
    )
    await session.flush()
    return referred


async def test_no_slot_before_reaching_threshold():
    await _setup()
    async with session_scope() as session:
        referrer, _ = await user_service.get_or_create(session, telegram_id=4001, first_name="Реф")
        referred = await _make_referred_and_pay(session, referrer, 4002)

        _, granted = await ref_service.on_first_payment(session, referred)
        assert granted is False
        assert referrer.device_slots_bonus == 0


async def test_slot_granted_after_two_paying_referrals():
    await _setup()
    async with session_scope() as session:
        referrer, _ = await user_service.get_or_create(session, telegram_id=4003, first_name="Реф")

        first = await _make_referred_and_pay(session, referrer, 4004)
        await ref_service.on_first_payment(session, first)

        second = await _make_referred_and_pay(session, referrer, 4005)
        _, granted = await ref_service.on_first_payment(session, second)

        assert granted is True
        assert referrer.device_slots_bonus == 1


async def test_slots_capped_at_max():
    await _setup()
    async with session_scope() as session:
        referrer, _ = await user_service.get_or_create(session, telegram_id=4006, first_name="Реф")

        # 8 оплативших друзей -> по формуле 4 слота, но потолок 3
        for i in range(8):
            referred = await _make_referred_and_pay(session, referrer, 4100 + i)
            await ref_service.on_first_payment(session, referred)

        assert referrer.device_slots_bonus == 3


async def test_same_referral_cannot_grant_slot_twice():
    """Повторный вызов on_first_payment для уже засчитанного друга не даёт слот второй раз."""
    await _setup()
    async with session_scope() as session:
        referrer, _ = await user_service.get_or_create(session, telegram_id=4007, first_name="Реф")
        a = await _make_referred_and_pay(session, referrer, 4008)
        b = await _make_referred_and_pay(session, referrer, 4009)

        await ref_service.on_first_payment(session, a)
        await ref_service.on_first_payment(session, b)
        assert referrer.device_slots_bonus == 1

        # Повторный вызов для того же "b" (например, дублирующийся вебхук)
        _, granted_again = await ref_service.on_first_payment(session, b)
        assert granted_again is False
        assert referrer.device_slots_bonus == 1


async def test_self_referral_is_rejected():
    await _setup()
    async with session_scope() as session:
        user, _ = await user_service.get_or_create(session, telegram_id=4010, first_name="Соло")
        attached = await ref_service.attach_referrer(session, user, user.referral_code)
        assert attached is False
        assert user.referred_by_id is None


async def test_referrer_cannot_be_changed_once_set():
    await _setup()
    async with session_scope() as session:
        first_referrer, _ = await user_service.get_or_create(
            session, telegram_id=4011, first_name="A"
        )
        second_referrer, _ = await user_service.get_or_create(
            session, telegram_id=4012, first_name="B"
        )
        newcomer, _ = await user_service.get_or_create(session, telegram_id=4013)

        await ref_service.attach_referrer(session, newcomer, first_referrer.referral_code)
        second_attach = await ref_service.attach_referrer(
            session, newcomer, second_referrer.referral_code
        )

        assert second_attach is False
        assert newcomer.referred_by_id == first_referrer.id
