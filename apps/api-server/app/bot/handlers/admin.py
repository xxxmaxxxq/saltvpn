"""
Админские команды: статистика, ручная выдача дней, состояние нод, рассылка.

Доступ только для ADMIN_IDS из .env — фильтр применяется ко всему роутеру.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import (
    HealthEvent,
    Node,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
    utcnow,
)
from app.services import healing
from app.services import subscriptions as sub_service
from app.services import users as user_service

log = logging.getLogger(__name__)
settings = get_settings()

router = Router(name="admin")
# Весь роутер доступен только администраторам
router.message.filter(F.from_user.id.in_(settings.admin_id_list))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Сводка по сервису: люди, деньги, конверсия."""
    async with session_scope() as session:
        total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0

        active = (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]),
                    Subscription.expires_at > utcnow(),
                )
            )
        ).scalar() or 0

        paid_active = (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.expires_at > utcnow(),
                    Subscription.is_trial.is_(False),
                )
            )
        ).scalar() or 0

        trials_taken = (
            await session.execute(select(func.count(User.id)).where(User.trial_used.is_(True)))
        ).scalar() or 0

        paying_users = (
            await session.execute(
                select(func.count(func.distinct(Payment.user_id))).where(
                    Payment.status == PaymentStatus.PAID
                )
            )
        ).scalar() or 0

        month_ago = utcnow() - timedelta(days=30)
        revenue_30d = (
            await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == PaymentStatus.PAID, Payment.paid_at >= month_ago
                )
            )
        ).scalar() or 0

        revenue_total = (
            await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == PaymentStatus.PAID
                )
            )
        ).scalar() or 0

        new_24h = (
            await session.execute(
                select(func.count(User.id)).where(User.created_at >= utcnow() - timedelta(days=1))
            )
        ).scalar() or 0

    conversion = (paying_users / trials_taken * 100) if trials_taken else 0

    await message.answer(
        "📊 <b>Статистика сервиса</b>\n\n"
        f"👥 Пользователей всего: <b>{total_users}</b> (+{new_24h} за сутки)\n"
        f"✅ Активных подписок: <b>{active}</b> (платных: {paid_active})\n"
        f"🎁 Триалов выдано: <b>{trials_taken}</b>\n"
        f"💳 Платящих: <b>{paying_users}</b>\n"
        f"📈 Конверсия триал → оплата: <b>{conversion:.1f}%</b>\n\n"
        f"💰 Выручка за 30 дней: <b>{revenue_30d:.0f} ₽</b>\n"
        f"💰 Выручка всего: <b>{revenue_total:.0f} ₽</b>"
    )


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject) -> None:
    """Карточка пользователя: /user <telegram_id>"""
    if not command.args:
        await message.answer("Использование: <code>/user 123456789</code>")
        return

    try:
        telegram_id = int(command.args.strip())
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, telegram_id)
        if user is None:
            await message.answer("Пользователь не найден")
            return

        payments_count = (
            await session.execute(
                select(func.count(Payment.id)).where(
                    Payment.user_id == user.id, Payment.status == PaymentStatus.PAID
                )
            )
        ).scalar() or 0
        spent = (
            await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.user_id == user.id, Payment.status == PaymentStatus.PAID
                )
            )
        ).scalar() or 0

        sub = user.subscription
        sub_info = (
            f"{sub.tariff_code} · {sub.status} · до {sub.expires_at.strftime('%d.%m.%Y')}"
            if sub
            else "нет"
        )

    await message.answer(
        f"👤 <b>tg={telegram_id}</b> @{user.username or '—'}\n"
        f"Подписка: {sub_info}\n"
        f"Триал использован: {'да' if user.trial_used else 'нет'}\n"
        f"Доп. слотов за рефералов: {user.device_slots_bonus}\n"
        f"Оплат: {payments_count} на {spent:.0f} ₽\n"
        f"Аккаунт в панели: <code>{user.marzban_username}</code>"
    )


