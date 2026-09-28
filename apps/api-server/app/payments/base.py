"""
Общий интерфейс платёжных провайдеров.

Зачем абстракция: платёжки для VPN-сервисов живут недолго — шлюз может
закрыться, поднять комиссию или начать блокировать тематику. Единый интерфейс
позволяет добавить нового провайдера (Lava, Tribute, YooMoney) одним файлом,
не трогая ни бота, ни биллинг.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import text


@dataclass(slots=True)
class Invoice:
    """Созданный счёт: ссылка на оплату + идентификатор у провайдера."""

    payment_url: str
    external_id: str
    # Для Telegram Stars ссылки нет — счёт отправляется сообщением в чат
    is_telegram_invoice: bool = False
    raw: dict | None = None


@dataclass(slots=True)
class WebhookResult:
    """Разобранный вебхук."""

    external_id: str
    is_paid: bool
    amount: float | None = None
    currency: str | None = None
    our_payment_id: int | None = None
    raw: dict | None = None


class PaymentProvider(ABC):
    """Базовый класс. Каждый провайдер реализует три метода."""

    code: str = "base"

    @property
    def title(self) -> str:
        """
        Текст кнопки берётся из messages.yml (buttons.pay_{code}), а не
        хардкодится в классе — иначе правку текста нельзя было бы сделать
        без деплоя кода, а именно ради этого messages.yml вообще завели.
        """
        return text(f"buttons.pay_{self.code}")

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Включён ли провайдер в .env и заполнены ли его ключи."""

    @abstractmethod
    async def create_invoice(
        self,
        payment_id: int,
        amount_rub: int,
        description: str,
        telegram_id: int,
    ) -> Invoice:
        """Создать счёт и вернуть ссылку на оплату."""

    @abstractmethod
    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        """
        Проверить подпись и разобрать вебхук.

        Бросает ValueError, если подпись неверна — обработчик вернёт 401
        и НЕ начислит дни.
        """

    async def confirm_payment(self, external_id: str) -> bool | None:
        """
        Переспросить у провайдера, действительно ли платёж оплачен.

        Нужно там, где вебхук не подписан: ЮKassa, например, шлёт обычный
        POST без HMAC, и проверка по списку адресов — единственная защита
        на входе. Адрес можно подделать, сумму в теле — тоже, поэтому
        перед начислением дней спрашиваем саму платёжку по её API.

        Возвращает:
          True  — платёж подтверждён, начисляем;
          False — провайдер не подтвердил, НЕ начисляем;
          None  — провайдеру эта проверка не нужна (подпись уже всё решила).

        По умолчанию None: провайдеры с честной подписью ничего не
        переспрашивают и лишнего запроса не делают.
        """
        return None
