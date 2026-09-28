"""
Общие помощники для тестов хендлеров бота.

aiogram-объекты (Message, CallbackQuery) — pydantic-модели, тяжёлые для
ручной сборки в каждом тесте. Здесь — минимальные фабрики с моками там,
где хендлеру нужен вызов метода (answer, edit_reply_markup), и патч
MarzbanClient на уровне публичных методов: подделывать HTTP не нужно,
сервисный код (sync_to_marzban) вызывает именно эти методы.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TgUser

from app.marzban import MarzbanClient
from app.models import Node, NodeStatus


def fake_tg_user(
    telegram_id: int, username: str | None = "tester", first_name: str = "Тест"
) -> TgUser:
    return TgUser(id=telegram_id, is_bot=False, first_name=first_name, username=username)


def _fake_sent_message() -> MagicMock:
    """Сообщение, «отправленное» через answer() — тоже умеет edit_text/edit_reply_markup."""
    sent = MagicMock(spec=Message)
    sent.edit_text = AsyncMock()
    sent.edit_reply_markup = AsyncMock()
    return sent


def fake_message(user: TgUser, text_: str = "/start") -> Message:
    msg = MagicMock(spec=Message)
    msg.from_user = user
    msg.chat = Chat(id=user.id, type="private")
    msg.text = text_
    msg.answer = AsyncMock(return_value=_fake_sent_message())
    msg.edit_text = AsyncMock()
    msg.edit_reply_markup = AsyncMock()
    msg.answer_invoice = AsyncMock()
    return msg


def fake_callback(data: str, user: TgUser, message: Message | None = None) -> CallbackQuery:
    cb = MagicMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = user
    cb.message = message if message is not None else fake_message(user)
    cb.answer = AsyncMock()
    return cb


async def seed_nodes(session) -> None:
    """
    Nl+ru ноды, здоровые — как на бою после подключения обеих локаций.

    БД в тестах — один файл SQLite на весь прогон (не пересоздаётся между
    файлами), поэтому вставка идемпотентна: код ноды уникален глобально,
    повторный вызов из другого теста не должен падать на UNIQUE constraint.
    """
    from sqlalchemy import select

    existing = {
        row[0]
        for row in (await session.execute(select(Node.code).where(Node.code.in_(["nl-1", "ru-1"]))))
    }
    to_add = []
    if "nl-1" not in existing:
        to_add.append(
            Node(
                code="nl-1",
                location="nl",
                host="<MASTER_HOST>",
                port=8443,
                status=NodeStatus.HEALTHY,
            )
        )
    if "ru-1" not in existing:
        to_add.append(
            Node(
                code="ru-1",
                location="ru",
                host="<RU_NODE_HOST>",
                port=2053,
                status=NodeStatus.HEALTHY,
            )
        )
    if to_add:
        session.add_all(to_add)
        await session.flush()


def patch_marzban_success(monkeypatch, subscription_path: str = "/sub/test-token") -> None:
    """Панель отвечает нормально: инбаунды есть, аккаунт создаётся с первого раза."""

    async def fake_list_inbounds(self):
        return {
            "vless": ["VLESS_REALITY_nl-1", "VLESS_REALITY_ru-1"],
            "hysteria2": [],
            "shadowsocks": [],
        }

    async def fake_get_user(self, username):
        return None

    async def fake_create_user(self, **kwargs):
        return {"username": kwargs.get("username"), "subscription_url": subscription_path}

    async def fake_modify_user(self, username, **kwargs):
        return {"username": username, "subscription_url": subscription_path}

    async def fake_get_subscription_url(self, username):
        return f"https://vpn.xxxmaxxxq.ru{subscription_path}"

    monkeypatch.setattr(MarzbanClient, "list_inbounds", fake_list_inbounds)
    monkeypatch.setattr(MarzbanClient, "get_user", fake_get_user)
    monkeypatch.setattr(MarzbanClient, "create_user", fake_create_user)
    monkeypatch.setattr(MarzbanClient, "modify_user", fake_modify_user)
    monkeypatch.setattr(MarzbanClient, "get_subscription_url", fake_get_subscription_url)


def patch_marzban_unavailable(monkeypatch) -> None:
    """Панель лежит: любой метод роняет MarzbanError, как при реальном сбое."""
    from app.marzban import MarzbanError

    async def boom(self, *args, **kwargs):
        raise MarzbanError("панель недоступна (тест)")

    monkeypatch.setattr(MarzbanClient, "list_inbounds", boom)
    monkeypatch.setattr(MarzbanClient, "set_inbounds", boom)
    monkeypatch.setattr(MarzbanClient, "get_user", boom)
