"""
Deep links для импорта подписки в клиентские приложения.

Смысл фишки: пользователь не копирует длинную ссылку руками, а нажимает кнопку —
телефон сам открывает приложение и добавляет подписку. Это снимает главную
точку отвала в онбординге.
"""

from __future__ import annotations

import base64
from urllib.parse import quote

# Ссылки на установку приложений (показываем, если deep link не сработал)
APP_STORES = {
    "happ_ios": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
    "happ_android": "https://play.google.com/store/apps/details?id=com.happproxy",
    "hiddify_desktop": "https://github.com/hiddify/hiddify-next/releases/latest",
    "v2raytun_ios": "https://apps.apple.com/app/v2raytun/id6476628951",
    "streisand_ios": "https://apps.apple.com/app/streisand/id6450534064",
}


def happ(subscription_url: str) -> str:
    """
    Happ — основное рекомендуемое приложение (iOS/Android/Windows/macOS).

    Важно: у Happ действие называется add, а не import. На happ://import/...
    приложение отвечает «Неизвестное действие deepLink» — схема import занята
    у него под другое. Формат: happ://add/<url или base64>.
    """
    return f"happ://add/{subscription_url}"


def happ_base64(subscription_url: str) -> str:
    """
    Тот же импорт, но ссылка в base64.

    Запасной вариант: часть сборок Happ разбирает голый URL в пути хуже,
    чем base64, особенно когда в ссылке есть параметры запроса.
    """
    encoded = base64.b64encode(subscription_url.encode()).decode()
    return f"happ://add/{encoded}"


def hiddify(subscription_url: str) -> str:
    """Hiddify — кроссплатформенная альтернатива, хороша на десктопе."""
    return f"hiddify://import/{quote(subscription_url, safe='')}"


def v2raytun(subscription_url: str) -> str:
    return f"v2raytun://import/{subscription_url}"


def streisand(subscription_url: str) -> str:
    return f"streisand://import/{quote(subscription_url, safe='')}"


def all_links(subscription_url: str) -> dict[str, str]:
    """Все поддерживаемые deep links разом — удобно отдавать в Mini App."""
    return {
        "happ": happ(subscription_url),
        "happ_base64": happ_base64(subscription_url),
        "hiddify": hiddify(subscription_url),
        "v2raytun": v2raytun(subscription_url),
        "streisand": streisand(subscription_url),
    }


def import_page(subscription_url: str) -> str:
    """
    https-адрес страницы импорта для кнопки в Telegram.

    Зачем нужен: Telegram разрешает в inline-кнопках только http/https,
    поэтому ссылку вида happ://import/... в кнопку поставить нельзя.
    Кнопка ведёт на нашу страницу, а она уже открывает приложение
    и показывает запасные варианты, если оно не установлено.
    """
    return subscription_url.replace("/sub/", "/i/", 1)
