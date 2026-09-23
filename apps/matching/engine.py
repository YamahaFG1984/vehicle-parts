"""Matching run: blocking → pair evaluation → reconcile with stored decisions → clustering.

Rule-made decisions are recomputed on every run. Human decisions are kept as long as the
evidence they were made on (both items' key attributes) is unchanged; otherwise they are
reopened with an audit entry. See docs/design.html §5–6.
"""

from __future__ import annotations

import itertools
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import transaction

from apps.catalog.models import Product, SupplierItem
from apps.catalog.normalizers import Finding, category_label, dims_equal
from apps.core import rules as rule_files

from . import issues as issue_service
from .models import Issue, MatchCandidate, ReviewDecision
from .rules import CONFLICT, FIELD_LABELS, ItemView, Outcome, evaluate

logger = logging.getLogger(__name__)

MC = MatchCandidate


@dataclass
class MatchSummary:
    pairs_evaluated: int = 0
    classification: Counter = field(default_factory=Counter)
    status: Counter = field(default_factory=Counter)
    reopened: int = 0
    cluster_conflicts: int = 0
    products_multi: int = 0
    products_single: int = 0
    changes: list[str] = field(default_factory=list)  # dry-run diff

    def as_text(self) -> str:
        return (
            f"评估 {self.pairs_evaluated} 对；规则分类 {dict(self.classification)}；"
            f"当前状态 {dict(self.status)}；重新打开 {self.reopened}；"
            f"簇冲突 {self.cluster_conflicts}；多成员产品 {self.products_multi}，"
            f"单成员产品 {self.products_single}"
        )


class _DryRun(Exception):
    pass


# ---------------------------------------------------------------------------- blocking


def candidate_pairs(views: list[ItemView], cfg: dict) -> set[tuple[int, int]]:
    blocks: dict[tuple, list[int]] = defaultdict(list)
    min_oe = cfg.get("min_oe_length", 5)
    blk = cfg["blocking"]
    for v in views:
        if blk.get("oe"):
            for oe in v.oe:
                if len(oe) >= min_oe:
                    blocks[("oe", oe)].append(v.id)
        if blk.get("category_fitment") and v.category and v.fitment_key:
            blocks[("cf", v.category, v.fitment_key)].append(v.id)
        if blk.get("dims") and v.dims:
            # Round to the tolerance grid so near-equal sizes land in the same block.
            key = tuple(round(d) for d in sorted(v.dims))
            blocks[("dims", key)].append(v.id)
    pairs = set()
    for ids in blocks.values():
        for x, y in itertools.combinations(sorted(set(ids)), 2):
            pairs.add((x, y))
    return pairs


# ---------------------------------------------------------------------------- union-find


