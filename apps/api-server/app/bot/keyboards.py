"""
Клавиатуры бота.

Правило интерфейса: на каждом экране не больше 4-5 кнопок, главное действие —
первым и крупным. Тексты кнопок берутся из config/messages.yml.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import get_settings, text
from app.utils import deeplink
from app.utils.deeplink import APP_STORES

settings = get_settings()


def _btn(key: str) -> str:
    return text(f"buttons.{key}")


# ── Онбординг ──────────────────────────────────────────────────────────────


def main_menu_button() -> InlineKeyboardButton:
    """
    Кнопка возврата в начало.

    Ставится последней строкой на всех вложенных экранах: у конкурентов
    (Quattro, Atom) это главный способ не потеряться в переписке.
    """
    return InlineKeyboardButton(text=_btn("main_menu"), callback_data="menu:main")


def main_menu(
    has_subscription: bool = False, is_trial_available: bool = False
) -> InlineKeyboardMarkup:
    """
    Главное меню — единый экран с разделами.

    Разделы те же, что в синем меню «Меню» рядом с полем ввода, чтобы
    человек находил нужное любым привычным способом. Ряды задаются явно:
    состав меню меняется (у новичка есть кнопка триала, у подписчика —
    продление), и автоматическая разбивка в этом случае ломает раскладку.
    """
    kb = InlineKeyboardBuilder()

    if is_trial_available:
        kb.row(InlineKeyboardButton(text=_btn("trial"), callback_data="trial:activate"))

    kb.row(
        InlineKeyboardButton(text=_btn("account"), callback_data="account:show"),
        InlineKeyboardButton(
            text=_btn("extend") if has_subscription else _btn("buy"),
            callback_data="buy:menu",
        ),
    )
    kb.row(
        InlineKeyboardButton(text=_btn("connect"), callback_data="connect:show"),
        InlineKeyboardButton(text=_btn("referral"), callback_data="ref:show"),
    )
    kb.row(
        InlineKeyboardButton(text=_btn("instruction"), callback_data="connect:help"),
        InlineKeyboardButton(text=_btn("support"), callback_data="support:contact"),
    )

    if _https_ready():
        kb.row(
            InlineKeyboardButton(
                text=_btn("miniapp"),
                web_app=WebAppInfo(url=f"{settings.public_base_url.rstrip('/')}/app"),
            )
        )
    return kb.as_markup()


def start_new_user() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=_btn("trial"), callback_data="trial:activate")
    kb.button(text=_btn("buy"), callback_data="buy:menu")
    kb.adjust(1)
    return kb.as_markup()


def start_returning_user() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=_btn("account"), callback_data="account:show")
    kb.button(text=_btn("extend"), callback_data="buy:menu")
    kb.button(text=_btn("connect"), callback_data="connect:show")
    kb.button(text=_btn("referral"), callback_data="ref:show")
    kb.adjust(2, 2)
    return kb.as_markup()


def _https_ready() -> bool:
    """
    Можно ли ставить в кнопку ссылку на наш домен.

    Telegram принимает в inline-кнопках только полноценные http(s)-адреса,
    поэтому при локальном запуске (http://localhost) кнопку-ссылку не
    показываем — вместо неё бот отдаёт ссылку текстом.
    """
    return settings.public_base_url.startswith("https://")


def connect_keyboard(subscription_url: str) -> InlineKeyboardMarkup:
    """
    Кнопки подключения.

    Первая — импорт в приложение: одно нажатие, и подписка уже в Happ.
    Именно эта кнопка снимает главный барьер онбординга.
    """
    kb = InlineKeyboardBuilder()
    if _https_ready():
        kb.row(
            InlineKeyboardButton(text=_btn("connect"), url=deeplink.import_page(subscription_url))
        )
    kb.row(InlineKeyboardButton(text=_btn("other_app"), callback_data="connect:apps"))
    kb.row(InlineKeyboardButton(text=_btn("instruction"), callback_data="connect:help"))
    kb.row(InlineKeyboardButton(text=_btn("copy_link"), callback_data="connect:link"))
    kb.row(main_menu_button())
    return kb.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    """Экран помощи: ссылки на приложения и возврат в меню."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📱 Happ для iPhone", url=APP_STORES["happ_ios"]))
    kb.row(InlineKeyboardButton(text="🤖 Happ для Android", url=APP_STORES["happ_android"]))
    kb.row(
        InlineKeyboardButton(
            text="💻 Hiddify для Windows и macOS", url=APP_STORES["hiddify_desktop"]
        )
    )
    kb.row(InlineKeyboardButton(text=_btn("not_working"), callback_data="heal:start"))
    kb.row(main_menu_button())
    return kb.as_markup()


