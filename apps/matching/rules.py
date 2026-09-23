"""Pure pair evaluation: compare two items field by field and classify the pair.

No database access, so every rule is unit-testable and explainable. See docs/design.html §5.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from apps.catalog.normalizers import category_label, dims_equal, is_sided

EQUAL, CONFLICT, MISSING, NA = "equal", "conflict", "missing", "n/a"

FIELD_LABELS = {
    "oe": "OE 号", "category": "类别", "fitment": "适配车型", "dims": "尺寸",
    "position": "位置", "fitment_years": "车型年份", "name": "名称",
}

REASON_LABELS = {
    "AUTO_OE_FULL_MATCH": "共用 OE 且类别/车型/尺寸/位置全部一致",
    "SHARED_OE_CONFLICT": "共用 OE 但关键属性冲突",
    "SHARED_OE_INCOMPLETE": "共用 OE 但关键信息缺失",
    "OE_DIFFER": "属性一致但 OE 号不同",
    "NO_OE_ATTR_MATCH": "无共同 OE，仅属性一致",
    "SAME_SUPPLIER_DUP": "同一供应商的两个编号",
    "MISSING_KEY_FIELDS": "缺少关键信息",
    "DIFFERENT_CATEGORY": "类别不同",
    "DIFFERENT_POSITION": "左右位置不同",
    "DIFFERENT_FITMENT": "适配车型不同",
    "DIFFERENT_DIMS": "尺寸不同",
    "CLUSTER_CONFLICT": "合并会使同一产品内出现冲突",
}


@dataclass(frozen=True)
class ItemView:
    """The attributes of a SupplierItem that matching looks at."""

    id: int
    label: str
    supplier_id: int
    name: str = ""
    category: str = ""
    position: str = ""
    make: str = ""
    model: str = ""
    year_from: int | None = None
    year_to: int | None = None
    dims: tuple | None = None
    oe: frozenset = frozenset()
    attr_hash: str = ""
    oe_raw: tuple = field(default=(), compare=False)  # ((normalized, as written), ...)

    def oe_display(self, numbers) -> list[str]:
        raw = dict(self.oe_raw)
        return [raw.get(n, n) for n in sorted(numbers)]

    @property
    def fitment_key(self) -> str:
        if not (self.make or self.model):
            return ""
        return f"{self.make.upper()}|{self.model.upper()}"

    @property
    def fitment_label(self) -> str:
        years = f" {self.year_from}-{self.year_to or ''}" if self.year_from else ""
        return f"{self.make} {self.model}".strip() + years

    @classmethod
    def from_item(cls, item) -> ItemView:
        return cls(
            id=item.pk, label=f"{item.supplier.code}:{item.supplier_part_no}",
            supplier_id=item.supplier_id, name=item.name, category=item.category,
            position=item.position, make=item.fitment_make, model=item.fitment_model,
            year_from=item.year_from, year_to=item.year_to,
            dims=tuple(item.dims_cm) if item.dims_cm else None,
            oe=frozenset(item.oe_numbers), attr_hash=item.attr_hash,
            oe_raw=tuple((pn.number_norm, pn.number_raw) for pn in item.part_numbers.all()
                         if pn.kind == "oe"),
        )


@dataclass
class Outcome:
    classification: str  # auto_confirmed / suspect / auto_rejected
    reasons: list[str]
    comparisons: dict
    conflicts: list[dict]
    missing: list[str]
    score: int
    confidence: float
    priority: int
    suggested_action: str
    evidence_hash: str = ""
    shared_oe: list[str] = field(default_factory=list)

    @property
    def hard_conflicts(self) -> list[str]:
        return [c["field"] for c in self.conflicts if c.get("hard")]


def evidence_hash(a: ItemView, b: ItemView) -> str:
    x, y = sorted([a.attr_hash, b.attr_hash])
    return hashlib.sha256(f"{x}|{y}".encode()).hexdigest()


def compare(a: ItemView, b: ItemView, cfg: dict) -> tuple[dict, list[str]]:
    """Field → {result, a, b}; plus soft 'missing' notes (e.g. fitment years)."""
    tol = cfg["dims_tolerance_cm"]
    out: dict[str, dict] = {}
    extra_missing = []

    def put(fld, result, va, vb):
        out[fld] = {"result": result, "a": va, "b": vb}

    # OE
    if a.oe and b.oe:
        shared = sorted(a.oe & b.oe)
        put("oe", EQUAL if shared else CONFLICT, a.oe_display(a.oe), b.oe_display(b.oe))
        out["oe"]["shared"] = shared
        out["oe"]["shared_display"] = a.oe_display(shared)
    else:
        put("oe", MISSING, a.oe_display(a.oe), b.oe_display(b.oe))
    # Category
    if a.category and b.category:
        put("category", EQUAL if a.category == b.category else CONFLICT,
            category_label(a.category), category_label(b.category))
    else:
        put("category", MISSING, category_label(a.category), category_label(b.category))
    # Fitment
    if a.fitment_key and b.fitment_key:
        if a.fitment_key != b.fitment_key:
            result = CONFLICT
        elif a.year_from and b.year_from:
            result = EQUAL if (a.year_from, a.year_to) == (b.year_from, b.year_to) else CONFLICT
        else:
            result = EQUAL
            if a.year_from or b.year_from:
                extra_missing.append("fitment_years")
        put("fitment", result, a.fitment_label, b.fitment_label)
    else:
        put("fitment", MISSING, a.fitment_label, b.fitment_label)
    # Dimensions
    if a.dims and b.dims:
        put("dims", EQUAL if dims_equal(a.dims, b.dims, tol) else CONFLICT,
            list(a.dims), list(b.dims))
    else:
        put("dims", MISSING, list(a.dims or []), list(b.dims or []))
    # Position
    if a.position and b.position:
        put("position", EQUAL if a.position == b.position else CONFLICT, a.position, b.position)
    elif a.position or b.position or is_sided(a.category) or is_sided(b.category):
        put("position", MISSING, a.position, b.position)
    else:
        put("position", NA, "", "")
    # Name: weak evidence only
    sim = fuzz.token_set_ratio(a.name.lower(), b.name.lower()) / 100 if a.name and b.name else 0
    out["name"] = {"result": "similarity", "a": a.name, "b": b.name, "similarity": round(sim, 2)}
    return out, extra_missing


def evaluate(a: ItemView, b: ItemView, cfg: dict) -> Outcome | None:
    """Classify a pair. Returns None when the pair is not worth recording."""
    cmp, extra_missing = compare(a, b, cfg)
    weights = cfg["weights"]
    hard_fields = cfg.get("hard_conflict_fields", ["category", "fitment", "dims", "position"])

    conflicts = []
    for fld in ("oe", "category", "fitment", "dims", "position"):
        if cmp[fld]["result"] == CONFLICT:
            conflicts.append({"field": fld, "a": cmp[fld]["a"], "b": cmp[fld]["b"],
                              "hard": fld in hard_fields})
    hard = [c["field"] for c in conflicts if c["hard"]]
    missing = [f for f in ("oe", "category", "fitment", "dims", "position")
               if cmp[f]["result"] == MISSING] + extra_missing
    shared_oe = cmp["oe"].get("shared", [])

    score = sum(weights[f] for f in ("oe", "category", "fitment", "dims", "position")
                if cmp[f]["result"] == EQUAL)
    score += round(weights.get("name", 0) * cmp["name"]["similarity"])
    # Missing evidence counts against confidence; only "not applicable" fields are left out.
    denom = sum(weights[f] for f in ("oe", "category", "fitment", "dims", "position")
                if cmp[f]["result"] != NA) + weights.get("name", 0)
    confidence = score / denom if denom else 0.0

    if hard:
        confidence = min(confidence, cfg.get("conflict_confidence_cap", 0.3))
    confidence = round(confidence, 3)

    same_supplier = a.supplier_id == b.supplier_id
    auto_cfg = cfg["auto_confirm"]
    required_ok = (
        all(cmp[f]["result"] == EQUAL for f in auto_cfg.get("required_equal", []))
        and cmp["position"]["result"] in (EQUAL, NA)
        and not missing_except_oe(missing)
    )
    attrs_match = cmp["category"]["result"] == EQUAL and (
        cmp["fitment"]["result"] == EQUAL or cmp["dims"]["result"] == EQUAL)

    reasons: list[str] = []
    if shared_oe and not same_supplier:
        if not hard and required_ok and auto_cfg.get("enabled", True):
            classification, reasons = "auto_confirmed", ["AUTO_OE_FULL_MATCH"]
        elif hard:
            classification, reasons = "suspect", ["SHARED_OE_CONFLICT"]
        else:
            classification, reasons = "suspect", ["SHARED_OE_INCOMPLETE"]
    elif hard:
        if shared_oe:  # same supplier, shared OE, conflicting: still worth a human look
            classification, reasons = "suspect", ["SHARED_OE_CONFLICT", "SAME_SUPPLIER_DUP"]
        else:
            classification = "auto_rejected"
            reasons = [f"DIFFERENT_{f.upper()}" for f in hard]
    elif shared_oe or attrs_match:
        classification = "suspect"
        if shared_oe:
            reasons = ["SHARED_OE_INCOMPLETE"] if missing_except_oe(missing) else []
        elif cmp["oe"]["result"] == CONFLICT:
            reasons = ["OE_DIFFER"]
        else:
            reasons = ["NO_OE_ATTR_MATCH"]
        if same_supplier:
            reasons.append("SAME_SUPPLIER_DUP")
    else:
        return None
    if classification == "suspect" and missing_except_oe(missing):
        reasons.append("MISSING_KEY_FIELDS")
    reasons = list(dict.fromkeys(reasons))  # dedupe, keep order

    prio_cfg = cfg.get("priority", {})
    priority = min((prio_cfg.get(r, 9) for r in reasons), default=9)
    if classification != "suspect":
        priority = 9
    return Outcome(
        classification=classification, reasons=reasons, comparisons=cmp, conflicts=conflicts,
        missing=missing, score=score, confidence=confidence, priority=priority,
        suggested_action=suggest(classification, reasons, a, b, cmp, conflicts, missing),
        evidence_hash=evidence_hash(a, b), shared_oe=shared_oe,
    )


def missing_except_oe(missing: list[str]) -> list[str]:
    return [m for m in missing if m != "oe"]


def _fields(names) -> str:
    return "、".join(FIELD_LABELS.get(n, n) for n in names)


def suggest(classification, reasons, a, b, cmp, conflicts, missing) -> str:
    if classification == "auto_confirmed":
        return "规则已自动归一；如判断有误，可在产品页拆分。"
    if classification == "auto_rejected":
        return "规则判定为不同产品（" + _fields(c["field"] for c in conflicts if c["hard"]) + "冲突），无需处理。"
    parts = []
    shared = cmp["oe"].get("shared_display", [])
    conflict_desc = "；".join(
        f"{FIELD_LABELS[c['field']]}：{a.label}={_fmt(c['a'])} vs {b.label}={_fmt(c['b'])}"
        for c in conflicts if c["field"] != "oe")
    if "SHARED_OE_CONFLICT" in reasons:
        parts.append(
            f"{', '.join(shared)} 同时出现在 {a.label}（{category_label(a.category) or '?'}）与 "
            f"{b.label}（{category_label(b.category) or '?'}），冲突：{conflict_desc}。"
            "请向供应商核对 OE 号是否录错或是否为通用参考号；默认不合并。")
    if "SHARED_OE_INCOMPLETE" in reasons:
        parts.append(f"共用 {', '.join(shared)}，但缺少{_fields(missing_except_oe(missing))}。"
                     "请补充资料后重新导入，或在核实后确认合并。")
    if "OE_DIFFER" in reasons:
        parts.append(f"属性一致但 OE 号不同（{_fmt(cmp['oe']['a'])} vs {_fmt(cmp['oe']['b'])}），"
                     "可能是不同代次或替代件，请核对。")
    if "NO_OE_ATTR_MATCH" in reasons:
        parts.append("无共同 OE 号，仅类别/车型/尺寸一致；请查看图片或向供应商索要 OE 号后确认。")
    if "SAME_SUPPLIER_DUP" in reasons:
        parts.append("同一供应商的两个编号属性相近：可能是重复录入，也可能是不同品质档，请向供应商确认。")
    if "MISSING_KEY_FIELDS" in reasons and "SHARED_OE_INCOMPLETE" not in reasons:
        parts.append(f"缺少{_fields(missing_except_oe(missing))}，判断依据不足。")
    return " ".join(parts)


def _fmt(v) -> str:
    if isinstance(v, list):
        if v and all(isinstance(x, (int, float)) for x in v):
            return "x".join(f"{x:g}" for x in v)
        return ",".join(str(x) for x in v) or "（空）"
    return str(v) if v not in (None, "") else "（空）"
