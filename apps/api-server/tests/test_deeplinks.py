"""Тесты deep links и конвертации в Telegram Stars."""

from app.payments.stars import StarsProvider
from app.utils.deeplink import all_links, happ, hiddify

SUB_URL = "https://vpn.example.com/sub/abc123"


def test_happ_deeplink_uses_add_not_import():
    """
    У Happ действие называется add. На happ://import/... приложение
    отвечает «Неизвестное действие deepLink» — это уже ловили вживую.
    """
    assert happ(SUB_URL) == f"happ://add/{SUB_URL}"
    assert "import" not in happ(SUB_URL)


def test_happ_base64_fallback_decodes_to_same_url():
    import base64

    from app.utils.deeplink import happ_base64

    encoded = happ_base64(SUB_URL).removeprefix("happ://add/")
    assert base64.b64decode(encoded).decode() == SUB_URL


def test_hiddify_deeplink_is_encoded():
    link = hiddify(SUB_URL)
    assert link.startswith("hiddify://import/")
    assert "https%3A%2F%2F" in link


def test_all_links_contain_supported_apps():
    links = all_links(SUB_URL)
    assert set(links) == {"happ", "happ_base64", "hiddify", "v2raytun", "streisand"}


def test_stars_conversion_rounds_up():
    # 249 руб при курсе 1.7 -> 147 звёзд (округление вверх)
    assert StarsProvider.rub_to_stars(249) == 147
    assert StarsProvider.rub_to_stars(1) == 1


def test_stars_never_zero():
    assert StarsProvider.rub_to_stars(0) >= 1


def test_import_page_replaces_sub_path():
    """Кнопка Telegram должна вести на https-страницу, а не на happ://."""
    from app.utils.deeplink import import_page

    assert import_page(SUB_URL) == "https://vpn.example.com/i/abc123"


def test_import_page_replaces_only_first_occurrence():
    url = "https://vpn.example.com/sub/tok_sub_1"
    from app.utils.deeplink import import_page

    assert import_page(url) == "https://vpn.example.com/i/tok_sub_1"


def test_headers_are_latin1_safe():
    """
    Заголовки HTTP кодируются в latin-1: кириллица в них роняет ответ.
    Поэтому и название сервиса, и текст объявления уходят в base64.
    """
    import base64

    for text in ("SaltVPN", "Подписка закончилась. Продлите её в боте."):
        encoded = "base64:" + base64.b64encode(text.encode()).decode()
        encoded.encode("latin-1")  # не должно бросать
        assert base64.b64decode(encoded.removeprefix("base64:")).decode() == text
