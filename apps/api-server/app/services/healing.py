"""
Auto-Healing — автоматическое восстановление доступа при блокировке ноды.

Зачем: главная причина оттока в VPN-сервисах — «вчера работало, сегодня нет».
У конкурентов это лечится перепиской с поддержкой (часы), у нас — автоматически
за минуты, причём ключ у клиента не меняется.

Как работает:
  1. Воркер проверяет каждую ноду: TCP-хендшейк на боевой порт + статус в Marzban.
  2. N провалов подряд -> нода помечается degraded.
  3. Выполняется план восстановления (по шагам, до первого успеха):
       sni_switch       — сменить маскировочный домен Reality
       port_switch      — переехать на резервный порт
       hysteria_promote — сделать Hysteria2 основным для локации
       node_failover    — увести пользователей на запасную ноду локации
  4. Пользователям уходит уведомление, админу — отчёт.

Пользователь ничего не перенастраивает: ссылка-подписка та же,
приложение подтягивает новые конфиги при обновлении.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.marzban import MarzbanClient, MarzbanError, build_inbounds
from app.models import HealthEvent, Node, NodeStatus, Subscription, User, utcnow

log = logging.getLogger(__name__)
settings = get_settings()

HEALING_STEPS = ["sni_switch", "port_switch", "hysteria_promote", "node_failover"]


# ── Проверка доступности ───────────────────────────────────────────────────


async def tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, int | None]:  # noqa: ASYNC109
    """
    Проверить, отвечает ли нода на TCP-соединение.

    Возвращает (доступна, задержка_мс). Именно так «видит» ноду
    заблокированный провайдером пользователь: если TCP не устанавливается —
    для клиента сервис не работает, чем бы это ни было вызвано.
    """
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True, int((time.monotonic() - started) * 1000)
    except (TimeoutError, OSError):
        return False, None


async def check_node(session: AsyncSession, node: Node) -> bool:
    """
    Проверить одну ноду и обновить её состояние.

    Возвращает True, если нода здорова.
    """
    reachable, latency = await tcp_probe(node.host, node.port, settings.health_timeout_sec)

    marzban_ok = True
    if node.marzban_node_id:
        try:
            async with MarzbanClient() as mz:
                marzban_ok = await mz.node_is_connected(node.marzban_node_id)
        except MarzbanError:
            marzban_ok = False

    healthy = reachable and marzban_ok
    node.last_check_at = utcnow()
    node.last_latency_ms = latency

    if healthy:
        if node.fail_count or node.status == NodeStatus.DEGRADED:
            log.info("Нода %s снова здорова", node.code)
        node.fail_count = 0
        if node.status == NodeStatus.DEGRADED:
            node.status = NodeStatus.HEALTHY
            session.add(HealthEvent(node_id=node.id, event="healed"))
    else:
        node.fail_count += 1
        log.warning(
            "Нода %s недоступна (%s подряд): tcp=%s marzban=%s",
            node.code,
            node.fail_count,
            reachable,
            marzban_ok,
        )

    await session.flush()
    return healthy


# ── План восстановления ────────────────────────────────────────────────────


async def heal_node(session: AsyncSession, node: Node) -> dict[str, Any]:
    """
    Выполнить план восстановления для проблемной ноды.

    Возвращает отчёт: какой шаг сработал и что именно изменилось.
    """
    if node.status != NodeStatus.DEGRADED:
        node.status = NodeStatus.DEGRADED
        session.add(HealthEvent(node_id=node.id, event="degraded"))
        await session.flush()

    if not settings.healing_enabled:
        log.warning("Auto-Healing выключен (HEALING_ENABLED=false) — только уведомление")
        return {"success": False, "action": None, "reason": "healing_disabled"}

    for step in HEALING_STEPS:
        handler = {
            "sni_switch": _step_sni_switch,
            "port_switch": _step_port_switch,
            "hysteria_promote": _step_hysteria_promote,
            "node_failover": _step_node_failover,
        }[step]

        try:
            result = await handler(session, node)
        except Exception as err:  # шаг не должен ронять весь план
            log.exception("Шаг %s для ноды %s упал: %s", step, node.code, err)
            continue

        if not result.get("applied"):
            continue

        # Проверяем, помогло ли
        await asyncio.sleep(3)
        healthy, _ = await tcp_probe(node.host, node.port, settings.health_timeout_sec)
        session.add(
            HealthEvent(
                node_id=node.id,
                event="healed" if healthy else "failed",
                action=step,
                details=result,
            )
        )
        await session.flush()

        if healthy or step == "node_failover":
            node.status = NodeStatus.HEALTHY if healthy else NodeStatus.DISABLED
            node.fail_count = 0
            await session.flush()
            log.info("Нода %s восстановлена шагом %s", node.code, step)
            return {"success": True, "action": step, "details": result}

    session.add(HealthEvent(node_id=node.id, event="failed", action="all_steps_exhausted"))
    node.status = NodeStatus.DISABLED
    await session.flush()
    log.error("Нода %s: все шаги восстановления исчерпаны, нужен новый IP", node.code)
    return {"success": False, "action": None, "reason": "all_steps_exhausted"}


async def _step_sni_switch(session: AsyncSession, node: Node) -> dict[str, Any]:
    """
    Шаг 1: сменить маскировочный домен Reality.

    Часто блокировка бьёт не по IP, а по связке IP+SNI — смена домена
    возвращает доступ, не трогая инфраструктуру.
    """
    pool = list(node.sni_pool or [])
    if not pool:
        return {"applied": False, "reason": "пустой пул SNI"}

    current = node.current_sni
    candidates = [s for s in pool if s != current]
    if not candidates:
        return {"applied": False, "reason": "нет альтернативных SNI"}

    new_sni = candidates[0]
    async with MarzbanClient() as mz:
        hosts = await mz.get_hosts()
        tag = f"VLESS_REALITY_{node.code}"
        if tag not in hosts:
            return {"applied": False, "reason": f"тег {tag} не найден в панели"}
        for host in hosts[tag]:
            host["sni"] = new_sni
            host["host"] = new_sni
        await mz.update_hosts(hosts)

    node.current_sni = new_sni
    # Использованный SNI отправляем в конец пула — в следующий раз возьмём другой
    node.sni_pool = [s for s in pool if s != new_sni] + [new_sni]
    await session.flush()
    return {"applied": True, "old_sni": current, "new_sni": new_sni}


async def _step_port_switch(session: AsyncSession, node: Node) -> dict[str, Any]:
    """Шаг 2: переехать на резервный порт (если блокируют конкретный порт)."""
    ports = list(node.backup_ports or [])
    candidates = [p for p in ports if p != node.port]
    if not candidates:
        return {"applied": False, "reason": "нет резервных портов"}

    new_port = candidates[0]
    async with MarzbanClient() as mz:
        hosts = await mz.get_hosts()
        tag = f"VLESS_REALITY_{node.code}"
        if tag not in hosts:
            return {"applied": False, "reason": f"тег {tag} не найден в панели"}
        for host in hosts[tag]:
            host["port"] = new_port
        await mz.update_hosts(hosts)

    old_port = node.port
    node.port = new_port
    node.backup_ports = [p for p in ports if p != new_port] + [old_port]
    await session.flush()
    return {"applied": True, "old_port": old_port, "new_port": new_port}


async def _step_hysteria_promote(session: AsyncSession, node: Node) -> dict[str, Any]:
    """
    Шаг 3: сделать Hysteria2 основным протоколом для этой ноды.

    Помогает, когда TCP-трафик режется, а UDP ещё проходит
    (частый сценарий у мобильных операторов).
    """
    async with MarzbanClient() as mz:
        hosts = await mz.get_hosts()
        hy_tag = f"HY2_{node.code}"
        if hy_tag not in hosts:
            return {"applied": False, "reason": "Hysteria2 не настроена на ноде"}

    # Перевод пользователей на приоритет Hysteria2 выполняется тем,
    # что VLESS-inbound этой ноды временно исключается из их наборов.
    users_switched = await _switch_users_inbounds(session, node, exclude_vless=True)
    return {"applied": True, "protocol": "hysteria2", "users_switched": users_switched}


async def _step_node_failover(session: AsyncSession, node: Node) -> dict[str, Any]:
    """Шаг 4: увести пользователей на запасную ноду той же локации."""
    result = await session.execute(
        select(Node)
        .where(
            Node.location == node.location,
            Node.id != node.id,
            Node.status == NodeStatus.HEALTHY,
        )
        .order_by(Node.users_count.asc())
    )
    backup = result.scalars().first()
    if backup is None:
        return {"applied": False, "reason": "нет запасной ноды в локации"}

    users_switched = await _switch_users_inbounds(session, node, replacement=backup)
    return {
        "applied": True,
        "from_node": node.code,
        "to_node": backup.code,
        "users_switched": users_switched,
    }


async def _switch_users_inbounds(
    session: AsyncSession,
    node: Node,
    replacement: Node | None = None,
    exclude_vless: bool = False,
) -> int:
    """
    Переписать наборы inbounds у пользователей проблемной ноды.

    Ключ и ссылка-подписка при этом не меняются — в этом вся суть
    Dynamic Subscription: клиент просто получает другой конфиг внутри.
    """
    from app.services import subscriptions as sub_service

    result = await session.execute(
        select(User, Subscription)
        .join(Subscription, Subscription.user_id == User.id)
        .where(Subscription.status.in_(["active", "trial"]))
    )
    rows = result.all()
    switched = 0

    async with MarzbanClient() as mz:
        for user, subscription in rows:
            nodes = await sub_service.nodes_for_tariff(session, subscription.tariff_code)
            if node.code not in [n.code for n in nodes]:
                continue

            target_codes = [n.code for n in nodes if n.code != node.code]
            if replacement is not None:
                target_codes.append(replacement.code)
            elif not exclude_vless:
                target_codes.append(node.code)

            inbounds = build_inbounds([node.location], target_codes or [node.code])
            if exclude_vless and replacement is None:
                inbounds["vless"] = [
                    tag for tag in inbounds["vless"] if not tag.endswith(node.code)
                ]

            try:
                await mz.set_inbounds(user.marzban_username, inbounds)
                switched += 1
            except MarzbanError as err:
                log.error("Не удалось переключить tg=%s: %s", user.telegram_id, err)

    if replacement is not None:
        replacement.users_count += switched
        node.users_count = max(0, node.users_count - switched)
        await session.flush()

    log.info("Переключено пользователей: %s (нода %s)", switched, node.code)
    return switched


# ── Персональное восстановление (кнопка «Не работает») ─────────────────────


async def heal_user(session: AsyncSession, user: User) -> dict[str, Any]:
    """
    Индивидуальное лечение по кнопке «🆘 Не работает».

    Порядок:
      1. Если нода пользователя проблемная — общее восстановление.
      2. Иначе даём пользователю расширенный набор входящих
         (Hysteria2 + Shadowsocks вдобавок к VLESS) — часто проблема
         именно в протоколе у конкретного оператора.
      3. Возвращаем обновлённую ссылку-подписку.
    """
    from app.services import subscriptions as sub_service

    subscription = user.subscription
    if subscription is None or not subscription.is_active:
        return {"ok": False, "reason": "no_active_subscription"}

    nodes = await sub_service.nodes_for_tariff(session, subscription.tariff_code)
    problem_nodes = [n for n in nodes if n.status != NodeStatus.HEALTHY or n.fail_count > 0]

    if problem_nodes:
        report = await heal_node(session, problem_nodes[0])
        action = report.get("action") or "node_heal"
    else:
        # Расширяем набор протоколов персонально
        inbounds = build_inbounds([n.location for n in nodes], [n.code for n in nodes])
        try:
            async with MarzbanClient() as mz:
                await mz.set_inbounds(user.marzban_username, inbounds)
            action = "protocol_expand"
        except MarzbanError as err:
            log.error("Персональное лечение tg=%s не удалось: %s", user.telegram_id, err)
            return {"ok": False, "reason": "marzban_unavailable"}

    return {
        "ok": True,
        "action": action,
        "subscription_url": sub_service.subscription_url(user),
    }


# ── Пользователи, затронутые инцидентом ────────────────────────────────────


async def affected_users(session: AsyncSession, node: Node) -> list[User]:
    """Кому отправлять уведомление «мы всё починили»."""
    from app.services import subscriptions as sub_service

    result = await session.execute(
        select(User, Subscription)
        .join(Subscription, Subscription.user_id == User.id)
        .where(Subscription.status.in_(["active", "trial"]))
    )
    users: list[User] = []
    for user, subscription in result.all():
        nodes = await sub_service.nodes_for_tariff(session, subscription.tariff_code)
        if node.code in [n.code for n in nodes]:
            users.append(user)
    return users
