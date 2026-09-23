"""Loads the tunable YAML rule files from settings.RULES_DIR (cached; call reload() after edits)."""

from functools import cache

import yaml
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

REQUIRED_KEYS = {
    "synonyms.yaml": ["categories", "positions", "makes"],
    "column_aliases.yaml": ["fields", "header_min_matches", "required_fields"],
    "matching.yaml": ["version", "weights", "dims_tolerance_cm", "auto_confirm", "blocking"],
}


@cache
def load(name: str) -> dict:
    path = settings.RULES_DIR / name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ImproperlyConfigured(f"规则文件不存在：{path}") from exc
    missing = [k for k in REQUIRED_KEYS.get(name, []) if k not in data]
    if missing:
        raise ImproperlyConfigured(f"规则文件 {path} 缺少配置项：{', '.join(missing)}")
    return data


def reload() -> None:
    load.cache_clear()


def synonyms() -> dict:
    return load("synonyms.yaml")


def column_aliases() -> dict:
    return load("column_aliases.yaml")


def matching() -> dict:
    return load("matching.yaml")
