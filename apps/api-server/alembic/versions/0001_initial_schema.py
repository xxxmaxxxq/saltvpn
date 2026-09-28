"""Начальная схема: пользователи, подписки, платежи, ноды, рефералы

Revision ID: 0001
Revises:
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("first_name", sa.String(128)),
        sa.Column("language_code", sa.String(8)),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("trial_used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("referral_code", sa.String(16), nullable=False),
        sa.Column("referred_by_id", sa.BigInteger()),
        sa.Column("device_slots_bonus", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("marzban_username", sa.String(64)),
        sa.Column("subscription_token", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["referred_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("telegram_id"),
        sa.UniqueConstraint("referral_code"),
        sa.UniqueConstraint("marzban_username"),
        sa.UniqueConstraint("subscription_token"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])
    op.create_index("ix_users_referral_code", "users", ["referral_code"])
    op.create_index("ix_users_referred_by_id", "users", ["referred_by_id"])
    op.create_index("ix_users_subscription_token", "users", ["subscription_token"])

    # ── subscriptions ──────────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tariff_code", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="trial", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_trial", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("device_limit", sa.SmallInteger(), server_default="2", nullable=False),
        sa.Column("streak_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("pause_days_used", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("pause_year", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True)),
        sa.Column("traffic_used_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_status_expires", "subscriptions", ["status", "expires_at"])

    # ── payments ───────────────────────────────────────────────────────────
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(128)),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(8), server_default="RUB", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("tariff_code", sa.String(32), nullable=False),
        sa.Column("period_months", sa.SmallInteger(), nullable=False),
        sa.Column("days_granted", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("promo_code", sa.String(32)),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider", "external_id", name="uq_payments_provider_external"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])

    # ── nodes ──────────────────────────────────────────────────────────────
    op.create_table(
        "nodes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("location", sa.String(16), nullable=False),
        sa.Column("host", sa.String(128), nullable=False),
        sa.Column("port", sa.Integer(), server_default="443", nullable=False),
        sa.Column("marzban_node_id", sa.Integer()),
        sa.Column("status", sa.String(16), server_default="healthy", nullable=False),
        sa.Column("fail_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("current_sni", sa.String(128)),
        sa.Column("sni_pool", postgresql.JSONB()),
        sa.Column("backup_ports", postgresql.JSONB()),
        sa.Column("users_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_nodes_location", "nodes", ["location"])

    # ── health_events ──────────────────────────────────────────────────────
    op.create_table(
        "health_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.BigInteger(), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64)),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_health_events_node_id", "health_events", ["node_id"])

    # ── referrals ──────────────────────────────────────────────────────────
    op.create_table(
        "referrals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("referrer_id", sa.BigInteger(), nullable=False),
        sa.Column("referred_id", sa.BigInteger(), nullable=False),
        sa.Column("is_qualified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True)),
        sa.Column("reward_granted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referred_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("referred_id"),
    )
    op.create_index("ix_referrals_referrer_id", "referrals", ["referrer_id"])

    # ── notifications ──────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("subscription_id", "kind", name="uq_notifications_sub_kind"),
    )
    op.create_index("ix_notifications_subscription_id", "notifications", ["subscription_id"])

    # ── pause_periods ──────────────────────────────────────────────────────
    op.create_table(
        "pause_periods",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("days_requested", sa.SmallInteger(), nullable=False),
        sa.Column("days_actual", sa.SmallInteger()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_pause_periods_subscription_id", "pause_periods", ["subscription_id"])

    # ── promo_codes ────────────────────────────────────────────────────────
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("discount_percent", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("bonus_days", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("is_personal_for_user_id", sa.BigInteger()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["is_personal_for_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"])
    op.create_index("ix_promo_codes_personal", "promo_codes", ["is_personal_for_user_id"])


def downgrade() -> None:
    op.drop_table("promo_codes")
    op.drop_table("pause_periods")
    op.drop_table("notifications")
    op.drop_table("referrals")
    op.drop_table("health_events")
    op.drop_table("nodes")
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("users")
