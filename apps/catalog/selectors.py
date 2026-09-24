"""Read side of the catalog: search, product views and the three-way classification."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Count, Q

from apps.matching.models import Issue, MatchCandidate

from .models import PartNumber, Product, SupplierItem
from .normalizers import normalize_number

# Open issues of these kinds mean the item needs more data before it can be trusted.
NEEDS_DATA_CODES = {
    "UNKNOWN_CATEGORY", "MISSING_FITMENT", "MISSING_DIMS", "MISSING_POSITION",
    "POSITION_INCONSISTENT", "INSUFFICIENT_FOR_MATCHING", "MISSING_PRICE", "MISSING_CURRENCY",
    "INVALID_VALUE", "KEY_ATTR_CHANGED", "CLUSTER_CONFLICT", "SUSPECTED_NAME_ERROR",
}
OPEN_CANDIDATE = [MatchCandidate.Status.PENDING, MatchCandidate.Status.NEEDS_INFO]

STATE_CONFIRMED = "已确认归一"
STATE_INDEPENDENT = "独立产品"
STATE_NEEDS_DATA = "待补充"


@dataclass
class ReviewFlags:
    open_candidate_items: set[int]
    needs_data_items: set[int]
    open_issue_counts: dict[int, int]


def review_flags() -> ReviewFlags:
    open_items = set()
    for a, b in MatchCandidate.objects.filter(status__in=OPEN_CANDIDATE).values_list(
            "item_a_id", "item_b_id"):
        open_items.update((a, b))
    needs = set(Issue.objects.filter(status=Issue.Status.OPEN, code__in=NEEDS_DATA_CODES,
                                     item__isnull=False).values_list("item_id", flat=True))
    counts = defaultdict(int)
    for item_id in Issue.objects.filter(status=Issue.Status.OPEN, item__isnull=False) \
            .values_list("item_id", flat=True):
        counts[item_id] += 1
    return ReviewFlags(open_items, needs, dict(counts))


def product_state(member_count: int, member_ids, flags: ReviewFlags) -> tuple[str, bool]:
    """(分类, 是否有待确认疑似)."""
    has_open = any(i in flags.open_candidate_items for i in member_ids)
    if member_count > 1:
        return STATE_CONFIRMED, has_open
    if any(i in flags.needs_data_items for i in member_ids):
        return STATE_NEEDS_DATA, has_open
    return STATE_INDEPENDENT, has_open


def active_products():
    return (Product.objects.filter(status=Product.Status.ACTIVE)
            .annotate(member_count=Count("items"))
            .filter(member_count__gt=0)
            .prefetch_related("items__supplier", "items__part_numbers", "items__offers"))


def search(query: str, *, supplier: str = "", state: str = "", limit: int | None = 200):
    """Products whose members match a part number / keyword / brand / supplier.

    Returns [{product, members, hits, state, has_open}] sorted by product code. The same
    function drives the search page and "export these results", so both always agree.
    limit=None returns every match (used by exports).
    """
    query = (query or "").strip()
    items = SupplierItem.objects.select_related("supplier", "product")
    if supplier:
        items = items.filter(Q(supplier__code__iexact=supplier) | Q(supplier__name__icontains=supplier))
    if query:
        qn = normalize_number(query)
        cond = (
            Q(name__icontains=query) | Q(brand__icontains=query)
            | Q(supplier__code__iexact=query) | Q(supplier__name__icontains=query)
            | Q(product__code__iexact=query) | Q(source_ref__iexact=query)
            | Q(fitment_make__icontains=query) | Q(fitment_model__icontains=query)
            | Q(category__icontains=query.lower().replace(" ", "_"))
        )
        if qn:
            cond |= Q(part_numbers__number_norm__contains=qn)
        items = items.filter(cond).distinct()
    flags = review_flags()
    grouped: dict[int, list] = defaultdict(list)
    for it in (items if limit is None else items[: limit * 3]):
        if it.product_id:
            grouped[it.product_id].append(it)
    products = active_products().filter(pk__in=grouped)
    results = []
    for p in products:
        members = list(p.items.all())
        state_label, has_open = product_state(len(members), [m.pk for m in members], flags)
        if state and state_label != state and not (state == "疑似重复" and has_open):
            continue
        results.append({"product": p, "members": members, "hits": grouped[p.pk],
                        "state": state_label, "has_open": has_open})
    results.sort(key=lambda r: r["product"].code)
    return results if limit is None else results[:limit]


def similar_numbers(query: str, limit: int = 8):
    """'Did you mean' suggestions using pg_trgm when the exact search finds nothing."""
    qn = normalize_number(query)
    if len(qn) < 3:
        return []
    return list(
        PartNumber.objects.annotate(sim=TrigramSimilarity("number_norm", qn))
        .filter(sim__gt=0.3).select_related("item__supplier", "item__product")
        .order_by("-sim")[:limit]
    )
