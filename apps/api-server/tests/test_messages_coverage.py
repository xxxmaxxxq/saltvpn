"""
Сверка ключей messages.yml с тем, что реально запрашивает код.

Два направления ошибки, обе тихие:
  - код просит ключ, которого нет -> text() вернёт "[нет текста: ...]"
    прямо в сообщении пользователю, тест на это не упадёт сам по себе;
  - в messages.yml остался ключ, который никто не использует -> редактор
    правит текст, который никогда не показывается (так и произошло с
    buttons.pay_crypto: у провайдера code="cryptopay", а ключ был другим).

AST, а не grep: часть вызовов text() многострочные, и текстовый поиск
пропускает такие обращения.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import CONFIG_DIR, get_messages

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Ключи, которые код строит динамически (f-строка) — AST не может подставить
# туда реальное значение, поэтому такие обращения проверяются отдельно.
# Если добавите новый динамический источник текста — впишите его сюда.
DYNAMIC_KEY_PREFIXES = (
    "buttons.",  # _btn(key) -> text(f"buttons.{key}")
    "notify_",  # send_expiry_notice(kind=...) -> text(f"notify_{kind}")
)


def _flatten(node: object, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                keys |= _flatten(value, path)
            else:
                keys.add(path)
    return keys


def _literal_text_calls() -> set[str]:
    """Все литеральные строковые ключи из вызовов text("...")."""
    used: set[str] = set()
    for py_file in APP_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "text"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                used.add(node.args[0].value)
    return used


def test_every_literal_text_key_exists_in_messages_yml() -> None:
    defined = _flatten(get_messages())
    used = _literal_text_calls()
    missing = used - defined
    assert not missing, f"text() просит ключи, которых нет в messages.yml: {sorted(missing)}"


def test_messages_yml_has_no_dead_keys() -> None:
    defined = _flatten(get_messages())
    used = _literal_text_calls()
    # Ключи под динамическими префиксами не мёртвые — их просто нельзя
    # увидеть статическим анализом текста вызова.
    candidates = {k for k in defined if not k.startswith(DYNAMIC_KEY_PREFIXES)}
    dead = candidates - used
    assert not dead, f"В messages.yml есть неиспользуемые ключи: {sorted(dead)}"


@pytest.mark.parametrize(
    "provider_code",
    ["platega", "cryptopay", "stars", "yookassa"],
)
def test_every_payment_provider_has_a_button_title(provider_code: str) -> None:
    """
    Заголовок кнопки провайдера берётся из buttons.pay_{code} в PaymentProvider.title
    (app/payments/base.py). Без соответствующего ключа кнопка покажет
    "[нет текста: ...]" вместо названия способа оплаты.
    """
    from app.config import text as get_text

    key = f"buttons.pay_{provider_code}"
    rendered = get_text(key)
    assert not rendered.startswith("[нет текста:"), f"Нет ключа {key} в messages.yml"


def test_config_dir_matches_repo_config() -> None:
    # Подстраховка от переезда файлов: если CONFIG_DIR вдруг указывает
    # не туда, оба теста выше молча читали бы пустой/чужой messages.yml.
    assert (Path(CONFIG_DIR) / "messages.yml").exists()
