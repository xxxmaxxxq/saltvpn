"""
Подпись профиля, которую видит пользователь в клиенте (Happ, Hiddify).

Раньше в remark подставлялся {USERNAME} — Marzban заменял его на логин
аккаунта (u<telegram_id>), и чужой Telegram ID светился на экране.
Тест закрепляет, что подпись — это флаг и страна, без имени аккаунта.
"""

from app.cli import LOCATION_LABELS, location_label


def test_labels_show_country_flags():
    assert location_label("nl") == "🇳🇱 Нидерланды"
    assert location_label("ru") == "🇷🇺 Россия"


def test_label_is_case_insensitive():
    assert location_label("NL") == location_label("nl")


def test_unknown_location_falls_back_to_code():
    assert location_label("de") == "🌍 DE"


def test_no_label_leaks_account_name():
    # {USERNAME} Marzban подменяет логином аккаунта — его в подписи быть не должно
    for label in LOCATION_LABELS.values():
        assert "USERNAME" not in label
