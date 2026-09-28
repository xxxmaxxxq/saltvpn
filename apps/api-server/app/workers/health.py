"""
Воркер Auto-Healing.

Каждые HEALTH_CHECK_INTERVAL_SEC (по умолчанию 120 с) проверяет все ноды.
При N провалах подряд запускает план восстановления, а затем сообщает
пользователям, что доступ уже восстановлен — раньше, чем они успеют написать
в поддержку.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Node, NodeStatus
from app.services import healing
from app.services import subscriptions as sub_service

log = logging.getLogger(__name__)
settings = get_settings()


async def run_once(bot: Bot) -> dict[str, int]:
    """Один проход проверки всех нод."""
    from app.bot.notifications import notify_admins, send_healed_notice

    checked = healed = failed = 0

    async with session_scope() as session:
        nodes = list(
            (
                await session.execute(
                    select(Node).where(Node.status != NodeStatus.DISABLED).order_by(Node.code)
                )
            ).scalars()
        )

        for node in nodes:
            checked += 1
            healthy = await healing.check_node(session, node)
            if healthy or node.fail_count < settings.health_fail_threshold:
                continue

            log.warning("Запускаю восстановление ноды %s", node.code)
            report = await healing.heal_node(session, node)

            if report.get("success"):
                healed += 1
                users = await healing.affected_users(session, node)
                for user in users:
                    await send_healed_notice(
                        bot, user.telegram_id, sub_service.subscription_url(user)
                    )
                await notify_admins(
                    bot,
                    f"⚡ <b>Auto-Healing</b>\n\nНода <b>{node.code}</b> восстановлена "
                    f"шагом <code>{report['action']}</code>.\n"
                    f"Уведомлено пользователей: {len(users)}.",
                )
            else:
                failed += 1
                await notify_admins(
                    bot,
                    f"🔴 <b>Нода {node.code} не восстановлена</b>\n\n"
                    f"Причина: <code>{report.get('reason')}</code>\n\n"
                    f"Что делать: закажите у хостера дополнительный IP и выполните\n"
                    f"<code>python -m app.cli set-node-host "
                    f"--code {node.code} --host НОВЫЙ_IP</code>",
                )

    if healed or failed:
        log.info("Auto-Healing: проверено %s, вылечено %s, провал %s", checked, healed, failed)
    return {"checked": checked, "healed": healed, "failed": failed}
