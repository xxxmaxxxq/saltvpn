"""
PyYAML при повторе ключа в мэппинге молча оставляет последнее значение —
ни исключения, ни предупреждения. Для messages.yml и tariffs.yml это значит,
что цена или текст могут "сами по себе" перестать совпадать с тем, что
редактор видит выше в файле. Обычный yaml.safe_load такую ошибку не покажет,
поэтому здесь свой загрузчик, который считает повторы ключей на любом
уровне вложенности.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import CONFIG_DIR


class _DuplicateKeyError(ValueError):
    pass


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping_no_dups(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise _DuplicateKeyError(
                f"повторяющийся ключ '{key}' на строке {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_dups
)


@pytest.mark.parametrize("filename", ["messages.yml", "tariffs.yml"])
def test_yaml_config_has_no_duplicate_keys(filename: str) -> None:
    path = Path(CONFIG_DIR) / filename
    text = path.read_text(encoding="utf-8")
    try:
        yaml.load(text, Loader=_StrictLoader)
    except _DuplicateKeyError as exc:
        pytest.fail(f"{filename}: {exc}")