@router.message(Command("give"))
async def cmd_give(message: Message, command: CommandObject) -> None:
    """Выдать дни вручную: /give <telegram_id> <дней>"""
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/give 123456789 30</code>")
        return

    try:
        telegram_id, days = int(parts[0]), int(parts[1])
    except ValueError:
        await message.answer("Оба параметра должны быть числами")
        return

    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, telegram_id)
        if user is None:
            await message.answer("Пользователь не найден")
            return

        subscription = user.subscription
        if subscription is None:
            from app.models import Subscription as Sub

            subscription = Sub(
                user_id=user.id,
                tariff_code="multi",
                status=SubscriptionStatus.ACTIVE,
                expires_at=utcnow() + timedelta(days=days),
                is_trial=False,
            )
            session.add(subscription)
            await session.flush()
        else:
            base = subscription.expires_at if subscription.expires_at > utcnow() else utcnow()
            subscription.expires_at = base + timedelta(days=days)
            subscription.status = SubscriptionStatus.ACTIVE

        await sub_service.sync_to_marzban(session, user, subscription)
        new_date = subscription.expires_at.strftime("%d.%m.%Y")

    await message.answer(f"✅ Выдано {days} дн. Подписка до {new_date}")
    try:
        await message.bot.send_message(
            telegram_id, f"🎁 Вам начислено <b>{days} дн.</b> подписки. Действует до {new_date}."
        )
    except Exception as err:  # пользователь мог заблокировать бота
        log.warning("Не удалось уведомить tg=%s: %s", telegram_id, err)


def _fmt_traffic(used_bytes: int) -> str:
    gb = used_bytes / 1024**3
    return f"{gb:.1f} ГБ" if gb >= 0.1 else f"{used_bytes / 1024**2:.0f} МБ"


