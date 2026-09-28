"""
Криптографические проверки: Telegram Mini App и вебхуки платёжек.

Важно: без этих проверок любой человек сможет отправить нам поддельный
«успешный платёж» и получить подписку бесплатно. Поэтому ни один вебхук
не обрабатывается до проверки подписи.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from urllib.parse import parse_qsl


def generate_token(length: int = 32) -> str:
    """Криптостойкий токен (для ссылки-подписки)."""
    return secrets.token_urlsafe(length)[:length]


def generate_referral_code() -> str:
    """Короткий человекочитаемый код для реферальной ссылки."""
    return secrets.token_hex(4)


# ── Telegram Mini App ──────────────────────────────────────────────────────


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_sec: int = 86400) -> dict:
    """
    Проверить initData из Telegram.WebApp и вернуть распакованные данные.

    Схема Telegram: secret = HMAC_SHA256("WebAppData", bot_token),
    затем сверяем HMAC от строки вида "key=value\\n..." (отсортированной).

    Бросает ValueError, если подпись неверна или данные протухли.
    """
    if not init_data:
        raise ValueError("initData пустой")

    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("В initData нет hash")

    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("Подпись initData неверна")

    # Защита от переигрывания старого initData
    auth_date = int(parsed.get("auth_date", 0))
    if max_age_sec:
        import time

        if time.time() - auth_date > max_age_sec:
            raise ValueError("initData устарел")

    if "user" in parsed:
        parsed["user"] = json.loads(parsed["user"])
    return parsed


# ── Вебхуки платёжных провайдеров ──────────────────────────────────────────


def verify_hmac_sha256(body: bytes, signature: str, secret: str) -> bool:
    """Общая проверка HMAC-SHA256 (Platega и подобные)."""
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def verify_cryptopay_signature(body: bytes, signature: str, token: str) -> bool:
    """
    CryptoPay подписывает тело ключом SHA256(токен приложения).

    Заголовок: crypto-pay-api-signature
    """
    if not signature or not token:
        return False
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def hash_ip(ip: str, salt: str) -> str:
    """
    Хэш IP для антифрода триала.

    Сам IP не храним: нужен только факт «с этого адреса триал уже брали».
    """
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]
