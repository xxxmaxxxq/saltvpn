"""«🆘 Не работает» — включая случай, когда панель Marzban недоступна."""

from datetime import timedelta

from app.bot.handlers.support import heal_me
from app.db import create_all_tables, session_scope
from app.models import Subscription, SubscriptionStatus, User, utcnow
from tests.bot_support import fake_callback, fake_tg_user, patch_marzban_unavailable, seed_nodes


async def _setup():
    await create_all_tables()


async def _user_with_active_subscription(telegram_id: int) -> None:
    async with session_scope() as session:
        await seed_nodes(session)
        user = User(
            telegram_id=telegram_id,
            referral_code=f"rc{telegram_id}",
            marzban_username=f"u{telegram_id}",
        )
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


async def test_heal_without_subscription_reports_generic_error():
    await _setup()
    callback = fake_callback("heal:start", fake_tg_user(7001))

    await heal_me(callback)

    status_msg = callback.message.answer.return_value
    status_msg.edit_text.assert_awaited()


async def test_heal_when_panel_unavailable_shows_failure_not_crash(monkeypatch):
    await _setup()
    patch_marzban_unavailable(monkeypatch)
    await _user_with_active_subscription(7002)

    callback = fake_callback("heal:start", fake_tg_user(7002))
    await heal_me(callback)

    status_msg = callback.message.answer.return_value
    status_msg.edit_text.assert_awaited_once()
    body = status_msg.edit_text.call_args[0][0]
    assert "не получилось" in body.lower() or "😔" in body
