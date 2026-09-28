"""
Клиент API панели Marzban.

Что делает панель: хранит аккаунты Xray, раздаёт ссылки-подписки, управляет
нодами. Мы работаем с ней только через REST — своего кода для Xray у нас нет,
и это осознанно (меньше кода = меньше поломок).

Ключевая идея: один пользователь сервиса = один аккаунт в Marzban с именем
u<telegram_id>. Смена тарифа = изменение набора inbounds у этого аккаунта,
ссылка-подписка при этом НЕ меняется.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class MarzbanError(RuntimeError):
    """Панель ответила ошибкой или недоступна."""


class MarzbanClient:
    """
    Асинхронный клиент панели.

    Токен авторизации кэшируется в памяти и обновляется автоматически при 401.
    Использование:

        async with MarzbanClient() as mz:
            await mz.create_user("u123", inbounds={"vless": ["VLESS_REALITY_nl-1"]}, days=3)
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.marzban_base_url).rstrip("/")
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0)

    async def __aenter__(self) -> MarzbanClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ── Авторизация ────────────────────────────────────────────────────────

    async def _login(self) -> str:
        try:
            resp = await self._client.post(
                "/api/admin/token",
                data={
                    "username": settings.marzban_username,
                    "password": settings.marzban_password,
                },
            )
        except httpx.HTTPError as err:
            # Панель недоступна (не поднята, сеть, DNS) — превращаем сетевую
            # ошибку в MarzbanError, чтобы вызывающий код обработал её штатно
            raise MarzbanError(f"Панель Marzban недоступна: {err}") from err
        if resp.status_code != 200:
            raise MarzbanError(f"Не удалось авторизоваться в Marzban: {resp.status_code}")
        self._token = resp.json()["access_token"]
        return self._token

    async def _headers(self) -> dict[str, str]:
        if not self._token:
            await self._login()
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """
        Запрос к панели.

        Любая сетевая ошибка (панель не поднята, DNS, таймаут) превращается
        в MarzbanError. Это важно: бизнес-логика ловит именно MarzbanError и
        продолжает работать без панели — подписка в нашей базе обновится,
        а синхронизация с Xray повторится, когда панель вернётся.
        """
        try:
            return await self._request_raw(method, path, **kwargs)
        except MarzbanError:
            raise
        except httpx.HTTPError as err:
            raise MarzbanError(f"Панель Marzban недоступна: {err}") from err

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _request_raw(self, method: str, path: str, **kwargs: Any) -> Any:
        """Сам запрос: автоперелогин при 401 и ретраи при сетевых сбоях."""
        headers = await self._headers()
        resp = await self._client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401:
            await self._login()
            headers = await self._headers()
            resp = await self._client.request(method, path, headers=headers, **kwargs)

        if resp.status_code >= 400:
            raise MarzbanError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ── Пользователи ───────────────────────────────────────────────────────

    async def get_user(self, username: str) -> dict[str, Any] | None:
        """Вернуть аккаунт или None, если его нет."""
        try:
            return await self._request("GET", f"/api/user/{username}")
        except MarzbanError as err:
            if "404" in str(err):
                return None
            raise

    async def create_user(
        self,
        username: str,
        inbounds: dict[str, list[str]],
        expire_at: datetime,
        data_limit_bytes: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        """
        Создать аккаунт.

        inbounds         — какие входящие соединения доступны, например
                           {"vless": ["VLESS_REALITY_nl-1"], "hysteria2": [...]}
        expire_at        — момент окончания подписки
        data_limit_bytes — 0 = безлимит
        """
        payload = {
            "username": username,
            "proxies": {proto: {} for proto in inbounds},
            "inbounds": inbounds,
            "expire": int(expire_at.replace(tzinfo=UTC).timestamp()),
            "data_limit": data_limit_bytes,
            "data_limit_reset_strategy": "month",
            "status": "active",
            "note": note,
        }
        return await self._request("POST", "/api/user", json=payload)

    async def modify_user(self, username: str, **fields: Any) -> dict[str, Any]:
        """Изменить аккаунт: срок, лимиты, набор inbounds, статус."""
        return await self._request("PUT", f"/api/user/{username}", json=fields)

    async def set_expire(self, username: str, expire_at: datetime) -> dict[str, Any]:
        return await self.modify_user(
            username, expire=int(expire_at.replace(tzinfo=UTC).timestamp())
        )

    async def set_inbounds(self, username: str, inbounds: dict[str, list[str]]) -> dict[str, Any]:
        """
        Переключить пользователя на другой набор входящих.

        Именно этот вызов делает Auto-Healing и смену тарифа: ссылка-подписка
        у клиента остаётся прежней, а конфиги внутри неё обновляются.
        """
        return await self.modify_user(
            username, inbounds=inbounds, proxies={proto: {} for proto in inbounds}
        )

    async def disable_user(self, username: str) -> dict[str, Any]:
        """Выключить доступ (используется при паузе подписки)."""
        return await self.modify_user(username, status="disabled")

    async def enable_user(self, username: str) -> dict[str, Any]:
        return await self.modify_user(username, status="active")

    async def delete_user(self, username: str) -> None:
        await self._request("DELETE", f"/api/user/{username}")

    async def reset_traffic(self, username: str) -> None:
        await self._request("POST", f"/api/user/{username}/reset")

    async def get_subscription_path(self, username: str) -> str | None:
        """
        Внутренний путь подписки, например /sub/eyJ0eXAiOi...

        Панель отдаёт конфиги не по имени аккаунта, а по собственному токену:
        запрос к /sub/<имя> она честно не находит и отвечает 404.
        """
        user = await self.get_user(username)
        if not user:
            return None
        return subscription_path_of(user.get("subscription_url") or "")

    async def get_subscription_url(self, username: str) -> str:
        """Ссылка-подписка, которую импортирует приложение клиента."""
        user = await self.get_user(username)
        if not user:
            raise MarzbanError(f"Аккаунт {username} не найден")
        sub_url = user.get("subscription_url", "")
        base = (settings.marzban_subscription_url or settings.public_base_url).rstrip("/")
        return f"{base}{sub_url}" if sub_url.startswith("/") else sub_url

    async def get_user_usage(self, username: str) -> int:
        """Использованный трафик в байтах за текущий период."""
        user = await self.get_user(username)
        return int(user.get("used_traffic", 0)) if user else 0

    async def list_inbounds(self) -> dict[str, list[str]]:
        """
        Какие inbounds настроены в панели: {протокол: [теги]}.

        Нужно, чтобы не отправлять в Marzban теги несуществующих inbounds:
        панель отвечает на такое ошибкой, и пользователь остаётся без ключа.
        """
        data = await self._request("GET", "/api/inbounds") or {}
        result: dict[str, list[str]] = {}
        for protocol, inbounds in data.items():
            tags = [i.get("tag") for i in inbounds if i.get("tag")]
            if tags:
                result[protocol] = tags
        return result

    async def list_users(self, limit: int = 200) -> list[dict[str, Any]]:
        """
        Аккаунты панели: статус, трафик, время последней активности.

        IP-адресов здесь нет и быть не может: access-логи Xray выключены,
        панель хранит только факт активности (online_at) и объём трафика.
        """
        data = await self._request("GET", f"/api/users?limit={limit}") or {}
        return data.get("users", [])

    # ── Ноды ───────────────────────────────────────────────────────────────

    async def list_nodes(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/nodes") or []

    async def get_node(self, node_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/api/node/{node_id}")

    async def node_is_connected(self, node_id: int) -> bool:
        try:
            node = await self.get_node(node_id)
        except MarzbanError:
            return False
        return node.get("status") == "connected"

    async def get_node_settings(self) -> dict[str, Any]:
        """Сертификат и требования к версии ноды — нужны при подключении новой."""
        return await self._request("GET", "/api/node/settings") or {}

    async def add_node(
        self,
        name: str,
        address: str,
        port: int = 62050,
        api_port: int = 62051,
        usage_coefficient: float = 1.0,
    ) -> dict[str, Any]:
        """
        Зарегистрировать ноду в панели.

        Нода должна быть уже запущена с сертификатом этой панели, иначе
        она подключится, но останется в статусе error.
        """
        payload = {
            "name": name,
            "address": address,
            "port": port,
            "api_port": api_port,
            "usage_coefficient": usage_coefficient,
            "add_as_new_host": False,
        }
        return await self._request("POST", "/api/node", json=payload)

    async def reconnect_node(self, node_id: int) -> None:
        await self._request("POST", f"/api/node/{node_id}/reconnect")

    # ── Хосты (inbound hosts) ──────────────────────────────────────────────

    async def get_hosts(self) -> dict[str, Any]:
        """Текущие хосты по тегам inbound — здесь живут адрес, порт и SNI."""
        return await self._request("GET", "/api/hosts") or {}

    async def update_hosts(self, hosts: dict[str, Any]) -> dict[str, Any]:
        """
        Обновить хосты целиком.

        Используется Auto-Healing при смене SNI/порта: меняем поле в нужном
        теге и отправляем структуру обратно.
        """
        return await self._request("PUT", "/api/hosts", json=hosts)

    # ── Статистика ─────────────────────────────────────────────────────────

    async def get_system_stats(self) -> dict[str, Any]:
        return await self._request("GET", "/api/system") or {}


def subscription_path_of(subscription_url: str) -> str | None:
    """
    Оставить от ссылки подписки только путь.

    Когда панели задан XRAY_SUBSCRIPTION_URL_PREFIX (а он задан: клиенты
    должны получать наш домен), она возвращает в subscription_url полный
    адрес. Приклеивать его к внутреннему адресу панели нельзя — получается
    строка вида http://marzban:8080https://... и запрос падает.
    """
    if not subscription_url:
        return None
    parts = urlsplit(subscription_url)
    path = parts.path
    if not path:
        return None
    return f"{path}?{parts.query}" if parts.query else path


def filter_available(
    wanted: dict[str, list[str]], available: dict[str, list[str]]
) -> dict[str, list[str]]:
    """
    Оставить только те теги, которые действительно есть в панели.

    Мы просим VLESS, Hysteria2 и Shadowsocks, но на ноде может быть поднят
    только VLESS. Без этой фильтрации создание пользователя падает целиком —
    лучше выдать ключ с тем, что есть.
    """
    result: dict[str, list[str]] = {}
    for protocol, tags in wanted.items():
        existing = [tag for tag in tags if tag in available.get(protocol, [])]
        if existing:
            result[protocol] = existing
    return result


def build_inbounds(location_codes: list[str], node_codes: list[str]) -> dict[str, list[str]]:
    """
    Собрать структуру inbounds по кодам нод.

    Теги формируются так же, как в шаблонах config/xray-vless:
        VLESS_REALITY_<node_code>,  HY2_<node_code>,  SS2022_<node_code>
    """
    vless = [f"VLESS_REALITY_{code}" for code in node_codes]
    hysteria = [f"HY2_{code}" for code in node_codes]
    shadowsocks = [f"SS2022_{code}" for code in node_codes]
    return {
        "vless": vless,
        "hysteria2": hysteria,
        "shadowsocks": shadowsocks,
    }
