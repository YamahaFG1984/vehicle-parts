"""xlsx exports. Every data row carries its source locator so a reader can go back to the original."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.catalog.models import FieldValue, SupplierItem, SupplierOffer
from apps.catalog.normalizers import category_label
from apps.catalog.selectors import active_products, product_state, review_flags
from apps.ingestion.models import ImportBatch, SourceRecord
from apps.matching.models import Issue, MatchCandidate
from apps.matching.rules import FIELD_LABELS, REASON_LABELS

HEADER_FILL = PatternFill("solid", fgColor="F3F1EC")
DECISION_FILL = PatternFill("solid", fgColor="FFF4CC")
POSITION_LABELS = {"left": "左", "right": "右", "": ""}


def _sheet(wb: Workbook, title: str, headers: list[str], rows, widths: dict | None = None,
           highlight: set[str] | None = None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = DECISION_FILL if highlight and cell.value in highlight else HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append(row)
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


def _new_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def _pos(item) -> str:
    return POSITION_LABELS.get(item.position, item.position)


def _oe(item) -> str:
    return ", ".join(pn.number_raw for pn in item.part_numbers.all() if pn.kind == "oe")


def export_master(path: Path) -> Path:
    flags = review_flags()
    products = list(active_products().order_by("code"))
    wb = _new_workbook()
    prod_rows, member_rows = [], []
    for p in products:
        members = sorted(p.items.all(), key=lambda m: (m.supplier.code, m.supplier_part_no))
        state, has_open = product_state(len(members), [m.pk for m in members], flags)
        disagreements = [k for k, v in (p.attr_consensus or {}).items() if not v.get("agreed", True)]
        prod_rows.append([
            p.code, state, "是" if has_open else "", len(members), p.category,
            POSITION_LABELS.get(p.position, p.position), p.fitment_label, p.dims_label,
            "一致" if not disagreements else "不一致：" + "、".join(FIELD_LABELS.get(d, d) for d in disagreements),
            "; ".join(f"{m.supplier.code}:{m.supplier_part_no}" for m in members),
            "; ".join(sorted({oe for m in members for oe in _oe(m).split(", ") if oe})),
            ", ".join(sorted({m.supplier.name for m in members})),
            sum(flags.open_issue_counts.get(m.pk, 0) for m in members),
        ])
        for m in members:
            member_rows.append([
                p.code, m.supplier.code, m.supplier_part_no, m.brand, m.name,
                category_label(m.category), _pos(m), m.fitment_label, m.dims_label, _oe(m),
                "是" if m.present_in_latest else "否（最新文件未出现）", m.source_ref,
                m.current_record.locator_label,
            ])
    _sheet(wb, "归一产品", ["产品编号", "分类", "有待确认疑似", "成员数", "类别", "位置", "适配车型",
                        "尺寸(包装)", "成员属性一致性", "品牌号/供应商编号", "OE号", "供应商",
                        "未处理异常数"], prod_rows)
    _sheet(wb, "成员条目", ["产品编号", "供应商", "供应商编号", "品牌", "原始名称", "类别", "位置",
                        "适配车型", "尺寸(包装)", "OE号", "在最新文件中", "原始记录ID", "来源定位"],
           member_rows)
    fv_rows = [
        [fv.item.product.code if fv.item.product else "", fv.item.supplier.code,
         fv.item.supplier_part_no, fv.field, fv.raw_value,
         "" if fv.normalized_value is None else str(fv.normalized_value),
         fv.source_record.batch.source_file.original_name, fv.source_record.locator_label,
         fv.source_column, "是" if fv.is_current else "否（历史值）", fv.source_record.batch_id]
        for fv in FieldValue.objects.select_related(
            "item__supplier", "item__product", "source_record__batch__source_file"
        ).order_by("item__product__code", "item__supplier__code", "item__supplier_part_no",
                   "field", "-is_current", "-created")
    ]
    _sheet(wb, "字段来源", ["产品编号", "供应商", "供应商编号", "字段", "原始值", "归一值", "来源文件",
                        "来源定位", "来源列", "当前值", "导入批次"], fv_rows)
    wb.save(path)
    return path


def export_offers(path: Path) -> Path:
    rows = []
    for o in SupplierOffer.objects.select_related(
            "item__supplier", "item__product", "source_record__batch").order_by(
            "item__product__code", "item__supplier__code", "item__supplier_part_no",
            "-is_current", "-quote_date"):
        rows.append([
            o.item.product.code if o.item.product else "", category_label(o.item.category),
            o.item.fitment_label, o.item.supplier.code, o.item.supplier.name,
            o.item.supplier_part_no, float(o.price) if o.price is not None else "（缺失）",
            o.currency or "（缺失）", o.moq if o.moq is not None else "（缺失）",
            o.quote_date.isoformat() if o.quote_date else "（缺失）",
            "是" if o.is_current else "否（历史报价）", o.source_record.locator_label,
            o.source_record.batch_id,
        ])
    wb = _new_workbook()
    _sheet(wb, "供应商报价", ["产品编号", "类别", "适配车型", "供应商", "供应商名称", "供应商编号", "价格",
                         "币种", "MOQ", "报价日期", "当前报价", "来源定位", "导入批次"], rows)
    ws = wb.create_sheet("说明")
    for line in ["同一归一产品下，不同供应商的报价逐行分别保留；同一供应商的历史报价也保留（当前报价=否）。",
                 "价格与币种按原文件保存，不做汇率换算；（缺失）表示原资料中该项为空。"]:
        ws.append([line])
    wb.save(path)
    return path


def _item_brief(item: SupplierItem) -> list:
    return [f"{item.supplier.code}:{item.supplier_part_no}", item.name,
            item.product.code if item.product else "", item.current_record.locator_label]


def export_review(path: Path) -> Path:
    cands = MatchCandidate.objects.filter(
        status__in=[MatchCandidate.Status.PENDING, MatchCandidate.Status.NEEDS_INFO]
    ).select_related("item_a__supplier", "item_a__product", "item_a__current_record",
                     "item_b__supplier", "item_b__product", "item_b__current_record")
    # Same grouping as the web review queue, so related pairs can be decided together offline.
    from apps.matching.selectors import review_groups

    group_of = {c.pk: n for n, g in enumerate(review_groups(), start=1) for c in g.candidates}
    cands = sorted(cands, key=lambda c: (group_of.get(c.pk, 0), c.priority, -c.confidence, c.pk))
    rows = []
    for c in cands:
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
    wb = _new_workbook()
    _sheet(wb, "疑似重复", [
        "candidate_id", "疑似组", "优先级", "触发原因", "置信度",
        "A 编号", "A 名称", "A 产品", "A 来源", "B 编号", "B 名称", "B 产品", "B 来源",
        "冲突字段", "缺失信息", "建议动作", "当前状态", "decision", "note",
    ], rows, widths={"建议动作": 70, "冲突字段": 40, "触发原因": 30},
        highlight={"decision", "note"})

    issues = Issue.objects.filter(status=Issue.Status.OPEN).select_related(
        "item__supplier", "item__product", "source_record", "batch__source_file")
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
    ws = wb.create_sheet("填写说明")
    for line in [
        "在黄色的 decision / note 列填写后，运行：python manage.py import_review <本文件> --reviewer 姓名",
        "疑似重复 decision：merge（确认合并）/ reject（不是同一产品）/ needs_info（待补充资料）；留空表示不处理。",
        "“疑似组”相同的行相互关联（同一批相似记录），建议一起对照判断。",
        "存在硬冲突的候选若选择 merge，必须在 note 说明理由。",
        "异常清单 decision：resolve（已解决）/ ignore（忽略）。",
        "任一行填写有误时整份文件不会写入，并逐行提示错误。",
    ]:
        ws.append([line])
    wb.save(path)
    return path


def export_import_report(path: Path) -> Path:
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


EXPORTS = {
    "master": ("master_data.xlsx", export_master),
    "offers": ("supplier_offers.xlsx", export_offers),
    "review": ("review_list.xlsx", export_review),
    "report": ("import_report.xlsx", export_import_report),
}


def export_all(out_dir: Path, only: list[str] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [fn(out_dir / name) for key, (name, fn) in EXPORTS.items() if not only or key in only]


def _fmt(v) -> str:
    if isinstance(v, list):
        if v and all(isinstance(x, (int, float)) for x in v):
            return "x".join(f"{x:g}" for x in v)
        return ",".join(map(str, v)) or "（空）"
    return str(v) if v not in (None, "") else "（空）"
