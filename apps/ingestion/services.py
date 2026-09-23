"""Import pipeline: archive → extract → map → normalize → diff → persist (one atomic batch)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from apps.catalog import services as catalog
from apps.catalog.models import SupplierItem
from apps.catalog.normalizers import (
    Finding,
    normalize_header,
    normalize_number,
    normalize_record,
    to_text,
)
from apps.core import rules
from apps.matching import issues as issue_service

from . import extractors, mapping
from .models import ImportBatch, SourceFile, SourceRecord, Supplier

logger = logging.getLogger(__name__)

DIFF = SourceRecord.DiffStatus


class ImportFailed(Exception):
    pass


@dataclass
class ParsedRow:
    table: str
    locator: dict
    label: str
    raw_data: dict
    mapped: dict
    row_hash: str


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    mappings: list[dict] = field(default_factory=list)
    skipped_tables: list[str] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)  # first rows of tables without a header


@dataclass
class ImportResult:
    batch: ImportBatch | None
    duplicate: bool = False
    dry_run: bool = False
    parse: ParseResult | None = None
    stats: dict = field(default_factory=dict)
    preview: list[dict] = field(default_factory=list)
    already_imported: bool = False  # dry run: same content was already imported successfully


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_supplier_code(path: Path) -> str | None:
    m = re.search(r"供应商\s*([A-Za-z0-9]+)", path.name) or re.search(
        r"supplier[\s_-]*([A-Za-z0-9]+)", path.name, re.I)
    return m.group(1).upper() if m else None


def parse_file(path: Path, file_type: str, override: mapping.MappingOverride | None,
               display_name: str | None = None) -> ParseResult:
    result = ParseResult()
    display_name = display_name or path.name
    extracted = extractors.extract(path, file_type)
    result.problems.extend(extracted.problems)
    previous = None
    for table in extracted.tables:
        if override and override.sheet and table.kind == "sheet" and table.name != override.sheet:
            continue
        tm = mapping.detect(table, override, previous if table.kind == "pdf" else None)
        if tm is None:
            if table.rows:
                result.skipped_tables.append(table.name)
                result.unmatched.append({"table": table.name, "rows": [
                    {"locator": r.locator, "cells": [to_text(c) for c in r.cells]}
                    for r in table.rows[:8]]})
                result.problems.append({
                    "locator": table.rows[0].locator,
                    "message": f"表 {table.name} 未识别到表头（需包含供应商编号列且至少 "
                               f"{rules.column_aliases()['header_min_matches']} 个已知列）",
                })
            continue
        previous = tm
        result.mappings.append(tm.as_dict())
        header_key = [normalize_header(h) for h in tm.headers]
        for row in table.rows[tm.header_row_index + 1:]:
            texts = [to_text(c) for c in row.cells]
            if [normalize_header(t) for t in texts] == header_key:
                continue  # header repeated on a later page
            # Ordered [header, value] pairs: jsonb would otherwise reorder the columns.
            raw_data = [
                [tm.headers[i] if i < len(tm.headers) and tm.headers[i] else f"col{i + 1}", text]
                for i, text in enumerate(texts)
            ]
            mapped = {
                fld: {"value": texts[i] if i < len(texts) else "", "column": tm.headers[i]}
                for i, fld in tm.columns.items()
            }
            row_hash = hashlib.sha256(
                json.dumps(raw_data, ensure_ascii=False).encode()
            ).hexdigest()
            result.rows.append(ParsedRow(
                table=table.name, locator=row.locator,
                label=extractors.locator_label(display_name, row.locator),
                raw_data=raw_data, mapped=mapped, row_hash=row_hash,
            ))
    return result


NO_ROWS_HINT = (
    "请先用 --dry-run（网页为“仅预览”）查看表头识别结果，然后在 config/rules/column_aliases.yaml "
    "补充列别名，或用 --mapping 指定表头行与列映射（网页预览页可直接调整映射）后重新导入。"
)


def _has_successful_batch(source_file: SourceFile) -> bool:
    return any(b.status == ImportBatch.Status.SUCCEEDED and b.stats.get("rows", 0) > 0
               for b in source_file.batches.all())


def import_source(path, supplier_code: str | None = None, *, supplier_name: str | None = None,
                  mapping_path=None, override: mapping.MappingOverride | None = None,
                  partial: bool = False, dry_run: bool = False, reprocess: bool = False,
                  user=None, original_name: str | None = None) -> ImportResult:
    """Import one file. A file whose content was already imported successfully is a no-op
    (duplicate) unless reprocess=True; a file that never produced rows can always be retried,
    e.g. after fixing column aliases, and reuses the archived original."""
    path = Path(path)
    if not path.exists():
        raise ImportFailed(f"文件不存在：{path}")
    original_name = original_name or path.name
    try:
        file_type = extractors.detect_file_type(Path(original_name))
    except extractors.ExtractionError as exc:
        raise ImportFailed(str(exc)) from exc
    supplier_code = (supplier_code or guess_supplier_code(Path(original_name)) or "").upper()
    if not supplier_code:
        raise ImportFailed("无法从文件名推断供应商，请用 --supplier 指定")
    if mapping_path:
        try:
            override = mapping.MappingOverride.from_file(mapping_path)
        except mapping.MappingError as exc:
            raise ImportFailed(str(exc)) from exc
    digest = sha256_of(path)

    existing = SourceFile.objects.filter(sha256=digest).first()
    if existing and not dry_run:
        if existing.supplier.code != supplier_code:
            raise ImportFailed(
                f"该文件（sha256 {digest[:12]}…）已作为供应商 {existing.supplier.code} 导入过")
    if existing and not dry_run and not reprocess and _has_successful_batch(existing):
        batch = ImportBatch.objects.create(
            source_file=existing, status=ImportBatch.Status.DUPLICATE, created_by=user,
            finished_at=timezone.now(), ruleset_version=rules.matching()["version"],
            stats={"duplicate_of": existing.batches.order_by("created").first().pk},
        )
        return ImportResult(batch=batch, duplicate=True)

    try:
        parsed = parse_file(path, file_type, override, original_name)
    except (extractors.ExtractionError, mapping.MappingError) as exc:
        raise ImportFailed(str(exc)) from exc
    if dry_run:
        use_suffix = (override.sku_position_suffix if override and override.sku_position_suffix
                      is not None else None)
        normalized = [(row, normalize_record(_values(row), use_suffix)) for row in parsed.rows]
        result = _preview(supplier_code, parsed, normalized, existing)
        result.already_imported = bool(existing and _has_successful_batch(existing))
        return result
    if not parsed.rows:
        details = "；".join(p["message"] for p in parsed.problems) or "文件中没有数据行"
        raise ImportFailed(f"没有识别出任何可导入的数据行（{details}）。{NO_ROWS_HINT}")
    if not any("supplier_part_no" in m["columns"].values() for m in parsed.mappings):
        raise ImportFailed(f"没有任何一列被映射为供应商编号（supplier_part_no），无法跟踪条目。{NO_ROWS_HINT}")

    use_suffix = (override.sku_position_suffix if override and override.sku_position_suffix
                  is not None else None)
    normalized = [(row, normalize_record(_values(row), use_suffix)) for row in parsed.rows]

    with transaction.atomic():
        supplier, _ = Supplier.objects.get_or_create(
            code=supplier_code, defaults={"name": supplier_name or f"供应商{supplier_code}"})
        if supplier_name and supplier.name != supplier_name:
            supplier.name = supplier_name
            supplier.save(update_fields=["name", "modified"])
        new_file = existing is None
        source_file = existing or SourceFile(
            supplier=supplier, original_name=original_name, sha256=digest,
            size=path.stat().st_size, file_type=file_type, uploaded_by=user)
        if new_file:
            with path.open("rb") as fh:
                source_file.file.save(original_name, File(fh), save=False)
        try:
            if new_file:
                source_file.save()
            batch = ImportBatch.objects.create(
                source_file=source_file, partial=partial, created_by=user,
                ruleset_version=rules.matching()["version"],
                mapping={"tables": parsed.mappings,
                         "override": override.as_dict() if override else None,
                         "reprocessed": not new_file},
            )
            stats = _persist(batch, supplier, parsed, normalized, override, partial)
        except Exception:
            if new_file:
                source_file.file.delete(save=False)  # don't leave an orphan copy behind
            raise
    logger.info("import batch %s: %s", batch.pk, stats)
    return ImportResult(batch=batch, parse=parsed, stats=stats)


def _same_observation(item, row: ParsedRow) -> bool:
    """Unchanged = same raw row AND same column mapping. A corrected mapping (e.g. a price
    column that was missed before) must be re-applied even though the raw row is identical."""
    current = item.current_record
    return current.row_hash == row.row_hash and current.mapped == row.mapped


def _values(row: ParsedRow) -> dict:
    return {fld: entry["value"] for fld, entry in row.mapped.items()}


def _preview(supplier_code, parsed, normalized, existing_file) -> ImportResult:
    known = {normalize_number(i.supplier_part_no): i for i in
             SupplierItem.objects.filter(supplier__code=supplier_code)}
    preview, counts, seen = [], Counter(), set()
    for row, rec in normalized:
        key = normalize_number(rec.supplier_part_no)
        item = known.get(key)
        if not key:
            status = DIFF.INVALID
        elif key in seen:
            status = DIFF.DUPLICATE
        elif item is None:
            status = DIFF.NEW
        elif _same_observation(item, row):
            status = DIFF.UNCHANGED
        else:
            status = DIFF.CONFLICT if catalog.changed_key_attrs(item, rec) else DIFF.UPDATED
        seen.add(key)
        counts[status.value] += 1
        preview.append({"locator": row.label, "part_no": rec.supplier_part_no, "status": status,
                        "category": rec.category, "position": rec.position,
                        "fitment": rec.fitment.label if rec.fitment else None,
                        "dims": rec.dims, "findings": [f.code for f in rec.findings]})
    stats = dict(counts)
    if existing_file:
        stats["already_imported_as"] = existing_file.original_name
    return ImportResult(batch=None, dry_run=True, parse=parsed, stats=stats, preview=preview)


def _persist(batch, supplier, parsed, normalized, override, partial) -> dict:
    counts = Counter()
    brand_default = (override.brand if override and override.brand else "")
    # Identity is the normalized part number, so "A-03-L" and "A03L" are the same listing.
    items = {normalize_number(i.supplier_part_no): i for i in
             SupplierItem.objects.filter(supplier=supplier).select_related("current_record")}
    seen: set[str] = set()
    for problem in parsed.problems:
        issue_service.open_batch_problem(batch, problem)
        counts["problems"] += 1

    for row, rec in normalized:
        part_no = rec.supplier_part_no
        key = normalize_number(part_no)
        item = items.get(key)
        changed = {}
        if not key:
            status = DIFF.INVALID
        elif key in seen:
            status = DIFF.DUPLICATE
        elif item is None:
            status = DIFF.NEW
        elif _same_observation(item, row):
            status = DIFF.UNCHANGED
        else:
            changed = catalog.changed_key_attrs(item, rec)
            dims_change = catalog.changed_dims(item, rec)
            status = DIFF.CONFLICT if changed else DIFF.UPDATED
        counts[status.value] += 1

        record = SourceRecord.objects.create(
            batch=batch, supplier=supplier, locator=row.locator, locator_label=row.label,
            source_ref=rec.source_ref, supplier_part_no=part_no, raw_data=row.raw_data,
            mapped=row.mapped, row_hash=row.row_hash, diff_status=status,
        )
        brand = (row.mapped.get("brand") or {}).get("value") or brand_default

        if status == DIFF.INVALID:
            issue_service.record_findings(
                [f for f in rec.findings if f.code == "MISSING_PART_NO"], record=record,
                batch=batch)
            continue
        if status == DIFF.DUPLICATE:
            issue_service.record_findings([Finding(
                "DUPLICATE_KEY_IN_FILE", "error", "supplier_part_no",
                f"编号 {part_no} 在同一文件中重复出现，以第一次出现的行为准",
                {"first": items[key].current_record.locator_label})],
                item=items[key], record=record, batch=batch)
            continue
        seen.add(key)

        if status == DIFF.NEW:
            item = catalog.create_item(supplier, record, rec, brand)
            items[key] = item
            issue_service.record_findings(rec.findings, item=item, record=record, batch=batch)
        elif status == DIFF.UNCHANGED:
            if not item.present_in_latest:
                issue_service.resolve_item_issues(
                    item, codes=["NOT_IN_LATEST"], note=f"批次 #{batch.pk} 中再次出现")
            item.current_record = record
            item.present_in_latest = True
            item.save(update_fields=["current_record", "present_in_latest", "modified"])
        else:
            old_part_no = item.supplier_part_no
            if dims_change and not changed:
                issue_service.record_findings([Finding(
                    "PACKAGE_DIMS_CHANGED", "info", "package_dims",
                    f"包装尺寸变化：{dims_change['old']} → {dims_change['new']}"
                    "（包装尺寸是辅助证据，按更新处理，不影响已归一关系）",
                    {"changes": {"dims": dims_change}})], item=item, record=record, batch=batch)
            catalog.update_item(item, record, rec, brand)
            issue_service.resolve_item_issues(
                item, exclude_record=record, note=f"被批次 #{batch.pk} 的新资料取代")
            issue_service.record_findings(rec.findings, item=item, record=record, batch=batch)
            if changed:
                issue_service.record_findings([Finding(
                    "KEY_ATTR_CHANGED", "warning", "key_attrs",
                    "关键属性变化：" + "；".join(
                        f"{k}: {v['old']} → {v['new']}" for k, v in changed.items()),
                    {"changes": changed})], item=item, record=record, batch=batch)
            if old_part_no != part_no:
                issue_service.record_findings([Finding(
                    "PART_NO_FORMAT_CHANGED", "info", "supplier_part_no",
                    f"编号写法由 {old_part_no} 变为 {part_no}，按同一条目处理（旧写法保留在历史中）",
                    {"old": old_part_no, "new": part_no})], item=item, record=record, batch=batch)

    missing = []
    if not partial:
        for key, item in items.items():
            if key not in seen and item.present_in_latest:
                part_no = item.supplier_part_no
                item.present_in_latest = False
                item.save(update_fields=["present_in_latest", "modified"])
                issue_service.open_item_issue(
                    item, "NOT_IN_LATEST", "info",
                    f"{part_no} 在最新文件 {batch.source_file.original_name} 中未出现（未删除）",
                    batch=batch)
                missing.append(part_no)
    counts["not_in_latest"] = len(missing)

    stats = {k: v for k, v in counts.items()}
    stats["rows"] = len(normalized)
    batch.stats = stats
    batch.report = {
        "unmapped_columns": sorted({c for m in parsed.mappings for c in m["unmapped"]}),
        "skipped_tables": parsed.skipped_tables,
        "not_in_latest": missing,
    }
    batch.status = ImportBatch.Status.SUCCEEDED
    batch.finished_at = timezone.now()
    batch.save()
    return stats
