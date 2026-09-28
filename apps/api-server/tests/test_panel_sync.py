"""
Сверка срока подписки с панелью.

Оплата и запись в панель — два разных шага. Если второй не прошёл
(панель лежала), пользователь заплатил, дни у нас начислены, а доступа
нет. Ссылка-подписка обязана это заметить и починить.
"""

from datetime import timedelta
from types import SimpleNamespace

from app.models import utcnow
from app.services.subscriptions import panel_is_stale


def _subscription(days: int = 30):
    return SimpleNamespace(expires_at=utcnow() + timedelta(days=days))


def test_missing_account_is_stale():
    assert panel_is_stale(None, _subscription()) is True


def test_account_without_expire_is_stale():
    # Бессрочный аккаунт в панели при срочной подписке у нас — расхождение
    assert panel_is_stale({"expire": 0}, _subscription()) is True


def test_matching_expire_is_not_stale():
    sub = _subscription()
    panel = {"expire": int(sub.expires_at.timestamp())}
    assert panel_is_stale(panel, sub) is False


def test_rounding_within_a_minute_is_not_stale():
    sub = _subscription()
    panel = {"expire": int(sub.expires_at.timestamp()) - 30}
    assert panel_is_stale(panel, sub) is False


def test_unpaid_renewal_is_stale():
    # Пользователь оплатил месяц, а в панели остался вчерашний срок
    sub = _subscription(days=30)
    panel = {"expire": int((utcnow() - timedelta(days=1)).timestamp())}
    assert panel_is_stale(panel, sub) is True
