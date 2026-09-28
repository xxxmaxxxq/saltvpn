"""/connect: получение ссылки-подписки и поведение без подписки."""

from app.bot.handlers.connect import show_connect, show_link
from app.db import create_all_tables, session_scope
from app.services import users as user_service
from tests.bot_support import fake_callback, fake_tg_user, patch_marzban_success, seed_nodes


async def _setup():
    await create_all_tables()


async def test_show_connect_without_subscription_offers_trial_price():
    await _setup()
    callback = fake_callback("connect:show", fake_tg_user(5001))

    await show_connect(callback)

    text_sent = callback.message.answer.call_args[0][0]
    assert "149" in text_sent or "триал" in text_sent.lower() or "бесплат" in text_sent.lower()


async def test_show_connect_with_active_subscription_returns_link(monkeypatch):
    await _setup()
    patch_marzban_success(monkeypatch, subscription_path="/sub/abc123")

    async with session_scope() as session:
        await seed_nodes(session)
        user, _ = await user_service.get_or_create(session, telegram_id=5002, first_name="Т")
        from datetime import timedelta

        from app.models import Subscription, SubscriptionStatus, utcnow
        from app.services import subscriptions as sub_service

        subscription = Subscription(
            user_id=user.id,
            tariff_code="nl",
            status=SubscriptionStatus.ACTIVE,
            expires_at=utcnow() + timedelta(days=10),
            is_trial=False,
        )
        session.add(subscription)
        await session.flush()
        await sub_service.sync_to_marzban(session, user, subscription)

    callback = fake_callback("connect:show", fake_tg_user(5002))
    await show_connect(callback)

    text_sent = callback.message.answer.call_args[0][0]
    assert "/sub/" in text_sent


async def test_show_link_without_subscription_sends_nothing_and_does_not_crash():
    await _setup()
    callback = fake_callback("connect:link", fake_tg_user(5003))

    await show_link(callback)
    # без подписки ссылки нет — хендлер просто ничего не отправляет,
    # но не должен падать
    callback.message.answer.assert_not_awaited()
