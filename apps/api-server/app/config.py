"""
Конфигурация сервиса.

Два источника настроек:
  1. Переменные окружения (.env) — секреты и адреса: токены, пароли, домены.
  2. YAML-файлы в config/ — бизнес-логика: цены, тексты, лимиты.

Такое разделение нужно, чтобы менять цены и тексты мог человек без доступа
к серверу (через git), а секреты никогда не попадали в репозиторий.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_project_root() -> Path:
    """
    Найти корень проекта — папку, в которой лежит config/tariffs.yml.

    Так код работает одинаково и при запуске из исходников, и в контейнере,
    где файлы разложены по другим путям. Переменная окружения CONFIG_DIR
    (задаётся в Dockerfile) имеет приоритет.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "tariffs.yml").exists():
            return parent
    return Path("/")


PROJECT_ROOT = _find_project_root()
CONFIG_DIR = Path(os.getenv("CONFIG_DIR") or PROJECT_ROOT / "config")


class Settings(BaseSettings):
    """Переменные окружения. Имена совпадают с ключами в .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Общее ──────────────────────────────────────────────────────────────
    env: str = "development"
    log_level: str = "INFO"
    tz: str = "Europe/Moscow"
    public_base_url: str = "http://localhost:8000"

    # ── Telegram ───────────────────────────────────────────────────────────
    # Название сервиса: попадает в заголовок подписки и отображается
    # в клиентских приложениях вместо адреса сервера
    service_name: str = "SaltVPN"

    bot_token: str = ""
    bot_username: str = "your_vpn_bot"
    admin_ids: str = ""
    support_username: str = ""
    backup_bot_username: str = ""
    news_channel_url: str = ""
    telegram_webhook_secret: str = "change_me"
    bot_mode: str = "polling"  # polling | webhook

    # ── База данных ────────────────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "vpn_admin"
    postgres_password: str = ""
    postgres_db: str = "vpn_service"

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""

    # ── Marzban ────────────────────────────────────────────────────────────
    # Панель отдаётся через сайдкар marzban-proxy: сама она слушает
    # только localhost, пока ей не дать SSL-сертификаты
    marzban_base_url: str = "http://marzban:8080"
    marzban_username: str = "admin"
    marzban_password: str = ""
    marzban_subscription_url: str = ""

    # ── Платежи ────────────────────────────────────────────────────────────
    platega_enabled: bool = False
    platega_merchant_id: str = ""
    platega_secret_key: str = ""
    platega_api_url: str = "https://app.platega.io"
    platega_webhook_secret: str = ""

    # ЮKassa — СБП для самозанятых, чек уходит в ФНС автоматически
    yookassa_enabled: bool = False
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_api_url: str = "https://api.yookassa.ru/v3"
    # Почта для чека. Пустая — чек не запрашивается, пробивать вручную
    yookassa_receipt_email: str = ""

    cryptopay_enabled: bool = False
    cryptopay_token: str = ""
    cryptopay_api_url: str = "https://pay.crypt.bot/api"
    cryptopay_asset: str = "USDT"
    cryptopay_usd_rate: float = 0.0

    stars_enabled: bool = True
    stars_rub_per_star: float = 1.7

    # ── Реквизиты владельца (для сайта и оферты) ───────────────────────────
    # Живут в .env, а не в репозитории: это персональные данные.
    # Без них ЮKassa не пропустит сайт на модерации.
    owner_full_name: str = ""
    owner_inn: str = ""
    owner_email: str = ""
    owner_phone: str = ""
    owner_address: str = ""

    # ── Безопасность ───────────────────────────────────────────────────────
    jwt_secret: str = "change_me"
    api_internal_token: str = "change_me"

    # ── Auto-Healing ───────────────────────────────────────────────────────
    health_check_interval_sec: int = 120
    health_fail_threshold: int = 3
    health_timeout_sec: int = 5
    healing_enabled: bool = True

    # ── Бэкапы ─────────────────────────────────────────────────────────────
    backup_enabled: bool = False

    # ── Локальный режим (запуск на своём компьютере, без серверов) ─────────
    # LOCAL_MODE=true: база — файл SQLite, состояния бота — в памяти,
    # Postgres, Redis и Marzban не нужны. Годится, чтобы посмотреть и
    # отладить интерфейс бота; ключи VPN при этом не выдаются.
    local_mode: bool = False
    sqlite_path: str = "vpn_local.sqlite3"

    # ── Производные значения ───────────────────────────────────────────────
    @property
    def database_url(self) -> str:
        """DSN для SQLAlchemy: SQLite в локальном режиме, иначе Postgres."""
        if self.local_mode:
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"

    @property
    def admin_id_list(self) -> list[int]:
        """ADMIN_IDS хранится строкой '123,456' — превращаем в список чисел."""
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip().isdigit()]

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    def missing_requisites(self) -> list[str]:
        """
        Чего не хватает сайту для модерации в платёжной системе.

        ЮKassa проверяет, что на сайте есть ФИО и ИНН самозанятого и
        способ связи. Пока список не пуст, страница показывает заглушку
        вместо реквизитов — лучше честный пропуск, чем выдуманные данные.
        """
        fields = {
            "OWNER_FULL_NAME": self.owner_full_name,
            "OWNER_INN": self.owner_inn,
            "OWNER_EMAIL": self.owner_email,
        }
        return sorted(name for name, value in fields.items() if not value.strip())

    def insecure_defaults(self) -> list[str]:
        """
        Секреты, оставшиеся заводскими.

        JWT_SECRET солит хеши IP, API_INTERNAL_TOKEN закрывает служебные
        ручки, TELEGRAM_WEBHOOK_SECRET отличает настоящие апдейты Telegram
        от поддельных. Все три лежат в .env.example со значением change_me,
        и забыть их заменить — значит открыть сервис постороннему.
        """
        fields = {
            "JWT_SECRET": self.jwt_secret,
            "API_INTERNAL_TOKEN": self.api_internal_token,
            "TELEGRAM_WEBHOOK_SECRET": self.telegram_webhook_secret,
        }
        return sorted(name for name, value in fields.items() if value in ("", "change_me"))


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Не найден конфиг {path}. Проверьте, что папка config/ на месте.")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Настройки окружения (кэшируются — читаются один раз за процесс)."""
    return Settings()


