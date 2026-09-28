"""
Модели базы данных (SQLAlchemy 2.0).

Подробное описание полей — в docs/DB_SCHEMA.md.
Правило по деньгам: суммы храним в Numeric (не float!), время — в UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

# На проде — JSONB (быстрее и индексируется), при локальном запуске на SQLite —
# обычный JSON. Один и тот же код работает в обоих случаях.
JSONType = JSON().with_variant(JSONB(), "postgresql")

# Первичный ключ: BIGINT на Postgres. SQLite умеет автоинкремент только для
# INTEGER PRIMARY KEY, поэтому там используется INTEGER — на объёмы локального
# режима этого с запасом достаточно.
PkType = BigInteger().with_variant(Integer(), "sqlite")


class UtcDateTime(TypeDecorator):
    """
    Дата и время, всегда с таймзоной UTC.

    Зачем: Postgres в timestamptz таймзону хранит, а SQLite — нет, и при
    локальном запуске из базы возвращалось «наивное» время. Сравнение такого
    времени с now(UTC) роняет код (TypeError). Этот тип гарантирует, что из
    базы всегда приходит время с таймзоной, в какой бы СУБД оно ни лежало.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def utcnow() -> datetime:
    """Текущее время в UTC с таймзоной (наивные datetime — источник багов)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ── Перечисления ───────────────────────────────────────────────────────────


class SubscriptionStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class NodeStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class NotificationKind(StrEnum):
    TRIAL_OFFER = "trial_offer"
    EXPIRE_3D = "expire_3d"
    EXPIRE_1D = "expire_1d"
    EXPIRE_2H = "expire_2h"
    EXPIRED = "expired"
    WINBACK = "winback"
    HEALED = "healed"


# ── Пользователи ───────────────────────────────────────────────────────────


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(8))

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Рефералы
    referral_code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Слоты устройств, заработанные приглашениями (прибавляются к device_limit)
    device_slots_bonus: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    # Marzban
    marzban_username: Mapped[str | None] = mapped_column(String(64), unique=True)
    subscription_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    subscription: Mapped[Subscription | None] = relationship(
        back_populates="user", uselist=False, lazy="selectin"
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="user", lazy="noload")

    def __repr__(self) -> str:
        return f"<User tg={self.telegram_id}>"


# ── Подписки ───────────────────────────────────────────────────────────────


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_status_expires", "status", "expires_at"),)

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    tariff_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=SubscriptionStatus.TRIAL, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    device_limit: Mapped[int] = mapped_column(SmallInteger, default=2, nullable=False)

    # Streak: серия продлений без разрыва
    streak_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    # Пауза
    pause_days_used: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    pause_year: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paused_until: Mapped[datetime | None] = mapped_column(UtcDateTime)

    traffic_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="subscription", lazy="selectin")

    @property
    def is_active(self) -> bool:
        return self.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE) and (
            self.expires_at > utcnow()
        )

    @property
    def days_left(self) -> int:
        delta = self.expires_at - utcnow()
        return max(0, delta.days + (1 if delta.seconds > 0 else 0))


# ── Платежи ────────────────────────────────────────────────────────────────


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (
        # Главная защита от двойного начисления при повторном вебхуке
        UniqueConstraint("provider", "external_id", name="uq_payments_provider_external"),
    )

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.PENDING, nullable=False)

    tariff_code: Mapped[str] = mapped_column(String(32), nullable=False)
    period_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    days_granted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    promo_code: Mapped[str | None] = mapped_column(String(32))

    payload: Mapped[dict | None] = mapped_column(JSONType)
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    user: Mapped[User] = relationship(back_populates="payments", lazy="selectin")


# ── Ноды ───────────────────────────────────────────────────────────────────


class Node(Base, TimestampMixin):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    location: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    marzban_node_id: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), default=NodeStatus.HEALTHY, nullable=False)
    fail_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    current_sni: Mapped[str | None] = mapped_column(String(128))
    sni_pool: Mapped[list | None] = mapped_column(JSONType)
    backup_ports: Mapped[list | None] = mapped_column(JSONType)

    users_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_check_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)


class HealthEvent(Base):
    __tablename__ = "health_events"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("nodes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )


# ── Рефералы ───────────────────────────────────────────────────────────────


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Один пользователь может быть приглашён только один раз
    referred_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    is_qualified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    qualified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    reward_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )


# ── Уведомления ────────────────────────────────────────────────────────────


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Каждый триггер уходит пользователю ровно один раз
        UniqueConstraint("subscription_id", "kind", name="uq_notifications_sub_kind"),
    )

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )


# ── Паузы ──────────────────────────────────────────────────────────────────


class PausePeriod(Base):
    __tablename__ = "pause_periods"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    planned_until: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    days_requested: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    days_actual: Mapped[int | None] = mapped_column(SmallInteger)


# ── Промокоды ──────────────────────────────────────────────────────────────


class PromoCode(Base, TimestampMixin):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    discount_percent: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    bonus_days: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Персональный оффер (например, таймер-скидка в конце триала)
    is_personal_for_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def is_usable(self) -> bool:
        if not self.is_active:
            return False
        if self.valid_until and self.valid_until < utcnow():
            return False
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        return True
