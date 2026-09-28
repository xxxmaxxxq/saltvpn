"""
CryptoPay (@CryptoBot) — оплата в USDT/TON.

Нужен для двух аудиторий: тех, кто не хочет светить карту, и экспатов
без российских карт. Плюс это самый устойчивый к блокировкам канал.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.payments.base import Invoice, PaymentProvider, WebhookResult
from app.utils.security import verify_cryptopay_signature

log = logging.getLogger(__name__)
settings = get_settings()

# Резервный курс, если API курсов недоступно (лучше продать чуть дороже,
# чем не продать вообще или уйти в минус)
FALLBACK_USD_RATE = 100.0


class CryptoPayProvider(PaymentProvider):
    code = "cryptopay"

    @property
    def enabled(self) -> bool:
        return bool(settings.cryptopay_enabled and settings.cryptopay_token)

    async def _rub_to_asset(self, amount_rub: int) -> float:
        """Перевести рубли в валюту счёта (по курсу CryptoBot или из .env)."""
        if settings.cryptopay_usd_rate > 0:
            return round(amount_rub / settings.cryptopay_usd_rate, 2)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{settings.cryptopay_api_url.rstrip('/')}/getExchangeRates",
                    headers={"Crypto-Pay-API-Token": settings.cryptopay_token},
                )
            rates = resp.json().get("result", [])
            for rate in rates:
                if rate.get("source") == settings.cryptopay_asset and rate.get("target") == "RUB":
                    return round(amount_rub / float(rate["rate"]), 2)
        except (httpx.HTTPError, ValueError, KeyError) as err:
            log.warning("Не удалось получить курс CryptoBot: %s", err)

        return round(amount_rub / FALLBACK_USD_RATE, 2)

    async def create_invoice(
        self, payment_id: int, amount_rub: int, description: str, telegram_id: int
    ) -> Invoice:
        amount = await self._rub_to_asset(amount_rub)
        payload = {
            "asset": settings.cryptopay_asset,
            "amount": str(amount),
            "description": description[:1024],
            "payload": json.dumps({"payment_id": payment_id, "telegram_id": telegram_id}),
            "paid_btn_name": "openBot",
            "paid_btn_url": f"https://t.me/{settings.bot_username}",
            "expires_in": 3600,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{settings.cryptopay_api_url.rstrip('/')}/createInvoice",
                json=payload,
                headers={"Crypto-Pay-API-Token": settings.cryptopay_token},
            )
        data = resp.json()
        if not data.get("ok"):
            log.error("CryptoPay вернул ошибку: %s", data)
            raise RuntimeError("Не удалось создать счёт в CryptoPay")

        result = data["result"]
        return Invoice(
            payment_url=result["bot_invoice_url"],
            external_id=str(result["invoice_id"]),
            raw=result,
        )

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        signature = headers.get("crypto-pay-api-signature", "")
        if not verify_cryptopay_signature(body, signature, settings.cryptopay_token):
            raise ValueError("Неверная подпись вебхука CryptoPay")

        data = json.loads(body)
        if data.get("update_type") != "invoice_paid":
            return WebhookResult(external_id="", is_paid=False, raw=data)

        invoice = data.get("payload", {})
        our_payment_id = None
        try:
            meta = json.loads(invoice.get("payload", "{}"))
            our_payment_id = int(meta.get("payment_id")) if meta.get("payment_id") else None
        except (ValueError, TypeError):
            pass

        return WebhookResult(
            external_id=str(invoice.get("invoice_id", "")),
            is_paid=invoice.get("status") == "paid",
            amount=float(invoice.get("amount", 0) or 0),
            currency=invoice.get("asset", "USDT"),
            our_payment_id=our_payment_id,
            raw=invoice,
        )