@functools.lru_cache(maxsize=1)
def get_tariffs() -> dict[str, Any]:
    """Содержимое config/tariffs.yml."""
    return _load_yaml("tariffs.yml")


@functools.lru_cache(maxsize=1)
def get_messages() -> dict[str, Any]:
    """Содержимое config/messages.yml."""
    return _load_yaml("messages.yml")


def reload_configs() -> None:
    """Сбросить кэш YAML — нужно, если правим тексты без перезапуска процесса."""
    get_tariffs.cache_clear()
    get_messages.cache_clear()


# ── Удобные хелперы поверх tariffs.yml ─────────────────────────────────────


def tariff_by_code(code: str) -> dict[str, Any] | None:
    for tariff in get_tariffs().get("tariffs", []):
        if tariff["code"] == code:
            return tariff
    return None


def period_by_months(months: int) -> dict[str, Any] | None:
    for period in get_tariffs().get("periods", []):
        if period["months"] == months:
            return period
    return None


def calc_price(tariff_code: str, months: int, discount_percent: int = 0) -> int:
    """
    Итоговая цена в рублях.

    tariff_code      — 'nl' | 'ru' | 'multi'
    months           — период из tariffs.yml
    discount_percent — дополнительная скидка (промокод, персональный оффер)
    """
    tariff = tariff_by_code(tariff_code)
    period = period_by_months(months)
    if not tariff or not period:
        raise ValueError(f"Неизвестный тариф или период: {tariff_code}/{months}")

    base = tariff["base_price"] * months
    after_period = base * (1 - float(period.get("discount", 0)))
    after_promo = after_period * (1 - discount_percent / 100)
    return int(round(after_promo))


def limits() -> dict[str, Any]:
    return get_tariffs().get("limits", {})


def text(key: str, **kwargs: Any) -> str:
    """
    Достать текст из messages.yml и подставить значения.

    Поддерживает вложенность через точку: text("buttons.trial").
    Если подстановки не хватает, вернём шаблон как есть, а не упадём:
    бот не должен молчать из-за опечатки в YAML.
    """
    node: Any = get_messages()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return f"[нет текста: {key}]"
        node = node[part]
    if not isinstance(node, str):
        return f"[нет текста: {key}]"
    try:
        return node.format(**kwargs).strip()
    except (KeyError, IndexError):
        return node.strip()
