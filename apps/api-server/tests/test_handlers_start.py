"""
/start и выдача триала.

Раньше этот стык (хендлер + первое обращение к user.subscription на
только что созданном объекте) падал MissingGreenlet молча — сообщение
об ошибке не долетало до пользователя, бот просто не отвечал. Тесты
идут через реальную SQLite, а не через мок сессии: именно на реальной
сессии эта ошибка и проявляется.
"""

from datetime import timedelta

from aiogram.filters import CommandObject

from app.bot.handlers.start import activate_trial, cmd_start
from app.db import create_all_tables, session_scope
from app.models import Subscription, SubscriptionStatus, User, utcnow
from tests.bot_support import fake_callback, fake_message, fake_tg_user, patch_marzban_success


async def _setup():
    await create_all_tables()


async def test_start_new_user_gets_welcome_with_trial_button():
    await _setup()
    user = fake_tg_user(telegram_id=1001)
    message = fake_message(user)

    await cmd_start(message, CommandObject(prefix="/", command="start", args=None))

    message.answer.assert_awaited_once()
    body, kwargs = message.answer.call_args
    assert "SaltVPN" in body[0] or "привет" in body[0].lower()
    assert kwargs["reply_markup"] is not None

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        db_user = await get_by_telegram_id(session, 1001)
        assert db_user is not None


async def test_start_referral_link_attaches_referrer():
    await _setup()
    async with session_scope() as session:
        from app.services import users as user_service

        referrer, _ = await user_service.get_or_create(session, telegram_id=1002, first_name="Реф")
        ref_code = referrer.referral_code

    newcomer = fake_tg_user(telegram_id=1003)
    message = fake_message(newcomer)
    await cmd_start(message, CommandObject(prefix="/", command="start", args=f"ref_{ref_code}"))

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        db_user = await get_by_telegram_id(session, 1003)
        assert db_user.referred_by_id == referrer.id


async def test_start_active_subscription_shows_days_left():
    await _setup()
    async with session_scope() as session:
        user = User(telegram_id=1004, referral_code="rc1004", marzban_username="u1004")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                tariff_code="multi",
                status=SubscriptionStatus.ACTIVE,
                expires_at=utcnow() + timedelta(days=10),
                is_trial=False,
            )
        )

    message = fake_message(fake_tg_user(telegram_id=1004))
    await cmd_start(message, CommandObject(prefix="/", command="start", args=None))

    text_sent = message.answer.call_args[0][0]
    assert "Подписка активна" in text_sent or "10 дн" in text_sent


async def test_start_paused_subscription_reminds_days_are_safe():
    await _setup()
    async with session_scope() as session:
        user = User(telegram_id=1005, referral_code="rc1005", marzban_username="u1005")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                tariff_code="multi",
                status=SubscriptionStatus.PAUSED,
                expires_at=utcnow() + timedelta(days=5),
                paused_until=utcnow() + timedelta(days=7),
                is_trial=False,
            )
        )

    message = fake_message(fake_tg_user(telegram_id=1005))
    await cmd_start(message, CommandObject(prefix="/", command="start", args=None))

    text_sent = message.answer.call_args[0][0]
    assert "заморожен" in text_sent.lower()


async def test_start_expired_subscription_invites_back():
    await _setup()
    async with session_scope() as session:
        user = User(
            telegram_id=1006, referral_code="rc1006", marzban_username="u1006", trial_used=True
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                tariff_code="multi",
                status=SubscriptionStatus.EXPIRED,
                expires_at=utcnow() - timedelta(days=2),
                is_trial=False,
            )
        )

    message = fake_message(fake_tg_user(telegram_id=1006))
    await cmd_start(message, CommandObject(prefix="/", command="start", args=None))

    text_sent = message.answer.call_args[0][0]
    assert "законч" in text_sent.lower()


async def test_trial_activation_grants_subscription(monkeypatch):
    await _setup()
    patch_marzban_success(monkeypatch)
    user = fake_tg_user(telegram_id=1007)
    callback = fake_callback("trial:activate", user)

    await activate_trial(callback)

    callback.answer.assert_awaited_once()
    callback.message.answer.assert_awaited_once()
    text_sent = callback.message.answer.call_args[0][0]
    assert "дн" in text_sent

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        db_user = await get_by_telegram_id(session, 1007)
        assert db_user.trial_used is True


async def test_trial_cannot_be_taken_twice(monkeypatch):
    """Повторная попытка не должна создавать вторую подписку."""
    await _setup()
    patch_marzban_success(monkeypatch)
    user = fake_tg_user(telegram_id=1008)

    await activate_trial(fake_callback("trial:activate", user))
    second_callback = fake_callback("trial:activate", user)
    await activate_trial(second_callback)

    text_sent = second_callback.message.answer.call_args[0][0]
    assert "уже" in text_sent.lower() or "исполь" in text_sent.lower()

    async with session_scope() as session:
        from sqlalchemy import func, select

        count = (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.user_id.in_(select(User.id).where(User.telegram_id == 1008))
                )
            )
        ).scalar()
        assert count == 1
