"""Read side of review: open candidates grouped into 'suspect groups'."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from apps.catalog.models import SupplierItem

from .engine import DisjointSet
from .models import Issue, MatchCandidate

OPEN = [MatchCandidate.Status.PENDING, MatchCandidate.Status.NEEDS_INFO]


@dataclass
class ReviewGroup:
    key: int  # smallest candidate id in the group; stable enough for a URL
    candidates: list[MatchCandidate]
    product_ids: set[int]
    items: list[SupplierItem] = field(default_factory=list)

    @property
    def priority(self) -> int:
        return min(c.priority for c in self.candidates)

    @property
    def reasons(self) -> list[str]:
        return sorted({r for c in self.candidates for r in c.reasons})


def review_groups(reason: str = "") -> list[ReviewGroup]:
    """Connected components of open candidates, with products (already merged sets) as nodes."""
    cands = list(MatchCandidate.objects.filter(status__in=OPEN).select_related(
        "item_a__supplier", "item_a__product", "item_b__supplier", "item_b__product"))
    if reason:
        cands = [c for c in cands if reason in c.reasons]
    node = {}
    for c in cands:
        node[c.pk] = (c.item_a.product_id, c.item_b.product_id)
    dsu = DisjointSet({p for pair in node.values() for p in pair})
    for a, b in node.values():
        dsu.union(a, b)
    grouped: dict[int, list[MatchCandidate]] = defaultdict(list)
    for c in cands:
        grouped[dsu.find(c.item_a.product_id)].append(c)
    groups = []
    for members in grouped.values():
        members.sort(key=lambda c: (c.priority, -c.confidence, c.pk))
        products = {p for c in members for p in (c.item_a.product_id, c.item_b.product_id)}
        groups.append(ReviewGroup(min(c.pk for c in members), members, products))
    groups.sort(key=lambda g: (g.priority, -max(c.confidence for c in g.candidates), g.key))
    return groups


def group_containing(candidate_id: int) -> ReviewGroup | None:
    for g in review_groups():
        if any(c.pk == candidate_id for c in g.candidates):
            items = list(SupplierItem.objects.filter(product__in=g.product_ids).select_related(
                "supplier", "product", "current_record").prefetch_related(
                "part_numbers", "offers").order_by("product__code", "supplier__code",
                                                   "supplier_part_no"))
            open_codes = defaultdict(list)
            for item_id, code in Issue.objects.filter(
                    item__in=items, status=Issue.Status.OPEN).values_list("item_id", "code"):
                open_codes[item_id].append(code)
            for it in items:
                it.open_issue_codes = sorted(open_codes.get(it.pk, []))
                it.current_offer = next((o for o in it.offers.all() if o.is_current), None)
                it.oe_display = ", ".join(pn.number_raw for pn in it.part_numbers.all()
                                          if pn.kind == "oe")
            g.items = items
            return g
    return None
