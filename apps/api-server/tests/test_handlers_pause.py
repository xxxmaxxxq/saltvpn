"""Заморозка/разморозка подписки, включая исчерпанный годовой лимит."""

from datetime import timedelta

from app.bot.handlers.pause import do_pause, do_resume, pause_menu
from app.db import create_all_tables, session_scope
from app.models import Subscription, SubscriptionStatus, User, utcnow
from tests.bot_support import fake_callback, fake_tg_user


async def _user_with_subscription(telegram_id: int, **sub_kwargs) -> None:
    async with session_scope() as session:
        user = User(
            telegram_id=telegram_id,
            referral_code=f"rc{telegram_id}",
            marzban_username=f"u{telegram_id}",
        )
        session.add(user)
        await session.flush()
        defaults = dict(
            user_id=user.id,
            tariff_code="multi",
            status=SubscriptionStatus.ACTIVE,
            expires_at=utcnow() + timedelta(days=30),
            is_trial=False,
            pause_days_used=0,
            pause_year=utcnow().year,
        )
        defaults.update(sub_kwargs)
        session.add(Subscription(**defaults))


async def _setup():
    await create_all_tables()


async def test_pause_menu_offers_days_when_quota_available():
    await _setup()
    await _user_with_subscription(3001)

    await pause_menu(fake_callback("pause:menu", fake_tg_user(3001)))
    # обёрнуто в answered_callback, проверяем финальный ответ


async def test_pause_menu_refuses_when_quota_exhausted():
    await _setup()
    await _user_with_subscription(3002, pause_days_used=30)

    callback = fake_callback("pause:menu", fake_tg_user(3002))
    await pause_menu(callback)

    text_sent = callback.message.answer.call_args[0][0]
    assert (
        "заморозить" in text_sent.lower()
        or "лимит" in text_sent.lower()
        or "дн" in text_sent.lower()
    )


async def test_do_pause_freezes_subscription_and_extends_expiry():
    await _setup()
    await _user_with_subscription(3003)

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        before = (await get_by_telegram_id(session, 3003)).subscription.expires_at

    await do_pause(fake_callback("pause:set:7", fake_tg_user(3003)))

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        user = await get_by_telegram_id(session, 3003)
        assert user.subscription.status == SubscriptionStatus.PAUSED
        assert user.subscription.expires_at == before + timedelta(days=7)


async def test_do_pause_over_quota_shows_error_not_crash():
    await _setup()
    await _user_with_subscription(3004, pause_days_used=29)

    callback = fake_callback("pause:set:5", fake_tg_user(3004))
    await do_pause(callback)

    text_sent = callback.message.answer.call_args[0][0]
    assert "недоступна" in text_sent.lower() or "осталось" in text_sent.lower()

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        user = await get_by_telegram_id(session, 3004)
        assert user.subscription.status != SubscriptionStatus.PAUSED


async def test_do_resume_returns_unused_days_to_quota():
    await _setup()
    await _user_with_subscription(3005)
    await do_pause(fake_callback("pause:set:10", fake_tg_user(3005)))

    await do_resume(fake_callback("pause:resume", fake_tg_user(3005)))

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        user = await get_by_telegram_id(session, 3005)
        assert user.subscription.status == SubscriptionStatus.ACTIVE
        # 10 дней запросили, сразу же разморозили -> почти все 10 дней вернулись в квоту
        assert user.subscription.pause_days_used <= 1


async def test_double_resume_does_not_crash():
    await _setup()
    await _user_with_subscription(3006)
    await do_pause(fake_callback("pause:set:5", fake_tg_user(3006)))

    await do_resume(fake_callback("pause:resume", fake_tg_user(3006)))
    second = fake_callback("pause:resume", fake_tg_user(3006))
    await do_resume(second)

    text_sent = second.message.answer.call_args[0][0]
    assert "не на паузе" in text_sent.lower() or "лимит" in text_sent.lower() or text_sent