def _tg_id_from_marzban(username: str) -> int | None:
    """Имя аккаунта в панели — это u<telegram_id>."""
    return int(username[1:]) if username.startswith("u") and username[1:].isdigit() else None


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    """
    Список подписчиков: тариф, остаток дней, трафик, активность.

    IP-адресов в списке нет намеренно: логи подключений не ведутся,
    панель хранит только факт активности и объём трафика.
    """
    from app.marzban import MarzbanClient, MarzbanError

    # Активность берём из панели, всё остальное — из своей базы
    activity: dict[str, dict] = {}
    try:
        async with MarzbanClient() as mz:
            for account in await mz.list_users():
                activity[account.get("username", "")] = account
    except MarzbanError as err:
        log.warning("Панель недоступна для /users: %s", err)

    async with session_scope() as session:
        result = await session.execute(
            select(User, Subscription)
            .join(Subscription, Subscription.user_id == User.id)
            .order_by(Subscription.expires_at.desc())
            .limit(30)
        )
        rows = result.all()

        if not rows:
            await message.answer("Подписчиков пока нет")
            return

        icons = {"active": "✅", "trial": "🎁", "paused": "⏸", "expired": "❌"}
        lines = [f"👥 <b>Подписчики</b> (показаны {len(rows)})\n"]

        for user, subscription in rows:
            account = activity.get(user.marzban_username or "", {})
            online_at = account.get("online_at")
            traffic = _fmt_traffic(int(account.get("used_traffic", 0) or 0))

            if online_at:
                seen = datetime.fromisoformat(online_at.replace("Z", "+00:00"))
                minutes = int((utcnow() - seen).total_seconds() // 60)
                activity_text = (
                    "🟢 сейчас"
                    if minutes < 5
                    else f"был {minutes // 60}ч назад"
                    if minutes >= 60
                    else f"был {minutes} мин назад"
                )
            else:
                activity_text = "не подключался"

            handle = f"@{user.username}" if user.username else f"tg={user.telegram_id}"
            lines.append(
                f"{icons.get(subscription.status, '·')} <b>{handle}</b> · "
                f"{subscription.tariff_code} · {subscription.days_left} дн.\n"
                f"    {traffic} · {activity_text} · <code>{user.telegram_id}</code>"
            )

    await message.answer("\n".join(lines))


@router.message(Command("online"))
async def cmd_online(message: Message) -> None:
    """Кто пользуется VPN прямо сейчас (активность за последние 5 минут)."""
    from app.marzban import MarzbanClient, MarzbanError

    try:
        async with MarzbanClient() as mz:
            accounts = await mz.list_users()
    except MarzbanError as err:
        await message.answer(f"Панель недоступна: {err}")
        return

    now = utcnow()
    online = []
    for account in accounts:
        online_at = account.get("online_at")
        if not online_at:
            continue
        seen = datetime.fromisoformat(online_at.replace("Z", "+00:00"))
        if (now - seen).total_seconds() <= 300:
            online.append((account.get("username", ""), account))

    if not online:
        await message.answer("🌙 Сейчас никто не подключён")
        return

    lines = [f"🟢 <b>Онлайн: {len(online)}</b>\n"]
    async with session_scope() as session:
        for username, account in online:
            telegram_id = _tg_id_from_marzban(username)
            user = (
                await user_service.get_by_telegram_id(session, telegram_id) if telegram_id else None
            )
            handle = f"@{user.username}" if user and user.username else username
            app_name = (account.get("sub_last_user_agent") or "—").split("/")[0][:20]
            lines.append(
                f"· <b>{handle}</b> · {_fmt_traffic(int(account.get('used_traffic', 0) or 0))}"
                f" · {app_name}"
            )

    await message.answer("\n".join(lines))


@router.message(Command("nodes"))
async def cmd_nodes(message: Message) -> None:
    """Состояние нод и последние инциденты."""
    async with session_scope() as session:
        nodes = list((await session.execute(select(Node).order_by(Node.code))).scalars())
        events = list(
            (
                await session.execute(
                    select(HealthEvent).order_by(HealthEvent.created_at.desc()).limit(5)
                )
            ).scalars()
        )

    if not nodes:
        await message.answer(
            "Ноды не добавлены. Добавить:\n"
            "<code>python -m app.cli add-node --code nl-1 --location nl --host IP</code>"
        )
        return

    icons = {"healthy": "🟢", "degraded": "🟡", "disabled": "🔴"}
    lines = ["🖥 <b>Состояние нод</b>\n"]
    for node in nodes:
        latency = f"{node.last_latency_ms} мс" if node.last_latency_ms else "—"
        lines.append(
            f"{icons.get(node.status, '⚪')} <b>{node.code}</b> ({node.location}) "
            f"{node.host}:{node.port}\n"
            f"    задержка {latency} · сбоев подряд: {node.fail_count} · "
            f"SNI: {node.current_sni or '—'}"
        )

    if events:
        lines.append("\n<b>Последние события:</b>")
        for event in events:
            lines.append(
                f"· {event.created_at.strftime('%d.%m %H:%M')} — {event.event}"
                f"{f' ({event.action})' if event.action else ''}"
            )

    await message.answer("\n".join(lines))


@router.message(Command("heal"))
async def cmd_heal(message: Message, command: CommandObject) -> None:
    """Ручной запуск восстановления ноды: /heal nl-1"""
    code = (command.args or "").strip()
    if not code:
        await message.answer("Использование: <code>/heal nl-1</code>")
        return

    async with session_scope() as session:
        node = (await session.execute(select(Node).where(Node.code == code))).scalar_one_or_none()
        if node is None:
            await message.answer("Нода не найдена")
            return
        result = await healing.heal_node(session, node)

    if result.get("success"):
        await message.answer(f"✅ Нода {code} восстановлена шагом <b>{result['action']}</b>")
    else:
        await message.answer(f"❌ Восстановить не удалось: {result.get('reason')}")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    """
    Рассылка всем активным: /broadcast текст сообщения

    Отправка идёт с троттлингом 20 сообщений в секунду — Telegram
    блокирует более быстрые рассылки.
    """
    body = (command.args or "").strip()
    if not body:
        await message.answer("Использование: <code>/broadcast Текст сообщения</code>")
        return

    async with session_scope() as session:
        rows = (
            await session.execute(select(User.telegram_id).where(User.is_blocked.is_(False)))
        ).scalars()
        recipients = list(rows)

    await message.answer(f"📢 Начинаю рассылку на {len(recipients)} чел…")
    sent = failed = 0
    for index, telegram_id in enumerate(recipients, start=1):
        try:
            await message.bot.send_message(telegram_id, body)
            sent += 1
        except Exception:
            failed += 1
        if index % 20 == 0:
            await asyncio.sleep(1)

    await message.answer(f"✅ Рассылка завершена: доставлено {sent}, ошибок {failed}")
