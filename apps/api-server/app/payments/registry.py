"""Реестр платёжных провайдеров: получение по коду и список включённых."""

from __future__ import annotations

from app.payments.base import PaymentProvider
from app.payments.cryptopay import CryptoPayProvider
from app.payments.platega import PlategaProvider
from app.payments.stars import StarsProvider
from app.payments.yookassa import YooKassaProvider

_PROVIDERS: dict[str, PaymentProvider] = {
    YooKassaProvider.code: YooKassaProvider(),
    PlategaProvider.code: PlategaProvider(),
    CryptoPayProvider.code: CryptoPayProvider(),
    StarsProvider.code: StarsProvider(),
}


def get_provider(code: str) -> PaymentProvider:
    provider = _PROVIDERS.get(code)
    if provider is None:
        raise KeyError(f"Неизвестный платёжный провайдер: {code}")
    return provider


def enabled_providers() -> list[PaymentProvider]:
    """Провайдеры, у которых заполнены ключи в .env — их и показываем в боте."""
    return [p for p in _PROVIDERS.values() if p.enabled]
