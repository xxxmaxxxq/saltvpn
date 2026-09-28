"""
Публичный сайт: витрина и оферта.

Страница существует ради модерации в платёжной системе, поэтому тесты
проверяют ровно то, что там смотрят: настоящие услуги с фиксированными
ценами, как покупатель получит заказ после оплаты, оферта, контакты
с ИНН самозанятого.

Отдельно закреплено, что цены на сайте считаются тем же calc_price,
что и в боте. Разойдись они — покупатель увидел бы на витрине одну
сумму, а списалась бы другая.
"""

import pytest

from app.config import calc_price, get_settings
from app.site import landing_html, offer_html


@pytest.fixture
def filled(monkeypatch):
    """Настройки с заполненными реквизитами."""
    settings = get_settings()
    monkeypatch.setattr(settings, "owner_full_name", "Иванов Иван Иванович")
    monkeypatch.setattr(settings, "owner_inn", "123456789012")
    monkeypatch.setattr(settings, "owner_email", "mail@example.com")
    monkeypatch.setattr(settings, "owner_phone", "+7 900 000-00-00")
    return settings


# ── Требования модерации ───────────────────────────────────────────────────


def test_landing_shows_every_tariff_price(filled):
    """Цены на витрине — те же, что спишет бот."""
    page = landing_html()
    for code in ("nl", "ru", "multi"):
        assert f"{calc_price(code, 1)}&nbsp;₽" in page
        assert f"{calc_price(code, 12)}&nbsp;₽" in page


def test_landing_explains_how_to_get_access(filled):
    """Для цифрового товара модерация требует описать получение заказа."""
    page = landing_html()
    assert "после оплаты" in page
    assert "Happ" in page


def test_landing_has_requisites(filled):
    page = landing_html()
    assert "Иванов Иван Иванович" in page
    assert "123456789012" in page
    assert "mail@example.com" in page


def test_inn_is_on_every_page(filled):
    """ЮKassa просит ссылку на страницу с реквизитами — подойдёт любая."""
    assert "123456789012" in landing_html()
    assert "123456789012" in offer_html()


def test_offer_covers_refunds_and_subject(filled):
    page = offer_html()
    assert "Возврат" in page
    assert "Предмет договора" in page
    assert "Реквизиты" in page


def test_pages_link_to_each_other(filled):
    assert 'href="/offer"' in landing_html()
    assert 'href="/"' in offer_html()


# ── Поведение без реквизитов ───────────────────────────────────────────────


def test_missing_requisites_are_listed(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "owner_full_name", "")
    monkeypatch.setattr(settings, "owner_inn", "")
    monkeypatch.setattr(settings, "owner_email", "")
    assert settings.missing_requisites() == ["OWNER_EMAIL", "OWNER_FULL_NAME", "OWNER_INN"]


def test_page_says_what_is_missing_instead_of_inventing(monkeypatch):
    """Лучше честная заглушка, чем выдуманные ФИО и ИНН на публичной странице."""
    settings = get_settings()
    monkeypatch.setattr(settings, "owner_full_name", "")
    monkeypatch.setattr(settings, "owner_inn", "")
    monkeypatch.setattr(settings, "owner_email", "")
    page = landing_html()
    assert "OWNER_INN" in page
    assert "Реквизиты не заполнены" in page


def test_whitespace_only_counts_as_missing(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "owner_inn", "   ")
    assert "OWNER_INN" in settings.missing_requisites()


# ── Безопасность вывода ────────────────────────────────────────────────────


def test_requisites_are_escaped(monkeypatch):
    """Реквизиты приходят из .env и попадают в HTML — экранируем."""
    settings = get_settings()
    monkeypatch.setattr(settings, "owner_full_name", "<script>alert(1)</script>")
    monkeypatch.setattr(settings, "owner_inn", "123456789012")
    monkeypatch.setattr(settings, "owner_email", "mail@example.com")
    page = landing_html()
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_pages_are_valid_html(filled):
    for page in (landing_html(), offer_html()):
        assert page.startswith("<!DOCTYPE html>")
        assert page.rstrip().endswith("</html>")
        assert page.count("<body>") == 1
        # Плейсхолдеры шаблона не должны утечь на страницу
        assert "{" not in page.split("<style>")[0]
