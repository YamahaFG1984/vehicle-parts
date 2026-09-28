"""Stock snapshots per SKU and warehouse: manual edits and stock-file imports (e.g. ERP exports).

This is a snapshot store, not an inventory ledger: each new value supersedes the previous one
for that SKU + warehouse, and the old one stays as history.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import StockImport, StockLevel, SupplierItem
from .normalizers import normalize_header, normalize_number, parse_date, to_text

DEFAULT_WAREHOUSE = "主仓"

# Header aliases for stock files (compared without case, spaces or punctuation).
STOCK_COLUMNS = {
    "supplier": ["supplier", "supplier code", "vendor", "供应商", "供应商代码"],
    "part_no": ["part no", "part number", "part #", "sku", "supplier sku", "brand number",
                "item no", "编号", "供应商编号", "品牌号", "货号"],
    "warehouse": ["warehouse", "location", "wh", "仓库", "库位"],
    "quantity": ["quantity", "qty", "on hand", "stock", "available", "数量", "库存", "库存数量",
                 "可用库存"],
    "as_of": ["as of", "date", "stock date", "snapshot date", "日期", "截至日期", "盘点日期"],
}


def set_stock(item: SupplierItem, quantity: int, *, warehouse: str = DEFAULT_WAREHOUSE,
              as_of: date | None = None, source: str = StockLevel.Source.MANUAL, user=None,
              note: str = "", stock_import: StockImport | None = None,
              locator: str = "") -> StockLevel | None:
    """Record a new snapshot. Returns None when nothing changed (same quantity and date)."""
    if quantity is None or quantity < 0:
        raise ValidationError("库存数量必须是不小于 0 的整数")
    warehouse = (warehouse or DEFAULT_WAREHOUSE).strip()[:64]
    as_of = as_of or timezone.localdate()
    with transaction.atomic():
        current = StockLevel.objects.select_for_update().filter(
            item=item, warehouse=warehouse, is_current=True).first()
        if current and current.quantity == quantity and current.as_of == as_of:
            return None
        if current:
            current.is_current = False
            current.save(update_fields=["is_current", "modified"])
        return StockLevel.objects.create(
            item=item, warehouse=warehouse, quantity=quantity, as_of=as_of, source=source,
            note=note[:200], stock_import=stock_import, locator=locator[:300], recorded_by=user)


def total_for_items(item_ids) -> int | None:
    """Current total across SKUs and warehouses; None when no stock was ever recorded."""
    qs = StockLevel.objects.filter(item_id__in=list(item_ids), is_current=True)
    if not qs.exists():
        return None
    return qs.aggregate(total=Sum("quantity"))["total"] or 0


def totals_by_item() -> dict[int, int]:
    return dict(StockLevel.objects.filter(is_current=True).values("item_id")
                .annotate(total=Sum("quantity")).values_list("item_id", "total"))


# ---------------------------------------------------------------------------- import


class StockImportError(Exception):
    pass


@dataclass
class StockImportResult:
    stock_import: StockImport | None
    stats: Counter = field(default_factory=Counter)
    unmatched: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)


def _header_map(cells) -> dict[str, int]:
    index = {normalize_header(a): fld for fld, aliases in STOCK_COLUMNS.items() for a in aliases}
    found = {}
    for i, cell in enumerate(cells):
        fld = index.get(normalize_header(to_text(cell)))
        if fld and fld not in found:
            found[fld] = i
    return found


def import_stock(path, *, original_name: str | None = None, supplier: str = "",
                 warehouse: str = "", as_of: date | None = None, user=None) -> StockImportResult:
    """Import a stock sheet. Needs a part-number column and a quantity column; supplier,
    warehouse and date columns are optional (command-line defaults fill them in)."""
    from apps.ingestion import extractors

    path = Path(path)
    original_name = original_name or path.name
    try:
        file_type = extractors.detect_file_type(Path(original_name))
        if file_type == "pdf":
            raise extractors.ExtractionError("库存表请使用 Excel 或 CSV")
        tables = extractors.extract(path, file_type).tables
    except extractors.ExtractionError as exc:
        raise StockImportError(str(exc)) from exc

    items = SupplierItem.objects.select_related("supplier")
    by_key: dict[tuple[str, str], SupplierItem] = {}
    by_number: dict[str, list[SupplierItem]] = {}
    for it in items:
        norm = normalize_number(it.supplier_part_no)
        by_key[(it.supplier.code.upper(), norm)] = it
        by_number.setdefault(norm, []).append(it)

    rows = []
    for table in tables:
        header_at, columns = None, {}
        for i, row in enumerate(table.rows[:30]):
            cols = _header_map(row.cells)
            if {"part_no", "quantity"} <= set(cols):
                header_at, columns = i, cols
                break
        if header_at is None:
            continue
        for row in table.rows[header_at + 1:]:
            # Read each row with its own table's columns (sheets may differ).
            values = {name: to_text(row.cells[i]) if i < len(row.cells) else ""
                      for name, i in columns.items()}
            rows.append((extractors.locator_label(original_name, row.locator), values))
    if not rows:
        raise StockImportError("没有找到库存表头：需要“编号 / SKU”和“数量 / 库存”两列")

    result = StockImportResult(stock_import=None)
    with transaction.atomic():
        stock_import = StockImport.objects.create(
            original_name=original_name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            uploaded_by=user)
        result.stock_import = stock_import
        for locator, values in rows:
            def cell(name, _values=values):
                return _values.get(name, "")

            part_no = cell("part_no")
            if not part_no:
                continue
            qty_text = cell("quantity").replace(",", "")
            try:
                quantity = int(float(qty_text))
                if quantity < 0:
                    raise ValueError
            except ValueError:
                result.problems.append({"locator": locator, "message": f"数量无法识别：{qty_text!r}"})
                result.stats["invalid"] += 1
                continue
            row_date, _ = parse_date(cell("as_of"))
            code = (cell("supplier") or supplier).upper()
            norm = normalize_number(part_no)
            item = by_key.get((code, norm)) if code else None
            if item is None and not code:
                candidates = by_number.get(norm, [])
                item = candidates[0] if len(candidates) == 1 else None
                if len(candidates) > 1:
                    result.unmatched.append({"locator": locator, "part_no": part_no,
                                             "reason": "多个供应商都有此编号，请在文件中注明供应商"})
                    result.stats["unmatched"] += 1
                    continue
            if item is None:
                result.unmatched.append({"locator": locator, "part_no": part_no,
                                         "reason": "系统中没有这个供应商编号"})
                result.stats["unmatched"] += 1
                continue
            level = set_stock(item, quantity, warehouse=cell("warehouse") or warehouse,
                              as_of=row_date or as_of, source=StockLevel.Source.IMPORT,
                              user=user, stock_import=stock_import, locator=locator)
            result.stats["updated" if level else "unchanged"] += 1
        stock_import.stats = dict(result.stats)
        stock_import.report = {"unmatched": result.unmatched, "problems": result.problems}
        stock_import.save(update_fields=["stats", "report", "modified"])
    return result
