"""Тесты бизнес-логики подписок: streak и пауза."""

from datetime import timedelta

from app.models import Subscription, SubscriptionStatus, utcnow
from app.services.subscriptions import calc_streak_bonus, can_pause, pause_days_left


def make_sub(**kwargs) -> Subscription:
    defaults = {
        "user_id": 1,
        "tariff_code": "multi",
        "status": SubscriptionStatus.ACTIVE,
        "expires_at": utcnow() + timedelta(days=10),
        "is_trial": False,
        "device_limit": 2,
        "streak_count": 0,
        "pause_days_used": 0,
        "pause_year": utcnow().year,
    }
    defaults.update(kwargs)
    return Subscription(**defaults)


def test_first_purchase_after_trial_opens_streak():
    sub = make_sub(is_trial=True, status=SubscriptionStatus.TRIAL)
    streak, bonus = calc_streak_bonus(sub)
    assert streak == 1
    assert bonus == 0


def test_renewal_before_expiry_grows_streak():
    sub = make_sub(streak_count=3)
    streak, bonus = calc_streak_bonus(sub)
    assert streak == 4
    assert bonus == 4


def test_streak_bonus_is_capped():
    sub = make_sub(streak_count=20)
    _, bonus = calc_streak_bonus(sub)
    assert bonus == 7


def test_late_renewal_resets_streak():
    sub = make_sub(streak_count=5, expires_at=utcnow() - timedelta(days=3))
    streak, bonus = calc_streak_bonus(sub)
    assert streak == 1
    assert bonus == 0


def test_renewal_within_grace_keeps_streak():
    sub = make_sub(streak_count=2, expires_at=utcnow() - timedelta(hours=5))
    streak, _ = calc_streak_bonus(sub)
    assert streak == 3


def test_pause_not_allowed_on_trial():
    sub = make_sub(is_trial=True, status=SubscriptionStatus.TRIAL)
    ok, reason = can_pause(sub, 7)
    assert ok is False
    assert "бесплатном" in reason


def test_pause_limited_by_yearly_quota():
    sub = make_sub(pause_days_used=28)
    assert pause_days_left(sub) == 2
    ok, _ = can_pause(sub, 7)
    assert ok is False
    ok, _ = can_pause(sub, 2)
    assert ok is True


def test_pause_quota_resets_next_year():
    sub = make_sub(pause_days_used=30, pause_year=utcnow().year - 1)
    assert pause_days_left(sub) == 30


def test_cannot_pause_twice():
    sub = make_sub(status=SubscriptionStatus.PAUSED)
    ok, reason = can_pause(sub, 5)
    assert ok is False
    assert "заморожена" in reason
