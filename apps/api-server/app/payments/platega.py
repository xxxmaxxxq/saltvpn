"""
Platega — приём платежей по СБП и картам МИР/Visa/MasterCard без ИП.

Основной способ оплаты для российской аудитории (≈80% платежей).
Документация меняется, поэтому все специфичные поля собраны в одном месте:
если формат API изменится, правка нужна только здесь.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.payments.base import Invoice, PaymentProvider, WebhookResult
from app.utils.security import verify_hmac_sha256

log = logging.getLogger(__name__)
settings = get_settings()


class PlategaProvider(PaymentProvider):
    code = "platega"

    @property
    def enabled(self) -> bool:
        return bool(
            settings.platega_enabled
            and settings.platega_merchant_id
            and settings.platega_secret_key
        )

    async def create_invoice(
        self, payment_id: int, amount_rub: int, description: str, telegram_id: int
    ) -> Invoice:
        payload = {
            "paymentMethod": 2,  # 2 = карта/СБП
            "amount": amount_rub,
            "currency": "RUB",
            "description": description,
            "return": f"https://t.me/{settings.bot_username}",
            "failedUrl": f"https://t.me/{settings.bot_username}",
            "payload": json.dumps({"payment_id": payment_id, "telegram_id": telegram_id}),
        }
        headers = {
            "X-MerchantId": settings.platega_merchant_id,
            "X-Secret": settings.platega_secret_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{settings.platega_api_url.rstrip('/')}/transaction/process",
                json=payload,
                headers=headers,
            )
        if resp.status_code >= 400:
            log.error("Platega вернула %s: %s", resp.status_code, resp.text[:300])
            raise RuntimeError("Не удалось создать счёт в Platega")

        data = resp.json()
        return Invoice(
            payment_url=data.get("redirect") or data.get("paymentUrl", ""),
            external_id=str(data.get("transactionId") or data.get("id", "")),
            raw=data,
        )

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        signature = headers.get("x-signature") or headers.get("X-Signature", "")
        if not verify_hmac_sha256(body, signature, settings.platega_webhook_secret):
            raise ValueError("Неверная подпись вебхука Platega")

        data = json.loads(body)
        status = str(data.get("status", "")).upper()

        our_payment_id = None
        raw_payload = data.get("payload")
        if raw_payload:
            try:
                meta = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                our_payment_id = int(meta.get("payment_id")) if meta.get("payment_id") else None
            except (ValueError, TypeError, AttributeError):
                our_payment_id = None

        return WebhookResult(
            external_id=str(data.get("transactionId") or data.get("id", "")),
            is_paid=status in ("CONFIRMED", "SUCCESS", "PAID"),
            amount=float(data.get("amount", 0) or 0),
            currency=data.get("currency", "RUB"),
            our_payment_id=our_payment_id,
            raw=data,
        )
