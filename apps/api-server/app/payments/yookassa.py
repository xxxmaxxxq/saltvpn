"""
ЮKassa — приём платежей по СБП для самозанятых.

Почему именно она основной способ для СБП: самозанятый не может
подключить торговый эквайринг напрямую (для этого нужен статус ИП),
но может принимать оплату через платёжный сервис, у которого свой
договор с банками. ЮKassa работает с самозанятыми и сама передаёт
чек в ФНС, поэтому вручную пробивать каждый платёж в «Моём налоге»
не нужно.

Комиссия ниже, чем у шлюзов без юридического статуса, а деньги идут
на счёт легально — это главное отличие от p2p-переводов на личную карту,
которые банк рано или поздно блокирует по 115-ФЗ.

ВАЖНО про безопасность. ЮKassa НЕ подписывает уведомления: приходит
обычный POST без HMAC. Поэтому здесь две линии защиты:
  1) проверка адреса отправителя по официальному списку сетей ЮKassa;
  2) обратный запрос к их API — единственный источник истины.
Само тело уведомления не считается доказательством оплаты: адрес можно
подделать, а сумму в JSON — тем более. Начисление происходит только
после того, как ЮKassa подтвердила платёж в ответе на наш запрос.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import uuid

import httpx

from app.config import get_settings
from app.payments.base import Invoice, PaymentProvider, WebhookResult

log = logging.getLogger(__name__)
settings = get_settings()

# Сети, из которых ЮKassa шлёт уведомления (их документация, раздел
# «Входящие уведомления»). Всё, что пришло с других адресов, до разбора
# тела не допускается.
YOOKASSA_NETWORKS: tuple[str, ...] = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "77.75.154.128/25",
    "2a02:5180::/32",
)

_NETWORKS = tuple(ipaddress.ip_network(net) for net in YOOKASSA_NETWORKS)

# Статусы платежа в API ЮKassa
STATUS_SUCCEEDED = "succeeded"
STATUS_CANCELED = "canceled"


def ip_allowed(client_ip: str) -> bool:
    """Пришло ли уведомление из сети ЮKassa."""
    if not client_ip:
        return False
    try:
        address = ipaddress.ip_address(client_ip.strip())
    except ValueError:
        return False
    return any(address in network for network in _NETWORKS)


def client_ip_from_headers(headers: dict[str, str]) -> str:
    """
    Адрес отправителя уведомления.

    Caddy проставляет X-Real-IP сам (см. docker/Caddyfile.master), затирая
    то, что прислал клиент, — поэтому заголовку можно верить. Контейнер
    api портов наружу не публикует, напрямую к нему не достучаться.
    """
    real_ip = headers.get("x-real-ip", "")
    if real_ip:
        return real_ip
    # X-Forwarded-For может содержать цепочку — исходный клиент первый
    forwarded = headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else ""


class YooKassaProvider(PaymentProvider):
    code = "yookassa"

    @property
    def enabled(self) -> bool:
        return bool(
            settings.yookassa_enabled and settings.yookassa_shop_id and settings.yookassa_secret_key
        )

    # ── Создание счёта ─────────────────────────────────────────────────────

    def _auth(self) -> tuple[str, str]:
        return (settings.yookassa_shop_id, settings.yookassa_secret_key)

    def _receipt(self, amount_rub: int, description: str, telegram_id: int) -> dict | None:
        """
        Данные чека для ФНС.

        Чек обязателен по 54-ФЗ, и ЮKassa формирует его сама, если передать
        этот блок. Нужен контакт покупателя: у Telegram нет ни почты, ни
        телефона пользователя, поэтому подставляется служебный адрес из
        настроек — так делают все боты, у которых нет формы с контактами.
        Без YOOKASSA_RECEIPT_EMAIL блок не отправляется вовсе: тогда чек
        придётся пробивать вручную в «Моём налоге».
        """
        if not settings.yookassa_receipt_email:
            return None
        return {
            "customer": {"email": settings.yookassa_receipt_email},
            "items": [
                {
                    "description": description[:128],
                    "quantity": "1.00",
                    "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
                    # 1 = без НДС: самозанятые НДС не платят
                    "vat_code": 1,
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }
            ],
        }

    async def create_invoice(
        self, payment_id: int, amount_rub: int, description: str, telegram_id: int
    ) -> Invoice:
        payload: dict = {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            # sbp — оплата через Систему быстрых платежей. Пользователь
            # уходит на страницу ЮKassa, выбирает банк и подтверждает
            # перевод в своём приложении
            "payment_method_data": {"type": "sbp"},
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{settings.bot_username}",
            },
            # true — деньги списываются сразу, без двухстадийной схемы
            "capture": True,
            "description": description[:128],
            # metadata вернётся в уведомлении — по ней находим свой платёж
            "metadata": {"payment_id": str(payment_id), "telegram_id": str(telegram_id)},
        }
        receipt = self._receipt(amount_rub, description, telegram_id)
        if receipt:
            payload["receipt"] = receipt

        headers = {
            # Ключ идемпотентности: если запрос повторится из-за обрыва
            # связи, ЮKassa вернёт тот же платёж, а не создаст второй
            "Idempotence-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{settings.yookassa_api_url.rstrip('/')}/payments",
                json=payload,
                headers=headers,
                auth=self._auth(),
            )
        if resp.status_code >= 400:
            log.error("ЮKassa вернула %s: %s", resp.status_code, resp.text[:300])
            raise RuntimeError("Не удалось создать счёт в ЮKassa")

        data = resp.json()
        confirmation = data.get("confirmation") or {}
        payment_url = confirmation.get("confirmation_url", "")
        if not payment_url:
            log.error("ЮKassa не вернула ссылку на оплату: %s", str(data)[:300])
            raise RuntimeError("ЮKassa не вернула ссылку на оплату")

        return Invoice(payment_url=payment_url, external_id=str(data.get("id", "")), raw=data)

    # ── Уведомление ────────────────────────────────────────────────────────

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        """
        Разобрать уведомление, отсеяв всё, что пришло не от ЮKassa.

        Подписи у ЮKassa нет, поэтому здесь только фильтр по адресу.
        Настоящая проверка — в confirm_payment: там мы спрашиваем саму
        платёжку и лишь её ответу верим.
        """
        client_ip = client_ip_from_headers(headers)
        if not ip_allowed(client_ip):
            raise ValueError(f"Уведомление ЮKassa с чужого адреса: {client_ip or 'неизвестен'}")

        data = json.loads(body)
        obj = data.get("object") or {}
        event = str(data.get("event", ""))

        our_payment_id = None
        meta = obj.get("metadata") or {}
        raw_id = meta.get("payment_id")
        if raw_id is not None:
            try:
                our_payment_id = int(raw_id)
            except (TypeError, ValueError):
                our_payment_id = None

        amount = obj.get("amount") or {}

        return WebhookResult(
            external_id=str(obj.get("id", "")),
            # Тело уведомления — только повод сходить в API, а не
            # доказательство: окончательное слово за confirm_payment
            is_paid=event == "payment.succeeded" and str(obj.get("status", "")) == STATUS_SUCCEEDED,
            amount=float(amount.get("value", 0) or 0),
            currency=amount.get("currency", "RUB"),
            our_payment_id=our_payment_id,
            raw=data,
        )

    async def confirm_payment(self, external_id: str) -> bool:
        """
        Спросить у ЮKassa, оплачен ли платёж на самом деле.

        Это и есть источник истины. Пока здесь не вернулось True, дни
        не начисляются, даже если уведомление выглядело безупречно.
        """
        if not external_id:
            return False

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{settings.yookassa_api_url.rstrip('/')}/payments/{external_id}",
                    auth=self._auth(),
                )
        except httpx.HTTPError as err:
            log.error("ЮKassa недоступна при проверке платежа %s: %s", external_id, err)
            return False

        if resp.status_code >= 400:
            log.error(
                "ЮKassa ответила %s на проверку платежа %s: %s",
                resp.status_code,
                external_id,
                resp.text[:200],
            )
            return False

        data = resp.json()
        confirmed = data.get("status") == STATUS_SUCCEEDED and bool(data.get("paid"))
        if not confirmed:
            log.warning(
                "ЮKassa не подтвердила платёж %s: status=%s paid=%s",
                external_id,
                data.get("status"),
                data.get("paid"),
            )
        return confirmed