def other_apps_keyboard(subscription_url: str) -> InlineKeyboardMarkup:
    """Все приложения ведут на ту же страницу импорта — она сама их различает."""
    page = deeplink.import_page(subscription_url)
    kb = InlineKeyboardBuilder()
    if _https_ready():
        kb.row(InlineKeyboardButton(text="Hiddify", url=f"{page}?app=hiddify"))
        kb.row(InlineKeyboardButton(text="v2RayTun", url=f"{page}?app=v2raytun"))
        kb.row(InlineKeyboardButton(text="Streisand (iOS)", url=f"{page}?app=streisand"))
    kb.row(InlineKeyboardButton(text=_btn("back"), callback_data="connect:show"))
    return kb.as_markup()


# ── Кабинет ────────────────────────────────────────────────────────────────


def account_keyboard(
    is_paused: bool = False, has_subscription: bool = True
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=_btn("extend"), callback_data="buy:menu")
    if has_subscription:
        kb.button(text=_btn("connect"), callback_data="connect:show")
        if is_paused:
            kb.button(text=_btn("resume"), callback_data="pause:resume")
        else:
            kb.button(text=_btn("pause"), callback_data="pause:menu")
        kb.button(text=_btn("not_working"), callback_data="heal:start")
    kb.button(text=_btn("referral"), callback_data="ref:show")
    if settings.public_base_url.startswith("https://"):
        kb.row(
            InlineKeyboardButton(
                text=_btn("miniapp"),
                web_app=WebAppInfo(url=f"{settings.public_base_url.rstrip('/')}/app"),
            )
        )
    kb.row(main_menu_button())
    kb.adjust(2, 2, 1)
    return kb.as_markup()


# ── Покупка ────────────────────────────────────────────────────────────────


def tariffs_keyboard(showcase: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tariff in showcase:
        price = tariff["periods"][0]["price"]
        badge = f" · {tariff['badge']}" if tariff.get("badge") else ""
        kb.button(
            text=f"{tariff['title']} — от {price} ₽{badge}",
            callback_data=f"buy:tariff:{tariff['code']}",
        )
    kb.button(text=_btn("main_menu"), callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def periods_keyboard(tariff: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for period in tariff["periods"]:
        badge = f" ({period['badge']})" if period.get("badge") else ""
        label = f"{period['title']} — {period['price']} ₽{badge}"
        kb.button(
            text=label,
            callback_data=f"buy:period:{tariff['code']}:{period['months']}",
        )
    kb.button(text=_btn("back"), callback_data="buy:menu")
    kb.adjust(1)
    return kb.as_markup()


def providers_keyboard(providers: list, tariff_code: str, months: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for provider in providers:
        kb.button(
            text=provider.title,
            callback_data=f"buy:pay:{provider.code}:{tariff_code}:{months}",
        )
    kb.button(text=_btn("back"), callback_data=f"buy:tariff:{tariff_code}")
    kb.adjust(1)
    return kb.as_markup()


def payment_link_keyboard(url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💰 Перейти к оплате", url=url))
    kb.row(InlineKeyboardButton(text=_btn("back"), callback_data="buy:menu"))
    return kb.as_markup()


# ── Пауза ──────────────────────────────────────────────────────────────────


def pause_days_keyboard(max_days: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for days in (7, 14, 30):
        if days <= max_days:
            kb.button(text=f"{days} дней", callback_data=f"pause:set:{days}")
    kb.button(text=_btn("back"), callback_data="account:show")
    kb.adjust(3, 1)
    return kb.as_markup()


# ── Поддержка и лечение ────────────────────────────────────────────────────


def healing_result_keyboard(subscription_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if _https_ready():
        kb.row(
            InlineKeyboardButton(
                text=_btn("refresh_sub"), url=deeplink.import_page(subscription_url)
            )
        )
    kb.row(InlineKeyboardButton(text=_btn("support"), callback_data="support:contact"))
    kb.row(main_menu_button())
    return kb.as_markup()


def support_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if settings.support_username:
        kb.row(
            InlineKeyboardButton(
                text="💬 Написать в поддержку",
                url=f"https://t.me/{settings.support_username}",
            )
        )
    if settings.news_channel_url:
        kb.row(InlineKeyboardButton(text="📢 Канал сервиса", url=settings.news_channel_url))
    kb.row(main_menu_button())
    return kb.as_markup()


# ── Уведомления ────────────────────────────────────────────────────────────


def renew_keyboard(promo_code: str | None = None) -> InlineKeyboardMarkup:
    """Кнопка из уведомления о скором окончании."""
    kb = InlineKeyboardBuilder()
    callback = f"buy:menu:{promo_code}" if promo_code else "buy:menu"
    kb.button(text=_btn("extend"), callback_data=callback)
    kb.adjust(1)
    return kb.as_markup()


def referral_keyboard(link: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    share_text = "Держи VPN, который просто работает. Первые 3 дня бесплатно:"
    kb.row(
        InlineKeyboardButton(
            text="📤 Поделиться ссылкой",
            url=f"https://t.me/share/url?url={link}&text={share_text}",
        )
    )
    kb.row(main_menu_button())
    return kb.as_markup()
