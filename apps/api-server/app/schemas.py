"""Pydantic-схемы запросов и ответов Mini App API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    tariff_code: str
    period_months: int = Field(ge=1, le=12)
    provider: str
    promo_code: str | None = None


class OrderResponse(BaseModel):
    payment_url: str
    payment_id: int
    amount: int
    is_telegram_invoice: bool = False


class PauseRequest(BaseModel):
    days: int = Field(ge=1, le=30)


class SubscriptionInfo(BaseModel):
    tariff: str
    tariff_title: str
    status: str
    expires_at: datetime | None
    days_left: int
    device_limit: int
    devices_online: int
    traffic_used_gb: float
    streak_count: int
    next_streak_bonus_days: int
    pause_days_left: int
    paused_until: datetime | None
    is_trial: bool


class UserInfo(BaseModel):
    telegram_id: int
    first_name: str | None
    referral_code: str


class ReferralInfo(BaseModel):
    total: int
    qualified: int
    slots_earned: int
    next_slot_in: int
    per_slot: int
    link: str


class MeResponse(BaseModel):
    user: UserInfo
    subscription: SubscriptionInfo | None
    subscription_url: str
    # https-адрес страницы импорта: Telegram.WebApp.openLink умеет только
    # http(s), поэтому кнопка в Mini App ведёт сюда, а не на happ://
    import_url: str
    deeplinks: dict[str, str]
    referrals: ReferralInfo
    personal_offer: dict | None = None
