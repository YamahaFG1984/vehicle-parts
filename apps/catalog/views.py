from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.ingestion.models import ImportBatch, SourceFile, Supplier
from apps.matching.models import Issue, MatchCandidate

from . import selectors
from .models import FieldValue, Product, SupplierItem
from .normalizers import normalize_number

SEARCH_EXAMPLES = ["OE-VNL-1001", "b01x", "Side Grille", "Volvo VNL", "Air Filter"]


def _search_box_context(**extra):
    """Choices for the shared search box (dashboard and search page)."""
    return {"suppliers": Supplier.objects.order_by("code"), "examples": SEARCH_EXAMPLES,
            "state_choices": ["已确认归一", "疑似重复", "独立产品", "待补充"], **extra}


def dashboard(request):
    flags = selectors.review_flags()
    products = list(selectors.active_products())
    states = {"已确认归一": 0, "独立产品": 0, "待补充": 0}
    with_open = 0
    for p in products:
        members = list(p.items.all())
        state, has_open = selectors.product_state(len(members), [m.pk for m in members], flags)
        states[state] += 1
        with_open += has_open
    context = {
        "item_count": SupplierItem.objects.count(),
        "product_count": len(products),
        "states": states,
        "with_open": with_open,
        "pending": MatchCandidate.objects.filter(status__in=selectors.OPEN_CANDIDATE).count(),
        "open_issues": Issue.objects.filter(status=Issue.Status.OPEN).count(),
        "batches": ImportBatch.objects.select_related("source_file__supplier")[:8],
        "by_supplier": SupplierItem.objects.values("supplier__code", "supplier__name")
        .annotate(n=Count("id")).order_by("supplier__code"),
    }
    context.update(_search_box_context())
    return render(request, "catalog/dashboard.html", context)


def search(request):
    q = request.GET.get("q", "").strip()
    supplier = request.GET.get("supplier", "").strip()
    state = request.GET.get("state", "").strip()
    limit = 200
    results = selectors.search(q, supplier=supplier, state=state, limit=limit + 1) \
        if (q or supplier or state) else []
    truncated = len(results) > limit
    results = results[:limit]
    suggestions = selectors.similar_numbers(q) if q and not results else []
    return render(request, "catalog/search.html", _search_box_context(
        q=q, supplier=supplier, state=state, results=results, truncated=truncated,
        suggestions=suggestions, show_examples=not (q or supplier or state),
    ))


def product_detail(request, code):
    product = get_object_or_404(Product, code=code)
    if product.status == Product.Status.RETIRED and product.merged_into:
        return redirect(product.merged_into)
    members = list(product.items.select_related("supplier", "current_record")
                   .prefetch_related("part_numbers", "offers__source_record").order_by(
                       "supplier__code", "supplier_part_no"))
    for m in members:
        m.current_offer = next((o for o in m.offers.all() if o.is_current), None)
    ids = [m.pk for m in members]
    edges = MatchCandidate.objects.filter(
        Q(item_a__in=ids) | Q(item_b__in=ids)).exclude(
        status__in=[MatchCandidate.Status.SUPERSEDED]).select_related(
        "item_a__supplier", "item_b__supplier", "item_a__product", "item_b__product")
    internal = [e for e in edges if e.item_a_id in ids and e.item_b_id in ids
                and e.status == MatchCandidate.Status.ACCEPTED]
    external = [e for e in edges if not (e.item_a_id in ids and e.item_b_id in ids)
                and e.status in selectors.OPEN_CANDIDATE]
    flags = selectors.review_flags()
    state, has_open = selectors.product_state(len(members), ids, flags)
    return render(request, "catalog/product_detail.html", {
        "product": product, "members": members, "internal": internal, "external": external,
        "state": state, "has_open": has_open,
        "issues": Issue.objects.filter(item__in=ids, status=Issue.Status.OPEN)
        .select_related("item__supplier"),
    })


def item_detail(request, pk):
    item = get_object_or_404(SupplierItem.objects.select_related(
        "supplier", "product", "current_record__batch__source_file"), pk=pk)
    values = FieldValue.objects.filter(item=item).select_related(
        "source_record__batch__source_file").order_by("field", "-is_current", "-created")
    # Rows are matched on the normalized number, so earlier spellings (A-03-L vs A03L) show too.
    key = normalize_number(item.supplier_part_no)
    observations = [r for r in item.supplier.records.select_related("batch__source_file")
                    .order_by("-batch_id") if normalize_number(r.supplier_part_no) == key]
    return render(request, "catalog/item_detail.html", {
        "item": item, "values": values, "observations": observations,
        "offers": item.offers.select_related("source_record").all(),
        "issues": item.issues.all(),
        "candidates": MatchCandidate.objects.filter(Q(item_a=item) | Q(item_b=item))
        .exclude(status=MatchCandidate.Status.SUPERSEDED)
        .select_related("item_a__supplier", "item_b__supplier"),
    })


def source_download(request, pk):
    source = get_object_or_404(SourceFile, pk=pk)
    try:
        handle = source.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("原始文件缺失") from exc
    return FileResponse(handle, as_attachment=True, filename=source.original_name)
