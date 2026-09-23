"""Find the header row of each table and map its columns to semantic fields.

Aliases come from config/rules/column_aliases.yaml; an optional mapping file (--mapping) can
pin the sheet, the header row and specific column→field assignments for an unusual file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from apps.catalog.normalizers import normalize_header, to_text
from apps.core import rules

from .extractors import RawTable


class MappingError(Exception):
    pass


@dataclass
class MappingOverride:
    sheet: str | None = None
    header_row: int | None = None  # 1-based row number as shown in Excel / PDF table row
    columns: dict[str, str] = field(default_factory=dict)  # header text -> field
    brand: str | None = None
    sku_position_suffix: bool | None = None

    @classmethod
    def from_file(cls, path: Path) -> MappingOverride:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        known = set(rules.column_aliases()["fields"])
        bad = {k: v for k, v in (data.get("columns") or {}).items() if v not in known}
        if bad:
            raise MappingError(f"映射文件中的字段名无效：{bad}；可用字段：{sorted(known)}")
        return cls(
            sheet=data.get("sheet"),
            header_row=data.get("header_row"),
            columns={str(k): v for k, v in (data.get("columns") or {}).items()},
            brand=data.get("brand"),
            sku_position_suffix=data.get("sku_position_suffix"),
        )

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, {}, "")}


@dataclass
class TableMapping:
    table: str
    header_row_index: int  # index into table.rows
    header_locator: dict
    headers: list[str]
    columns: dict[int, str]  # column index -> field
    unmapped: list[str]
    duplicates: list[str]
    inherited: bool = False

    def as_dict(self) -> dict:
        return {
            "table": self.table,
            "header_locator": self.header_locator,
            "columns": {self.headers[i] or f"col{i + 1}": f for i, f in self.columns.items()},
            "unmapped": self.unmapped,
            "duplicates": self.duplicates,
            "inherited_header": self.inherited,
        }


def alias_index() -> dict[str, str]:
    idx = {}
    for fld, aliases in rules.column_aliases()["fields"].items():
        idx[normalize_header(fld)] = fld
        for alias in aliases:
            idx.setdefault(normalize_header(alias), fld)
    return idx


def _map_headers(headers: list[str], override: MappingOverride | None):
    aliases = alias_index()
    pinned = {normalize_header(k): v for k, v in (override.columns if override else {}).items()}
    columns: dict[int, str] = {}
    unmapped, duplicates = [], []
    for i, header in enumerate(headers):
        key = normalize_header(header)
        if not key:
            continue
        fld = pinned.get(key) or aliases.get(key)
        if not fld:
            unmapped.append(header)
        elif fld in columns.values():
            duplicates.append(header)
        else:
            columns[i] = fld
    return columns, unmapped, duplicates


def detect(table: RawTable, override: MappingOverride | None = None,
           previous: TableMapping | None = None) -> TableMapping | None:
    cfg = rules.column_aliases()
    min_matches = cfg.get("header_min_matches", 3)
    required = set(cfg.get("required_fields", []))
    scan = cfg.get("header_scan_rows", 30)

    candidates = list(enumerate(table.rows[:scan]))
    if override and override.header_row:
        candidates = [
            (i, r) for i, r in enumerate(table.rows) if r.locator.get("row") == override.header_row
        ]
    best = None
    for i, row in candidates:
        headers = [to_text(c) for c in row.cells]
        columns, unmapped, duplicates = _map_headers(headers, override)
        if len(columns) < min_matches or not required <= set(columns.values()):
            continue
        if best is None or len(columns) > len(best.columns):
            best = TableMapping(table.name, i, row.locator, headers, columns, unmapped, duplicates)
    if best:
        return best
    # PDF tables continued on the next page often have no header row: reuse the previous one.
    if previous and table.rows and len(table.rows[0].cells) == len(previous.headers):
        return TableMapping(table.name, -1, previous.header_locator, previous.headers,
                            previous.columns, previous.unmapped, previous.duplicates,
                            inherited=True)
    return None
