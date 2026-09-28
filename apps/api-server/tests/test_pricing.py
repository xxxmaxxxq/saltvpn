"""Тесты расчёта цен: скидки по периодам и промокодам."""

from app.config import calc_price, get_tariffs, tariff_by_code


def test_tariffs_loaded():
    tariffs = get_tariffs()
    codes = {t["code"] for t in tariffs["tariffs"]}
    assert codes == {"nl", "ru", "multi"}


def test_base_price_one_month():
    assert calc_price("nl", 1) == 149
    assert calc_price("ru", 1) == 199
    assert calc_price("multi", 1) == 249


def test_period_discount_applied():
    # 3 месяца = -10%: 149 * 3 * 0.9 = 402.3 -> 402
    assert calc_price("nl", 3) == 402
    # 12 месяцев = -35%: 149 * 12 * 0.65 = 1162.2 -> 1162
    assert calc_price("nl", 12) == 1162


def test_longer_period_is_cheaper_per_month():
    """Годовой тариф обязан быть выгоднее помесячного — иначе он бессмысленен."""
    for code in ("nl", "ru", "multi"):
        month_price = calc_price(code, 1)
        year_per_month = calc_price(code, 12) / 12
        assert year_per_month < month_price


def test_promo_discount_stacks_on_period_discount():
    base = calc_price("multi", 3)
    with_promo = calc_price("multi", 3, discount_percent=40)
    assert with_promo < base
    assert with_promo == round(base * 0.6)


def test_unknown_tariff_raises():
    import pytest

    with pytest.raises(ValueError):
        calc_price("unknown", 1)


def test_multi_includes_both_locations():
    multi = tariff_by_code("multi")
    assert set(multi["locations"]) == {"nl", "ru"}
