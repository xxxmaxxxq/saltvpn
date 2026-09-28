"""
Тесты устойчивости клиента панели.

Главное требование: недоступная панель не должна ронять бизнес-логику.
Любая сетевая ошибка обязана приходить как MarzbanError, потому что именно
её ловят сервисы подписок, биллинга и Auto-Healing.
"""

import httpx
import pytest

from app.marzban import MarzbanClient, MarzbanError, build_inbounds


async def test_network_error_becomes_marzban_error(monkeypatch):
    client = MarzbanClient(base_url="http://marzban.invalid")

    async def boom(*args, **kwargs):
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr(client._client, "post", boom)
    monkeypatch.setattr(client._client, "request", boom)

    with pytest.raises(MarzbanError):
        await client._request("GET", "/api/user/u1")

    await client.close()


async def test_get_user_returns_none_on_404(monkeypatch):
    client = MarzbanClient(base_url="http://marzban.invalid")

    async def not_found(*args, **kwargs):
        raise MarzbanError("GET /api/user/u1 -> 404: not found")

    monkeypatch.setattr(client, "_request", not_found)
    assert await client.get_user("u1") is None
    await client.close()


def test_build_inbounds_tags_match_templates():
    """Теги должны совпадать с шаблонами в config/xray-vless и config/hysteria2."""
    inbounds = build_inbounds(["nl"], ["nl-1", "nl-2"])
    assert inbounds["vless"] == ["VLESS_REALITY_nl-1", "VLESS_REALITY_nl-2"]
    assert inbounds["hysteria2"] == ["HY2_nl-1", "HY2_nl-2"]
    assert inbounds["shadowsocks"] == ["SS2022_nl-1", "SS2022_nl-2"]


def test_subscription_path_strips_absolute_url():
    """
    Панели задан XRAY_SUBSCRIPTION_URL_PREFIX, поэтому в subscription_url она
    возвращает полный адрес. Приклеивание его к внутреннему адресу давало
    строку http://marzban:8080https://... и ответ 500 у клиента.
    """
    from app.marzban import subscription_path_of

    assert subscription_path_of("https://vpn.example.com/sub/tok123") == "/sub/tok123"
    assert subscription_path_of("/sub/tok123") == "/sub/tok123"
    assert subscription_path_of("https://vpn.example.com/sub/t?x=1") == "/sub/t?x=1"
    assert subscription_path_of("") is None
