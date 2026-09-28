"""Тесты проверки подписей — защита от поддельных «оплат»."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.utils.security import (
    hash_ip,
    verify_cryptopay_signature,
    verify_hmac_sha256,
    verify_telegram_init_data,
)

BOT_TOKEN = "123456:TEST-TOKEN"


def build_init_data(user_id: int = 1, auth_date: int | None = None) -> str:
    payload = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    check_string = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def test_valid_init_data_accepted():
    data = verify_telegram_init_data(build_init_data(42), BOT_TOKEN)
    assert data["user"]["id"] == 42


def test_tampered_init_data_rejected():
    init_data = build_init_data(42).replace("first_name", "first_nome")
    with pytest.raises(ValueError):
        verify_telegram_init_data(init_data, BOT_TOKEN)


def test_init_data_with_wrong_token_rejected():
    with pytest.raises(ValueError):
        verify_telegram_init_data(build_init_data(42), "999:WRONG")


def test_expired_init_data_rejected():
    old = int(time.time()) - 90000
    with pytest.raises(ValueError):
        verify_telegram_init_data(build_init_data(42, auth_date=old), BOT_TOKEN)


def test_platega_signature():
    body = b'{"id":"pay_1","status":"CONFIRMED"}'
    secret = "webhook-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hmac_sha256(body, signature, secret) is True
    assert verify_hmac_sha256(body, "deadbeef", secret) is False
    assert verify_hmac_sha256(body, signature, "other-secret") is False


def test_cryptopay_signature():
    body = b'{"update_type":"invoice_paid"}'
    token = "app-token"
    secret = hashlib.sha256(token.encode()).digest()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert verify_cryptopay_signature(body, signature, token) is True
    assert verify_cryptopay_signature(body, signature, "wrong-token") is False


def test_empty_signature_rejected():
    assert verify_hmac_sha256(b"{}", "", "secret") is False


def test_ip_hash_is_stable_and_not_reversible():
    a = hash_ip("1.2.3.4", "salt")
    assert a == hash_ip("1.2.3.4", "salt")
    assert a != hash_ip("1.2.3.5", "salt")
    assert "1.2.3.4" not in a


# ── Заводские секреты ──────────────────────────────────────────────────────


def test_insecure_defaults_flags_change_me(monkeypatch):
    """С заводскими значениями сервис обязан сообщить о всех трёх ключах."""
    from app.config import Settings

    settings = Settings(
        bot_token="123:TEST",
        jwt_secret="change_me",
        api_internal_token="change_me",
        telegram_webhook_secret="change_me",
    )
    assert settings.insecure_defaults() == [
        "API_INTERNAL_TOKEN",
        "JWT_SECRET",
        "TELEGRAM_WEBHOOK_SECRET",
    ]


def test_insecure_defaults_empty_when_replaced():
    from app.config import Settings

    settings = Settings(
        bot_token="123:TEST",
        jwt_secret="a" * 64,
        api_internal_token="b" * 64,
        telegram_webhook_secret="c" * 64,
    )
    assert settings.insecure_defaults() == []


def test_empty_secret_counts_as_insecure():
    """Пустая строка опаснее change_me: пустой заголовок сошёлся бы с ней."""
    from app.config import Settings

    settings = Settings(bot_token="123:TEST", telegram_webhook_secret="")
    assert "TELEGRAM_WEBHOOK_SECRET" in settings.insecure_defaults()