class DisjointSet:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}
        self.members = {i: {i} for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return rx
        if len(self.members[rx]) < len(self.members[ry]):
            rx, ry = ry, rx
        self.parent[ry] = rx
        self.members[rx] |= self.members.pop(ry)
        return rx


# ---------------------------------------------------------------------------- run


def run_matching(*, dry_run: bool = False) -> MatchSummary:
    rule_files.reload()
    cfg = rule_files.matching()
    summary = MatchSummary()
    try:
        with transaction.atomic():
            _run(cfg, summary, dry_run)
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass
    return summary


def _snapshot(outcome: Outcome | None, a: ItemView, b: ItemView) -> dict:
    snap = {"a": a.label, "b": b.label}
    if outcome:
        snap.update(classification=outcome.classification, reasons=outcome.reasons,
                    conflicts=outcome.conflicts, missing=outcome.missing)
    return snap


def _apply_outcome(cand: MatchCandidate, outcome: Outcome, version: str) -> None:
    cand.rule_classification = outcome.classification
    cand.classification = outcome.classification
    cand.score, cand.confidence, cand.priority = outcome.score, outcome.confidence, outcome.priority
    cand.reasons, cand.comparisons = outcome.reasons, outcome.comparisons
    cand.conflicts, cand.missing = outcome.conflicts, outcome.missing
    cand.suggested_action = outcome.suggested_action
    cand.ruleset_version = version
    cand.evidence_hash = outcome.evidence_hash


RULE_STATUS = {
    "auto_confirmed": MC.Status.ACCEPTED,
    "suspect": MC.Status.PENDING,
    "auto_rejected": MC.Status.REJECTED,
}


def _run(cfg: dict, summary: MatchSummary, dry_run: bool) -> None:
    version = cfg["version"]
    items = list(SupplierItem.objects.select_related("supplier", "product", "current_record")
                 .prefetch_related("part_numbers"))
    views = {i.pk: ItemView.from_item(i) for i in items}
    existing = {(c.item_a_id, c.item_b_id): c for c in MC.objects.all()}

    # 1) Evaluate blocked pairs and reconcile with what is stored.
    evaluated: dict[tuple[int, int], MatchCandidate] = {}
    for a_id, b_id in sorted(candidate_pairs(list(views.values()), cfg)):
        a, b = views[a_id], views[b_id]
        outcome = evaluate(a, b, cfg)
        summary.pairs_evaluated += 1
        cand = existing.get((a_id, b_id))
        if outcome is None:
            continue
        summary.classification[outcome.classification] += 1
        if cand is None:
            cand = MC(item_a_id=a_id, item_b_id=b_id, decided_by=MC.DecidedBy.RULE)
            _apply_outcome(cand, outcome, version)
            cand.status = RULE_STATUS[outcome.classification]
            cand.save()
            ReviewDecision.objects.create(candidate=cand, action=ReviewDecision.Action.RULE,
                                          via=ReviewDecision.Via.SYSTEM,
                                          note=f"规则 {version}：{outcome.classification}",
                                          snapshot=_snapshot(outcome, a, b))
            summary.changes.append(f"新候选 {a.label} ↔ {b.label}: {outcome.classification}")
        elif cand.decided_by == MC.DecidedBy.HUMAN:
            evidence_changed = cand.evidence_hash != outcome.evidence_hash
            old_status = cand.status
            _apply_outcome(cand, outcome, version)
            if evidence_changed:
                cand.decided_by = MC.DecidedBy.RULE
                cand.status = RULE_STATUS[outcome.classification]
                if cand.status == MC.Status.ACCEPTED and old_status != MC.Status.ACCEPTED:
                    cand.status = MC.Status.PENDING  # never auto-merge over a human "no"
                summary.reopened += 1
                ReviewDecision.objects.create(
                    candidate=cand, action=ReviewDecision.Action.REOPEN,
                    via=ReviewDecision.Via.SYSTEM,
                    note=f"资料已变化，原人工决定（{old_status}）失效，重新评估为 {cand.status}",
                    snapshot=_snapshot(outcome, a, b))
                summary.changes.append(f"重新打开 {a.label} ↔ {b.label}")
            else:
                # Human decision stands; only the displayed rule output is refreshed.
                cand.classification = {
                    MC.Status.ACCEPTED: MC.Classification.AUTO_CONFIRMED,
                    MC.Status.REJECTED: MC.Classification.AUTO_REJECTED,
                }.get(cand.status, MC.Classification.SUSPECT)
            cand.save()
        else:
            before = (cand.classification, cand.status)
            _apply_outcome(cand, outcome, version)
            cand.status = RULE_STATUS[outcome.classification]
            cand.save()
            if before != (cand.classification, cand.status):
                summary.changes.append(
                    f"{a.label} ↔ {b.label}: {before[0]}/{before[1]} → "
                    f"{cand.classification}/{cand.status}")
        evaluated[(a_id, b_id)] = cand

    # Rule-made candidates that were not produced this time no longer apply.
    for key, cand in existing.items():
        if key not in evaluated and cand.decided_by == MC.DecidedBy.RULE \
                and cand.status != MC.Status.SUPERSEDED:
            cand.status = MC.Status.SUPERSEDED
            cand.save(update_fields=["status", "modified"])
            summary.changes.append(f"失效 {views[key[0]].label} ↔ {views[key[1]].label}")

    all_cands = list(MC.objects.all())
    # 2) Cluster over accepted edges, refusing merges that would put a hard conflict inside.
    dsu = DisjointSet(views.keys())
    accepted = sorted(
        (c for c in all_cands if c.status == MC.Status.ACCEPTED),
        key=lambda c: (c.decided_by != MC.DecidedBy.HUMAN, -c.confidence, c.pk),
    )
    for cand in accepted:
        ra, rb = dsu.find(cand.item_a_id), dsu.find(cand.item_b_id)
        if ra == rb:
            continue
        # A reviewer's explicit merge is authoritative; only rule-made merges are guarded.
        clash = None if cand.decided_by == MC.DecidedBy.HUMAN else _cluster_clash(
            dsu.members[ra], dsu.members[rb], views, cfg)
        if clash:
            summary.cluster_conflicts += 1
            x, y, fields_ = clash
            cand.status = MC.Status.PENDING
            cand.classification = MC.Classification.SUSPECT
            cand.decided_by = MC.DecidedBy.RULE
            cand.reasons = list(dict.fromkeys([*cand.reasons, "CLUSTER_CONFLICT"]))
            cand.priority = 1
            cand.suggested_action = (
                f"合并后 {views[x].label} 与 {views[y].label} 会进入同一产品（{'、'.join(fields_)}），"
                "规则不做这种传递合并，请人工确认。 " + cand.suggested_action)
            cand.save()
            item = SupplierItem.objects.get(pk=cand.item_a_id)
            issue_service.open_item_issue(
                item, "CLUSTER_CONFLICT", "warning",
                f"{views[cand.item_a_id].label} ↔ {views[cand.item_b_id].label} 的合并被暂停："
                f"{views[x].label} 与 {views[y].label}：{'、'.join(fields_)}",
                details={"candidate": cand.pk, "items": [views[x].label, views[y].label]})
            continue
        dsu.union(ra, rb)

    # 3) One open question per pair of clusters; honour human "not the same" between clusters.
    cluster_of = {i: dsu.find(i) for i in views}
    by_clusters: dict[frozenset, list[MatchCandidate]] = defaultdict(list)
    for cand in all_cands:
        ca, cb = cluster_of[cand.item_a_id], cluster_of[cand.item_b_id]
        if ca == cb:
            if cand.status in (MC.Status.PENDING, MC.Status.NEEDS_INFO) \
                    and cand.decided_by == MC.DecidedBy.RULE:
                cand.status = MC.Status.SUPERSEDED
                cand.save(update_fields=["status", "modified"])
            continue
        by_clusters[frozenset((ca, cb))].append(cand)
    for group in by_clusters.values():
        human_block = next((c for c in group if c.decided_by == MC.DecidedBy.HUMAN and c.status in
                            (MC.Status.REJECTED, MC.Status.NEEDS_INFO)), None)
        open_rule = sorted(
            (c for c in group if c.decided_by == MC.DecidedBy.RULE and c.status == MC.Status.PENDING),
            key=lambda c: (c.priority, -c.confidence, c.pk))
        keep = None if human_block else (open_rule[0] if open_rule else None)
        for cand in open_rule:
            if cand is keep:
                continue
            cand.status = MC.Status.SUPERSEDED
            cand.save(update_fields=["status", "modified"])

    # 4) Products follow clusters; product codes stay stable where possible.
    _assign_products(dsu, items, views, summary)

    # 5) Data-quality findings that depend on the whole catalog, recomputed every run.
    findings = {}
    for item in items:
        v = views[item.pk]
        if not v.oe and (not v.category or (not v.fitment_key and not v.dims)):
            findings.setdefault(item.pk, []).append(_insufficient(v))
    if cfg.get("name_error_check", {}).get("enabled", True):
        for item_id, finding in _name_error_findings(views, cfg).items():
            findings.setdefault(item_id, []).append(finding)
    issue_service.sync_system_findings(
        {i.pk: i for i in items}, findings, codes=SYSTEM_FINDING_CODES)

    summary.status = Counter(MC.objects.values_list("status", flat=True))


SYSTEM_FINDING_CODES = ("INSUFFICIENT_FOR_MATCHING", "SUSPECTED_NAME_ERROR")


def _insufficient(v: ItemView):
    lacking = [n for n, ok in (("OE 号", v.oe), ("类别", v.category), ("车型", v.fitment_key),
                               ("尺寸", v.dims)) if not ok]
    return Finding("INSUFFICIENT_FOR_MATCHING", "warning", "",
                   f"缺少{'、'.join(lacking)}，无法与其他记录比对，归入待补充")


def _name_error_findings(views: dict, cfg: dict) -> dict:
    """Package size unlike every same-type record of this truck model, yet identical to several
    records of another type for the same model: the name (or the size) is probably wrong.

    Shared packaging between two types is fine as long as each record also matches its own type
    (e.g. air filter housing and fan shroud for the Century share one carton size).
    """
    tol = cfg["dims_tolerance_cm"]
    need = cfg.get("name_error_check", {}).get("min_other_category_matches", 2)
    by_model = defaultdict(list)
    for v in views.values():
        if v.category and v.fitment_key and v.dims:
            by_model[v.fitment_key].append(v)
    out = {}
    for group in by_model.values():
        for v in group:
            same = [o for o in group if o.id != v.id and o.category == v.category]
            if any(dims_equal(v.dims, o.dims, tol) for o in same):
                continue
            lookalikes = defaultdict(list)
            for o in group:
                if o.category != v.category and dims_equal(v.dims, o.dims, tol):
                    lookalikes[o.category].append(o)
            best = max(lookalikes.items(), key=lambda kv: len(kv[1]), default=None)
            if not best or len(best[1]) < need:
                continue
            other_cat, others = best
            dims = " x ".join(f"{d:g}" for d in v.dims)
            peers = f"与同车型的 {len(same)} 条 {category_label(v.category)} 都不同" if same else \
                f"同车型没有其他 {category_label(v.category)} 可对照"
            out[v.id] = Finding(
                "SUSPECTED_NAME_ERROR", "warning", "name",
                f"包装尺寸 {dims} cm 与同车型的 {len(others)} 条 {category_label(other_cat)} 一致"
                f"（{', '.join(o.label for o in others[:4])}），{peers}：疑似品名/类别或尺寸录错，请核对",
                {"lookalike_category": other_cat, "lookalikes": [o.label for o in others],
                 "same_category_peers": [o.label for o in same]})
    return out


def _cluster_clash(left: set, right: set, views: dict, cfg: dict):
    """First pair (x, y, reasons) that must not end up in one product through a rule merge.

    Hard attribute conflicts are never allowed; two part numbers of the same supplier are not
    allowed either unless a reviewer merged them (same rule as direct pairs, applied transitively).
    """
    from .rules import compare

    hard = cfg.get("hard_conflict_fields", [])
    allow_same = cfg["auto_confirm"].get("allow_same_supplier", False)
    for x in sorted(left):
        for y in sorted(right):
            cmp, _ = compare(views[x], views[y], cfg)
            reasons = [FIELD_LABELS[f] for f in hard if cmp[f]["result"] == CONFLICT]
            if not allow_same and views[x].supplier_id == views[y].supplier_id:
                reasons.append("同一供应商的两个编号")
            if reasons:
                return x, y, reasons
    return None


def _assign_products(dsu: DisjointSet, items, views, summary: MatchSummary) -> None:
    item_by_id = {i.pk: i for i in items}
    clusters = sorted(dsu.members.values(), key=lambda m: min(m))
    claimed: set[int] = set()
    moves: dict[int, Counter] = defaultdict(Counter)  # old product -> where its members went
    for members in clusters:
        counts = Counter(item_by_id[i].product_id for i in members if item_by_id[i].product_id)
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        choice = next((pid for pid, _ in ranked if pid not in claimed), None)
        product = Product.objects.get(pk=choice) if choice else Product.objects.create()
        claimed.add(product.pk)
        if product.status != Product.Status.ACTIVE:
            product.status, product.merged_into = Product.Status.ACTIVE, None
        for i in members:
            it = item_by_id[i]
            if it.product_id != product.pk:
                if it.product_id:
                    moves[it.product_id][product.pk] += 1
                it.product = product
                it.save(update_fields=["product", "modified"])
        _refresh_consensus(product, [item_by_id[i] for i in members])
        if len(members) > 1:
            summary.products_multi += 1
        else:
            summary.products_single += 1
    # Products left without members are retired and point to where most of their members went.
    for product in Product.objects.filter(status=Product.Status.ACTIVE).exclude(pk__in=claimed):
        dest = moves.get(product.pk)
        product.status = Product.Status.RETIRED
        product.merged_into_id = dest.most_common(1)[0][0] if dest else None
        product.save(update_fields=["status", "merged_into", "modified"])


def _refresh_consensus(product: Product, members: list[SupplierItem]) -> None:
    def agg(values):
        vals = sorted({v for v in values if v})
        return {"value": vals[0] if len(vals) == 1 else "", "agreed": len(vals) <= 1,
                "values": vals}

    consensus = {
        "category": agg(category_label(m.category) for m in members),
        "position": agg(m.position for m in members),
        "fitment": agg(m.fitment_label for m in members),
        "dims": agg(m.dims_label for m in members),
    }
    product.attr_consensus = consensus
    product.category = consensus["category"]["value"] or " / ".join(consensus["category"]["values"])
    product.position = consensus["position"]["value"]
    product.fitment_label = consensus["fitment"]["value"] or " / ".join(consensus["fitment"]["values"])
    product.dims_label = consensus["dims"]["value"] or " / ".join(consensus["dims"]["values"])
    product.save()


def pending_count() -> int:
    return MC.objects.filter(status__in=[MC.Status.PENDING, MC.Status.NEEDS_INFO]).count()


def open_issue_count() -> int:
    return Issue.objects.filter(status=Issue.Status.OPEN).count()
