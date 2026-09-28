import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.ingestion.models import ImportBatch, SourceFile, Supplier
from apps.matching.models import Issue, MatchCandidate

from . import images, selectors, stock
from .forms import ImageUploadForm, StockForm, StockImportForm
from .models import FieldValue, ItemImage, Product, StockImport, StockLevel, SupplierItem
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
    stock_levels = list(StockLevel.objects.filter(item__in=ids, is_current=True)
                        .select_related("item__supplier").order_by(
                            "item__supplier__code", "item__supplier_part_no", "warehouse"))
    gallery = list(ItemImage.objects.filter(item__in=ids).select_related("item__supplier")
                   .order_by("-is_primary", "item__supplier__code", "item__supplier_part_no",
                             "created"))
    for m in members:
        m.primary_image = next((g for g in gallery if g.item_id == m.pk and g.is_primary), None)
        levels = [s for s in stock_levels if s.item_id == m.pk]
        m.stock_total = sum(s.quantity for s in levels) if levels else None
    # SKU table: sortable by price / MOQ / quote date / stock, filterable by currency.
    currencies = sorted({m.current_offer.currency for m in members
                         if m.current_offer and m.current_offer.currency})
    currency = request.GET.get("currency", "")
    currency = currency if currency in currencies else ""
    rows, sort = selectors.sort_members(members, request.GET.get("sort", ""), currency)

    def sort_url(field):
        nxt = f"-{field}" if sort == field else field  # click again to reverse
        return "?" + urlencode({k: v for k, v in (("sort", nxt), ("currency", currency)) if v})

    def currency_url(code):
        return "?" + urlencode({k: v for k, v in (("sort", sort), ("currency", code)) if v})

    return render(request, "catalog/product_detail.html", {
        "product": product, "members": members, "internal": internal, "external": external,
        "state": state, "has_open": has_open,
        "rows": rows, "sort": sort, "currency": currency,
        "sort_links": {f: sort_url(f) for f in selectors.MEMBER_SORTS},
        "currency_links": [("全部", currency_url(""), not currency)]
        + [(c, currency_url(c), c == currency) for c in currencies],
        "mixed_currency_sort": sort.lstrip("-") == "price" and not currency
        and len(currencies) > 1,
        "images": gallery,
        "stock_levels": stock_levels,
        "stock_total": sum(s.quantity for s in stock_levels) if stock_levels else None,
        "issues": Issue.objects.filter(item__in=ids, status=Issue.Status.OPEN)
        .select_related("item__supplier"),
    })


def item_detail(request, pk, image_form=None, stock_form=None):
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
        "images": item.images.all(),
        "image_form": image_form or ImageUploadForm(),
        "stock_levels": item.stock_levels.filter(is_current=True),
        "stock_history": item.stock_levels.filter(is_current=False)
        .select_related("recorded_by", "stock_import")[:20],
        "stock_form": stock_form or StockForm(),
    })


@require_POST
def item_images_upload(request, pk):
    item = get_object_or_404(SupplierItem, pk=pk)
    form = ImageUploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            added = images.add_images(item, form.cleaned_data["images"],
                                      source=form.cleaned_data["source"],
                                      caption=form.cleaned_data["caption"], user=request.user)
        except ValidationError as exc:
            form.add_error("images", exc)
        else:
            messages.success(request, f"已上传 {len(added)} 张图片。")
            return redirect(f"{item.get_absolute_url()}#images")
    return item_detail(request, pk, image_form=form)


@require_POST
def item_image_primary(request, pk):
    image = get_object_or_404(ItemImage.objects.select_related("item"), pk=pk)
    images.set_primary(image)
    messages.success(request, "已设为主图。")
    return redirect(f"{image.item.get_absolute_url()}#images")


@require_POST
def item_image_delete(request, pk):
    image = get_object_or_404(ItemImage.objects.select_related("item"), pk=pk)
    item = image.item
    images.delete_image(image)
    messages.success(request, "图片已删除。")
    return redirect(f"{item.get_absolute_url()}#images")


def item_image(request, pk):
    """Serve an image (login required like every page); ?size=thumb for the thumbnail."""
    image = get_object_or_404(ItemImage, pk=pk)
    f = image.thumbnail if request.GET.get("size") == "thumb" and image.thumbnail else image.image
    try:
        handle = f.open("rb")
    except FileNotFoundError as exc:
        raise Http404("图片文件缺失") from exc
    response = FileResponse(handle, content_type=mimetypes.guess_type(f.name)[0] or "image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response


@require_POST
def item_stock(request, pk):
    item = get_object_or_404(SupplierItem, pk=pk)
    form = StockForm(request.POST)
    if form.is_valid():
        level = stock.set_stock(item, form.cleaned_data["quantity"],
                                warehouse=form.cleaned_data["warehouse"],
                                as_of=form.cleaned_data["as_of"], note=form.cleaned_data["note"],
                                user=request.user)
        messages.success(request, "库存已更新。" if level else "库存与当前记录相同，未变化。")
        return redirect(f"{item.get_absolute_url()}#stock")
    return item_detail(request, pk, stock_form=form)


def source_download(request, pk):
    source = get_object_or_404(SourceFile, pk=pk)
    try:
        handle = source.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("原始文件缺失") from exc
    return FileResponse(handle, as_attachment=True, filename=source.original_name)


@require_POST
def stock_import(request):
    form = StockImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "库存表导入失败：" + "；".join(
            f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
        return redirect("ingestion:batch_list")
    upload = form.cleaned_data["file"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"stock{Path(upload.name).suffix.lower()}"
        with path.open("wb") as fh:
            for chunk in upload.chunks():
                fh.write(chunk)
        try:
            result = stock.import_stock(
                path, original_name=upload.name, supplier=form.cleaned_data["supplier"],
                warehouse=form.cleaned_data["warehouse"], as_of=form.cleaned_data["as_of"],
                user=request.user)
        except stock.StockImportError as exc:
            messages.error(request, f"库存表导入失败：{exc}")
            return redirect("ingestion:batch_list")
    messages.success(request, f"库存表已导入：{dict(result.stats)}")
    return redirect("catalog:stock_import_detail", pk=result.stock_import.pk)


def stock_import_detail(request, pk):
    record = get_object_or_404(StockImport, pk=pk)
    return render(request, "catalog/stock_import_detail.html", {
        "record": record,
        "levels": record.levels.select_related("item__supplier", "item__product")
        .order_by("item__supplier__code", "item__supplier_part_no"),
    })
