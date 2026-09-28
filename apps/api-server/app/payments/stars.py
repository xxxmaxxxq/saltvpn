"""
Telegram Stars — оплата внутри Telegram.

Особенность: вебхука нет, счёт отправляется сообщением в чат, а подтверждение
приходит апдейтами pre_checkout_query и successful_payment (см. bot/handlers).
Комиссия высокая, зато работает всегда и не зависит от внешних шлюзов —
это наш «последний рубеж», если остальные способы отвалятся.
"""

from __future__ import annotations

import math

from app.config import get_settings
from app.payments.base import Invoice, PaymentProvider, WebhookResult

settings = get_settings()


class StarsProvider(PaymentProvider):
    code = "stars"

    @property
    def enabled(self) -> bool:
        return bool(settings.stars_enabled)

    @staticmethod
    def rub_to_stars(amount_rub: int) -> int:
        """Рубли -> звёзды по курсу из .env, округление вверх."""
        rate = settings.stars_rub_per_star or 1.7
        return max(1, math.ceil(amount_rub / rate))

    async def create_invoice(
        self, payment_id: int, amount_rub: int, description: str, telegram_id: int
    ) -> Invoice:
        """
        Ссылки на оплату нет: бот сам отправит invoice-сообщение.

        external_id формируем сами — он же уедет в invoice_payload и вернётся
        в successful_payment, обеспечивая связь платежа с нашей записью в БД.
        """
        return Invoice(
            payment_url="",
            external_id=f"stars_{payment_id}",
            is_telegram_invoice=True,
            raw={"stars": self.rub_to_stars(amount_rub), "payment_id": payment_id},
        )

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        raise NotImplementedError("Stars подтверждаются апдейтом successful_payment в боте")
