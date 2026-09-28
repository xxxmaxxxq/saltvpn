"""
ЮKassa: приём СБП.

Главное здесь — что уведомление само по себе ничего не решает.
ЮKassa не подписывает вебхуки, поэтому подделать POST может кто угодно,
кто знает адрес ручки. Тесты закрепляют обе линии защиты: фильтр по
сети отправителя и обязательную сверку с API перед начислением.
"""

import json

import pytest

from app.payments.yookassa import (
    YooKassaProvider,
    client_ip_from_headers,
    ip_allowed,
)


def _notification(
    payment_id: str = "2f8c1b0e-000f-5000-9000-1f3a0b2c4d5e",
    status: str = "succeeded",
    event: str = "payment.succeeded",
    our_id: int | None = 42,
) -> bytes:
    obj = {
        "id": payment_id,
        "status": status,
        "paid": status == "succeeded",
        "amount": {"value": "149.00", "currency": "RUB"},
        "metadata": {"payment_id": str(our_id), "telegram_id": "495414590"} if our_id else {},
    }
    return json.dumps({"type": "notification", "event": event, "object": obj}).encode()


# ── Фильтр по адресу отправителя ───────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    ["185.71.76.1", "185.71.77.30", "77.75.153.10", "77.75.156.11", "77.75.154.200"],
)
def test_official_addresses_pass(addr):
    assert ip_allowed(addr) is True


@pytest.mark.parametrize("addr", ["8.8.8.8", "192.168.1.1", "77.75.156.12", "", "не-адрес"])
def test_foreign_addresses_rejected(addr):
    assert ip_allowed(addr) is False


def test_ipv6_range_allowed():
    assert ip_allowed("2a02:5180::1") is True


def test_real_ip_header_wins_over_forwarded():
    # Caddy сам проставляет X-Real-IP, затирая присланное клиентом
    headers = {"x-real-ip": "185.71.76.1", "x-forwarded-for": "8.8.8.8"}
    assert client_ip_from_headers(headers) == "185.71.76.1"


def test_forwarded_for_takes_first_hop():
    headers = {"x-forwarded-for": "185.71.76.1, 10.0.0.1"}
    assert client_ip_from_headers(headers) == "185.71.76.1"


# ── Разбор уведомления ─────────────────────────────────────────────────────


def test_webhook_from_foreign_ip_is_rejected():
    """Подделка с чужого адреса не должна даже разбираться."""
    provider = YooKassaProvider()
    with pytest.raises(ValueError, match="чужого адреса"):
        provider.parse_webhook(_notification(), {"x-real-ip": "8.8.8.8"})


def test_webhook_without_ip_is_rejected():
    provider = YooKassaProvider()
    with pytest.raises(ValueError):
        provider.parse_webhook(_notification(), {})


def test_successful_notification_parsed():
    provider = YooKassaProvider()
    result = provider.parse_webhook(_notification(), {"x-real-ip": "185.71.76.1"})
    assert result.is_paid is True
    assert result.our_payment_id == 42
    assert result.amount == 149.0
    assert result.external_id == "2f8c1b0e-000f-5000-9000-1f3a0b2c4d5e"


def test_canceled_payment_is_not_paid():
    provider = YooKassaProvider()
    body = _notification(status="canceled", event="payment.canceled")
    result = provider.parse_webhook(body, {"x-real-ip": "185.71.76.1"})
    assert result.is_paid is False


def test_waiting_for_capture_is_not_paid():
    provider = YooKassaProvider()
    body = _notification(status="waiting_for_capture", event="payment.waiting_for_capture")
    result = provider.parse_webhook(body, {"x-real-ip": "185.71.76.1"})
    assert result.is_paid is False


def test_broken_metadata_does_not_crash():
    """Без нашего payment_id платёж ищется по external_id — падать нельзя."""
    provider = YooKassaProvider()
    body = _notification(our_id=None)
    result = provider.parse_webhook(body, {"x-real-ip": "185.71.76.1"})
    assert result.our_payment_id is None
    assert result.external_id


# ── Обратная проверка через API ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_requires_external_id():
    provider = YooKassaProvider()
    assert await provider.confirm_payment("") is False


@pytest.mark.asyncio
async def test_confirm_true_only_when_succeeded_and_paid(monkeypatch):
    provider = YooKassaProvider()
    _fake_response(monkeypatch, 200, {"status": "succeeded", "paid": True})
    assert await provider.confirm_payment("abc") is True


