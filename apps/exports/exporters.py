"""xlsx exports. Every data row carries its source locator so a reader can go back to the original.

Each sheet is built by a function that takes an ExportScope: either the whole catalog, or the
products returned by a search. The full exports (master data, offers, review list, import
report) and "export these search results" are just different combinations of the same sheets,
so a searched export and a full export never disagree on columns or meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from django.db.models import Q
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.catalog.models import FieldValue, SupplierItem, SupplierOffer
from apps.catalog.normalizers import category_label
from apps.catalog.selectors import active_products, product_state, review_flags, search
from apps.ingestion.models import ImportBatch, SourceRecord
from apps.matching.models import Issue, MatchCandidate
from apps.matching.rules import FIELD_LABELS, REASON_LABELS

HEADER_FILL = PatternFill("solid", fgColor="F3F1EC")
DECISION_FILL = PatternFill("solid", fgColor="FFF4CC")
HIT_FILL = PatternFill("solid", fgColor="E1EAF6")
POSITION_LABELS = {"left": "左", "right": "右", "": ""}
OPEN_CANDIDATE = [MatchCandidate.Status.PENDING, MatchCandidate.Status.NEEDS_INFO]


# ---------------------------------------------------------------------------- scope


@dataclass
class ExportScope:
    """Which products an export covers. product_ids=None means the whole catalog."""

    product_ids: set[int] | None = None
    hit_item_ids: set[int] = field(default_factory=set)  # items that matched the query
    criteria: dict = field(default_factory=dict)  # shown on the "查询条件" sheet

    @property
    def is_full(self) -> bool:
        return self.product_ids is None

    def products(self):
        qs = active_products().order_by("code")
        return qs if self.is_full else qs.filter(pk__in=self.product_ids)

    def item_filter(self, prefix: str = "") -> Q:
        """Q restricting a queryset whose `prefix` path leads to a SupplierItem."""
        if self.is_full:
            return Q()
        return Q(**{f"{prefix}product_id__in": self.product_ids})


def scope_from_search(query: str = "", supplier: str = "", state: str = "") -> ExportScope:
    """Exactly the products the search page shows for the same criteria (without its limit)."""
    results = search(query, supplier=supplier, state=state, limit=None)
    return ExportScope(
        product_ids={r["product"].pk for r in results},
        hit_item_ids={h.pk for r in results for h in r["hits"]},
        criteria={"关键词 / 编号": query, "供应商": supplier, "分类": state},
    )


# ---------------------------------------------------------------------------- helpers


def _sheet(wb: Workbook, title: str, headers: list[str], rows, widths: dict | None = None,
           highlight: set[str] | None = None, row_fills: dict[int, PatternFill] | None = None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = DECISION_FILL if highlight and cell.value in highlight else HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append(row)
    for row_no, fill in (row_fills or {}).items():
        for cell in ws[row_no]:
            cell.fill = fill
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for idx, header in enumerate(headers, start=1):
        width = (widths or {}).get(header)
        if width is None:
            longest = max((len(str(c.value)) for c in ws[get_column_letter(idx)][:200]
                           if c.value is not None), default=8)
            width = min(max(8, longest + 2), 60)
        ws.column_dimensions[get_column_letter(idx)].width = width
    return ws


def _notes(wb: Workbook, title: str, lines: list[str]):
    ws = wb.create_sheet(title)
    for line in lines:
        ws.append([line])
    ws.column_dimensions["A"].width = 110
    return ws


def _new_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def _pos(value) -> str:
    return POSITION_LABELS.get(value, value)


def _oe(item) -> str:
    return ", ".join(pn.number_raw for pn in item.part_numbers.all() if pn.kind == "oe")


def _fmt(v) -> str:
    if isinstance(v, list):
        if v and all(isinstance(x, (int, float)) for x in v):
            return "x".join(f"{x:g}" for x in v)
        return ",".join(map(str, v)) or "（空）"
    return str(v) if v not in (None, "") else "（空）"


def _members(product) -> list[SupplierItem]:
    return sorted(product.items.all(), key=lambda m: (m.supplier.code, m.supplier_part_no))


# ---------------------------------------------------------------------------- sheets


def add_criteria_sheet(wb: Workbook, scope: ExportScope, products: list) -> None:
    rows = [[k, v or "（不限）"] for k, v in scope.criteria.items()]
    rows += [
        ["命中归一产品数", len(products)],
        ["命中条目数", len(scope.hit_item_ids)],
        ["成员条目总数", sum(len(p.items.all()) for p in products)],
        ["导出时间", timezone.localtime().strftime("%Y-%m-%d %H:%M")],
    ]
    _sheet(wb, "查询条件", ["项目", "值"], rows, widths={"项目": 18, "值": 50})
    _notes(wb, "说明", [
        "本文件是一次查询的整理结果：命中条目所属的归一产品，连同产品下的全部品牌号 / 供应商编号、各供应商报价与字段来源。",
        "“品牌号”表中蓝色行是直接命中查询条件的条目，其余是同一归一产品下的其他品牌号。",
        "“疑似重复”“异常清单”只包含与这些产品相关的记录；在黄色 decision / note 列填写后，"
        "可用 python manage.py import_review <本文件> --reviewer 姓名 回写（与待确认清单相同）。",
        "报价按原币种保存，不做汇率换算；（缺失）表示原资料中该项为空。",
    ])


def add_products_sheet(wb: Workbook, products: list, flags) -> None:
    rows = []
    for p in products:
        members = _members(p)
        state, has_open = product_state(len(members), [m.pk for m in members], flags)
        disagreements = [k for k, v in (p.attr_consensus or {}).items() if not v.get("agreed", True)]
        rows.append([
            p.code, state, "是" if has_open else "", len(members), p.category, _pos(p.position),
            p.fitment_label, p.dims_label,
            "一致" if not disagreements else
            "不一致：" + "、".join(FIELD_LABELS.get(d, d) for d in disagreements),
            "; ".join(f"{m.supplier.code}:{m.supplier_part_no}" for m in members),
            "; ".join(sorted({oe for m in members for oe in _oe(m).split(", ") if oe})),
            ", ".join(sorted({m.supplier.name for m in members})),
            sum(flags.open_issue_counts.get(m.pk, 0) for m in members),
        ])
    _sheet(wb, "归一产品", ["产品编号", "分类", "有待确认疑似", "成员数", "类别", "位置", "适配车型",
                        "尺寸(包装)", "成员属性一致性", "品牌号/供应商编号", "OE号", "供应商",
                        "未处理异常数"], rows)


def add_members_sheet(wb: Workbook, products: list, scope: ExportScope, title: str) -> None:
    rows, fills = [], {}
    show_hits = not scope.is_full
    for p in products:
        for m in _members(p):
            hit = m.pk in scope.hit_item_ids
            row = [p.code, m.supplier.code, m.supplier_part_no, m.brand, m.name,
                   category_label(m.category), _pos(m.position), m.fitment_label, m.dims_label,
                   _oe(m), "是" if m.present_in_latest else "否（最新文件未出现）", m.source_ref,
                   m.current_record.locator_label]
            if show_hits:
                row.insert(1, "命中" if hit else "")
                if hit:
                    fills[len(rows) + 2] = HIT_FILL
            rows.append(row)
    headers = ["产品编号", "供应商", "供应商编号", "品牌", "原始名称", "类别", "位置", "适配车型",
               "尺寸(包装)", "OE号", "在最新文件中", "原始记录ID", "来源定位"]
    if show_hits:
        headers.insert(1, "命中查询")
    _sheet(wb, title, headers, rows, row_fills=fills)


def add_offers_sheet(wb: Workbook, scope: ExportScope) -> None:
    rows = []
    offers = SupplierOffer.objects.filter(scope.item_filter("item__")).select_related(
        "item__supplier", "item__product", "source_record__batch").order_by(
        "item__product__code", "item__supplier__code", "item__supplier_part_no",
        "-is_current", "-quote_date")
    for o in offers:
        rows.append([
            o.item.product.code if o.item.product else "", category_label(o.item.category),
            o.item.fitment_label, o.item.supplier.code, o.item.supplier.name,
            o.item.supplier_part_no, float(o.price) if o.price is not None else "（缺失）",
            o.currency or "（缺失）", o.moq if o.moq is not None else "（缺失）",
            o.quote_date.isoformat() if o.quote_date else "（缺失）",
            "是" if o.is_current else "否（历史报价）", o.source_record.locator_label,
            o.source_record.batch_id,
        ])
    _sheet(wb, "供应商报价", ["产品编号", "类别", "适配车型", "供应商", "供应商名称", "供应商编号", "价格",
                         "币种", "MOQ", "报价日期", "当前报价", "来源定位", "导入批次"], rows)


def add_provenance_sheet(wb: Workbook, scope: ExportScope) -> None:
    values = FieldValue.objects.filter(scope.item_filter("item__")).select_related(
        "item__supplier", "item__product", "source_record__batch__source_file"
    ).order_by("item__product__code", "item__supplier__code", "item__supplier_part_no",
               "field", "-is_current", "-created")
    rows = [
        [fv.item.product.code if fv.item.product else "", fv.item.supplier.code,
         fv.item.supplier_part_no, fv.field, fv.raw_value,
         "" if fv.normalized_value is None else str(fv.normalized_value),
         fv.source_record.batch.source_file.original_name, fv.source_record.locator_label,
         fv.source_column, "是" if fv.is_current else "否（历史值）", fv.source_record.batch_id]
        for fv in values
    ]
    _sheet(wb, "字段来源", ["产品编号", "供应商", "供应商编号", "字段", "原始值", "归一值", "来源文件",
                        "来源定位", "来源列", "当前值", "导入批次"], rows)


def _item_brief(item: SupplierItem) -> list:
    return [f"{item.supplier.code}:{item.supplier_part_no}", item.name,
            item.product.code if item.product else "", item.current_record.locator_label]


def add_review_sheets(wb: Workbook, scope: ExportScope) -> None:
    """疑似重复 + 异常清单 with the decision / note columns that import_review reads back."""
    from apps.matching.selectors import review_groups

    cands = MatchCandidate.objects.filter(status__in=OPEN_CANDIDATE)
    if not scope.is_full:
        cands = cands.filter(scope.item_filter("item_a__") | scope.item_filter("item_b__"))
    cands = cands.select_related(
        "item_a__supplier", "item_a__product", "item_a__current_record",
        "item_b__supplier", "item_b__product", "item_b__current_record")
    # Same grouping as the web review queue, so related pairs can be decided together offline.
    group_of = {c.pk: n for n, g in enumerate(review_groups(), start=1) for c in g.candidates}
    rows = []
    for c in sorted(cands, key=lambda c: (group_of.get(c.pk, 0), c.priority, -c.confidence, c.pk)):
        conflicts = "；".join(
            f"{FIELD_LABELS.get(x['field'], x['field'])}: {_fmt(x['a'])} vs {_fmt(x['b'])}"
            + ("" if x.get("hard") else "（软）") for x in c.conflicts)
        rows.append([
            c.pk, group_of.get(c.pk, ""), c.priority,
            "；".join(REASON_LABELS.get(r, r) for r in c.reasons), c.confidence,
            *_item_brief(c.item_a), *_item_brief(c.item_b), conflicts or "—",
            "、".join(FIELD_LABELS.get(m, m) for m in c.missing) or "—", c.suggested_action,
            c.get_status_display(), "", "",
        ])
    _sheet(wb, "疑似重复", [
        "candidate_id", "疑似组", "优先级", "触发原因", "置信度",
        "A 编号", "A 名称", "A 产品", "A 来源", "B 编号", "B 名称", "B 产品", "B 来源",
        "冲突字段", "缺失信息", "建议动作", "当前状态", "decision", "note",
    ], rows, widths={"建议动作": 70, "冲突字段": 40, "触发原因": 30},
        highlight={"decision", "note"})

    issues = Issue.objects.filter(status=Issue.Status.OPEN)
    if not scope.is_full:  # file-level problems (no item) only belong to the full list
        issues = issues.filter(scope.item_filter("item__"))
    issues = issues.select_related(
        "item__supplier", "item__product", "item__current_record", "source_record",
        "batch__source_file")
    severity_order = {"error": 0, "warning": 1, "info": 2}
    issue_rows = []
    for i in sorted(issues, key=lambda x: (severity_order[x.severity], x.code, x.pk)):
        locator = (i.source_record.locator_label if i.source_record else
                   (i.item.current_record.locator_label if i.item else
                    f"{i.batch.source_file.original_name} {i.details.get('locator', '')}"
                    if i.batch else ""))
        issue_rows.append([
            i.pk, i.get_severity_display(), i.code, i.message,
            i.item.supplier.code if i.item else "", i.item.supplier_part_no if i.item else "",
            i.item.product.code if i.item and i.item.product else "", i.field, locator, "", "",
        ])
    _sheet(wb, "异常清单", ["issue_id", "级别", "代码", "说明", "供应商", "供应商编号", "产品编号", "字段",
                        "来源定位", "decision", "note"], issue_rows,
           widths={"说明": 60}, highlight={"decision", "note"})


REVIEW_NOTES = [
    "在黄色的 decision / note 列填写后，运行：python manage.py import_review <本文件> --reviewer 姓名",
    "疑似重复 decision：merge（确认合并）/ reject（不是同一产品）/ needs_info（待补充资料）；留空表示不处理。",
    "“疑似组”相同的行相互关联（同一批相似记录），建议一起对照判断。",
    "存在硬冲突的候选若选择 merge，必须在 note 说明理由。",
    "异常清单 decision：resolve（已解决）/ ignore（忽略）。",
    "任一行填写有误时整份文件不会写入，并逐行提示错误。",
]


# ---------------------------------------------------------------------------- workbooks


def export_master(path, scope: ExportScope | None = None):
    scope = scope or ExportScope()
    products = list(scope.products())
    wb = _new_workbook()
    add_products_sheet(wb, products, review_flags())
    add_members_sheet(wb, products, scope, "成员条目")
    add_provenance_sheet(wb, scope)
    wb.save(path)
    return path


def export_offers(path, scope: ExportScope | None = None):
    scope = scope or ExportScope()
    wb = _new_workbook()
    add_offers_sheet(wb, scope)
    _notes(wb, "说明", [
        "同一归一产品下，不同供应商的报价逐行分别保留；同一供应商的历史报价也保留（当前报价=否）。",
        "价格与币种按原文件保存，不做汇率换算；（缺失）表示原资料中该项为空。",
    ])
    wb.save(path)
    return path


def export_review(path, scope: ExportScope | None = None):
    scope = scope or ExportScope()
    wb = _new_workbook()
    add_review_sheets(wb, scope)
    _notes(wb, "填写说明", REVIEW_NOTES)
    wb.save(path)
    return path


def export_search(path, scope: ExportScope):
    """整理结果 for one query: products with all brand numbers, offers, provenance, and the
    related review items, in one workbook."""
    products = list(scope.products())
    wb = _new_workbook()
    add_criteria_sheet(wb, scope, products)
    add_products_sheet(wb, products, review_flags())
    add_members_sheet(wb, products, scope, "品牌号")
    add_offers_sheet(wb, scope)
    add_provenance_sheet(wb, scope)
    add_review_sheets(wb, scope)
    _notes(wb, "填写说明", REVIEW_NOTES)
    wb.save(path)
    return path


def export_import_report(path, scope: ExportScope | None = None):
    """Import batches are about files, not products, so this report is always complete."""
    batches = ImportBatch.objects.select_related("source_file__supplier").order_by("pk")
    keys = ["rows", "new", "updated", "conflict", "unchanged", "duplicate", "invalid",
            "not_in_latest", "problems"]
    batch_rows = [[
        b.pk, b.source_file.original_name, b.source_file.supplier.code, b.get_status_display(),
        b.created.strftime("%Y-%m-%d %H:%M"), *[b.stats.get(k, 0) for k in keys],
        ", ".join(b.report.get("unmapped_columns", [])), b.ruleset_version,
        b.source_file.sha256[:16],
    ] for b in batches]
    wb = _new_workbook()
    _sheet(wb, "批次", ["批次", "文件", "供应商", "状态", "时间", "行数", "新增", "更新", "冲突", "未变",
                      "文件内重复", "无法识别", "本次未出现", "文件级问题", "未识别列", "规则版本",
                      "sha256(前16位)"], batch_rows)
    key_changes = {
        i.source_record_id: i.message for i in Issue.objects.filter(code="KEY_ATTR_CHANGED")
    }
    codes: dict[int, list[str]] = {}
    for rec_id, code in Issue.objects.filter(source_record__isnull=False).values_list(
            "source_record_id", "code"):
        codes.setdefault(rec_id, []).append(code)
    rec_rows = [[
        r.batch_id, r.locator_label, r.supplier_part_no, r.get_diff_status_display(),
        key_changes.get(r.pk, ""), ", ".join(sorted(codes.get(r.pk, []))),
    ] for r in SourceRecord.objects.order_by("batch_id", "pk")]
    _sheet(wb, "记录明细", ["批次", "来源定位", "供应商编号", "状态", "关键属性变化", "异常代码"], rec_rows,
           widths={"关键属性变化": 60})
    missing_rows = [[b.pk, b.source_file.supplier.code, pn] for b in batches
                    for pn in b.report.get("not_in_latest", [])]
    _sheet(wb, "本次未出现", ["批次", "供应商", "供应商编号"], missing_rows)
    problem_rows = [[i.batch_id, i.code, i.message, str(i.details.get("locator", ""))]
                    for i in Issue.objects.filter(source_record__isnull=True, batch__isnull=False,
                                                  code="UNPARSEABLE")]
    _sheet(wb, "文件级问题", ["批次", "代码", "说明", "定位"], problem_rows)
    wb.save(path)
    return path


# Full-catalog exports (dashboard buttons, `export_data`, docs/samples).
EXPORTS = {
    "master": ("master_data.xlsx", export_master),
    "offers": ("supplier_offers.xlsx", export_offers),
    "review": ("review_list.xlsx", export_review),
    "report": ("import_report.xlsx", export_import_report),
}


def export_all(out_dir: Path, only: list[str] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [fn(out_dir / name) for key, (name, fn) in EXPORTS.items() if not only or key in only]


def search_filename(criteria: dict) -> str:
    """查询结果_<条件>_<时间>.xlsx, keeping only characters safe in file names."""
    label = "_".join(v for v in criteria.values() if v) or "全部"
    label = re.sub(r"[^\w一-鿿.-]+", "-", label).strip("-")[:40] or "查询"
    return f"查询结果_{label}_{timezone.localtime():%Y%m%d-%H%M}.xlsx"
