"""
Админ-команды: доступ только по ADMIN_IDS.

Фильтр висит на роутере (router.message.filter(...)), а не в теле хендлера —
поэтому отказ проверяется через реальный Dispatcher.feed_update, а не прямым
вызовом функции: прямой вызов обошёл бы фильтр и ничего бы не доказал.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Update
from aiogram.types import Message as TgMessage
from aiogram.types import User as TgUser

from app.bot.handlers import router
from app.bot.handlers.admin import cmd_give
from app.config import get_settings
from app.db import create_all_tables, session_scope
from app.models import Subscription, SubscriptionStatus, User, utcnow
from tests.bot_support import fake_message, fake_tg_user


async def _setup():
    await create_all_tables()


def _real_message(user_id: int, text_: str) -> TgMessage:
    return TgMessage(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Т"),
        text=text_,
    )


async def test_non_admin_command_is_ignored_by_router():
    await _setup()
    settings = get_settings()
    non_admin_id = 999999999
    assert non_admin_id not in settings.admin_id_list, "тест требует id вне ADMIN_IDS из .env теста"

    bot = AsyncMock()
    bot.id = 1
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    message = _real_message(non_admin_id, "/stats").as_(bot)
    update = Update(update_id=1, message=message)

    await dispatcher.feed_update(bot, update)

    # Ни один хендлер admin-роутера не должен был вызвать send_message/answer:
    # апдейт либо не обработан никем, либо ушёл в другой роутер, но не в /stats
    bot.send_message.assert_not_awaited()


async def test_give_grants_days_to_existing_active_subscription():
    await _setup()
    from aiogram.filters import CommandObject

    async with session_scope() as session:
        user = User(telegram_id=6002, referral_code="rc6002", marzban_username="u6002")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                tariff_code="multi",
                status=SubscriptionStatus.ACTIVE,
                expires_at=utcnow() + timedelta(days=5),
                is_trial=False,
            )
        )

    message = fake_message(fake_tg_user(6002))
    message.bot = AsyncMock()
    await cmd_give(message, CommandObject(prefix="/", command="give", args="6002 30"))

    async with session_scope() as session:
        from app.services.users import get_by_telegram_id

        user = await get_by_telegram_id(session, 6002)
        # было +5 дней, добавили 30 -> около 35 дней вперёд от "сейчас"
        assert 33 <= user.subscription.days_left <= 35


async def test_give_rejects_non_numeric_args():
    await _setup()
    from aiogram.filters import CommandObject

    message = fake_message(fake_tg_user(6003))
    await cmd_give(message, CommandObject(prefix="/", command="give", args="abc 30"))

    text_sent = message.answer.call_args[0][0]
    assert "числ" in text_sent.lower()


async def test_give_unknown_user_reports_not_found():
    await _setup()
    from aiogram.filters import CommandObject

    message = fake_message(fake_tg_user(6004))
    await cmd_give(message, CommandObject(prefix="/", command="give", args="424242424 10"))

    text_sent = message.answer.call_args[0][0]
    assert "не найден" in text_sent.lower()