@pytest.mark.asyncio
async def test_confirm_false_when_not_paid(monkeypatch):
    """Статус succeeded без paid — не повод начислять."""
    provider = YooKassaProvider()
    _fake_response(monkeypatch, 200, {"status": "succeeded", "paid": False})
    assert await provider.confirm_payment("abc") is False


@pytest.mark.asyncio
async def test_confirm_false_when_pending(monkeypatch):
    provider = YooKassaProvider()
    _fake_response(monkeypatch, 200, {"status": "pending", "paid": False})
    assert await provider.confirm_payment("abc") is False


@pytest.mark.asyncio
async def test_confirm_false_when_api_errors(monkeypatch):
    """Панель платёжки недоступна — лучше не начислить, чем начислить зря."""
    provider = YooKassaProvider()
    _fake_response(monkeypatch, 500, {})
    assert await provider.confirm_payment("abc") is False


@pytest.mark.asyncio
async def test_confirm_false_when_network_down(monkeypatch):
    import httpx

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *_a, **_kw):
            raise httpx.ConnectError("нет сети")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())
    assert await YooKassaProvider().confirm_payment("abc") is False


def _fake_response(monkeypatch, status_code: int, payload: dict) -> None:
    """Подменить httpx.AsyncClient ответом платёжки."""
    import httpx

    class _Response:
        def __init__(self):
            self.status_code = status_code
            self.text = json.dumps(payload)

        def json(self):
            return payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *_a, **_kw):
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())


# ── Создание счёта ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoice_asks_for_sbp_and_carries_our_id(monkeypatch):
    """В запросе должен быть способ sbp и наш payment_id в metadata."""
    import httpx

    sent: dict = {}

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "id": "2f8c1b0e-000f-5000-9000-1f3a0b2c4d5e",
                "status": "pending",
                "confirmation": {"confirmation_url": "https://yoomoney.ru/checkout/payments/sbp/1"},
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json=None, headers=None, auth=None):
            sent.update(url=url, payload=json, headers=headers, auth=auth)
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())

    invoice = await YooKassaProvider().create_invoice(
        payment_id=42, amount_rub=149, description="VPN NL на 1 мес.", telegram_id=495414590
    )

    assert invoice.payment_url.startswith("https://")
    assert invoice.external_id == "2f8c1b0e-000f-5000-9000-1f3a0b2c4d5e"
    assert sent["payload"]["payment_method_data"] == {"type": "sbp"}
    assert sent["payload"]["amount"] == {"value": "149.00", "currency": "RUB"}
    assert sent["payload"]["metadata"]["payment_id"] == "42"
    assert sent["payload"]["capture"] is True
    # Ключ идемпотентности защищает от второго счёта при обрыве связи
    assert sent["headers"]["Idempotence-Key"]


@pytest.mark.asyncio
async def test_invoice_fails_loudly_without_payment_url(monkeypatch):
    """Счёт без ссылки бесполезен — лучше ошибка, чем пустая кнопка."""
    import httpx

    class _Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "x", "status": "pending", "confirmation": {}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_a, **_kw):
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())
    with pytest.raises(RuntimeError, match="ссылку на оплату"):
        await YooKassaProvider().create_invoice(1, 149, "VPN", 1)


# ── Включение провайдера ───────────────────────────────────────────────────


def test_provider_off_without_keys():
    from app.payments.registry import get_provider

    provider = get_provider("yookassa")
    # В тестовом окружении ключей нет — кнопка не должна показываться
    assert provider.enabled is False


def test_other_providers_skip_the_extra_call():
    """Провайдеры с честной подписью не делают лишнего запроса."""
    import asyncio

    from app.payments.registry import get_provider

    assert asyncio.run(get_provider("platega").confirm_payment("x")) is None


# ── Привязка счёта к заказу ────────────────────────────────────────────────


def test_matching_invoice_accepted():
    from app.services.billing import invoice_belongs_to_payment

    assert invoice_belongs_to_payment("pay-1", "pay-1") is True


def test_foreign_invoice_rejected():
    """
    Ключевая защита: уведомление принесло id настоящей, но чужой оплаты.

    Так можно было бы оплатить самый дешёвый тариф, а в metadata указать
    заказ на год — провайдер подтвердил бы платёж, он ведь реальный.
    """
    from app.services.billing import invoice_belongs_to_payment

    assert invoice_belongs_to_payment("pay-dorogoy-zakaz", "pay-deshevaya-oplata") is False


def test_missing_stored_id_does_not_block():
    """Связь оборвалась до записи external_id — сверять нечего."""
    from app.services.billing import invoice_belongs_to_payment

    assert invoice_belongs_to_payment(None, "pay-1") is True
    assert invoice_belongs_to_payment("", "pay-1") is True
