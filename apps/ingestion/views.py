import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.core import rules
from apps.matching.engine import run_matching

from .forms import UploadForm
from .mapping import MappingOverride
from .models import ImportBatch
from .services import ImportFailed, import_source

PENDING_DIR = "pending_uploads"
SESSION_KEY = "pending_imports"


def _pending(request, token):
    pending = request.session.get(SESSION_KEY, {}).get(token)
    if not pending or not Path(pending["path"]).exists():
        raise Http404("预览已过期，请重新上传")
    return pending


def _save_pending(request, token, data):
    store = request.session.get(SESSION_KEY, {})
    store[token] = data
    request.session[SESSION_KEY] = store


def _drop_pending(request, token):
    store = request.session.get(SESSION_KEY, {})
    data = store.pop(token, None)
    request.session[SESSION_KEY] = store
    if data:
        Path(data["path"]).unlink(missing_ok=True)


def _override(pending) -> MappingOverride | None:
    columns, header_row = pending.get("columns") or {}, pending.get("header_row")
    if not columns and not header_row:
        return None
    return MappingOverride(columns=columns, header_row=header_row)


@require_http_methods(["GET", "POST"])
def batch_list(request):
    """Upload always goes to a preview first; nothing is stored until the user confirms."""
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        token = uuid.uuid4().hex
        folder = Path(settings.MEDIA_ROOT) / PENDING_DIR
        folder.mkdir(parents=True, exist_ok=True)
        # Keep the original extension: file type detection relies on it.
        path = folder / f"{token}{Path(upload.name).suffix.lower()}"
        with path.open("wb") as fh:
            for chunk in upload.chunks():
                fh.write(chunk)
        _save_pending(request, token, {
            "path": str(path), "name": upload.name,
            "supplier": form.cleaned_data["supplier"] or None,
            "supplier_name": form.cleaned_data["supplier_name"] or None,
            "partial": form.cleaned_data["partial"], "columns": {}, "header_row": None,
        })
        return redirect("ingestion:preview", token=token)
    batches = ImportBatch.objects.select_related("source_file__supplier", "created_by")
    return render(request, "ingestion/batch_list.html", {"form": form, "batches": batches})


@require_http_methods(["GET", "POST"])
def preview(request, token):
    pending = _pending(request, token)
    error = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            _drop_pending(request, token)
            messages.info(request, "已取消，未写入任何数据。")
            return redirect("ingestion:batch_list")
        # Column choices and header row from the form become a mapping override.
        columns = {}
        for key, header in request.POST.items():
            if key.startswith("hdr_"):
                columns[header] = request.POST.get("map_" + key[4:], "")
        header_row = request.POST.get("header_row", "").strip()
        pending["columns"] = columns
        pending["header_row"] = int(header_row) if header_row.isdigit() else None
        _save_pending(request, token, pending)
        if action == "confirm":
            try:
                result = import_source(
                    pending["path"], pending["supplier"], supplier_name=pending["supplier_name"],
                    override=_override(pending), partial=pending["partial"],
                    reprocess=request.POST.get("reprocess") == "1", user=request.user,
                    original_name=pending["name"])
            except ImportFailed as exc:
                error = str(exc)
            else:
                _drop_pending(request, token)
                if result.duplicate:
                    messages.warning(request, "该文件内容已导入过，未产生任何数据变化。")
                else:
                    summary = run_matching()
                    messages.success(request, f"导入完成，已重新匹配：{summary.as_text()}")
                return redirect("ingestion:batch_detail", pk=result.batch.pk)
        else:
            return redirect("ingestion:preview", token=token)
    try:
        result = import_source(
            pending["path"], pending["supplier"], supplier_name=pending["supplier_name"],
            override=_override(pending), partial=pending["partial"], dry_run=True,
            original_name=pending["name"])
    except ImportFailed as exc:
        result, error = None, error or str(exc)
    headers = []
    if result:
        seen = set()
        for m in result.parse.mappings:
            for h in m["headers"]:
                if h and h not in seen:
                    seen.add(h)
                    headers.append({"header": h, "field": next(
                        (f for hh, f in m["columns"].items() if hh == h), "")})
    fields = list(rules.column_aliases()["fields"])
    return render(request, "ingestion/preview.html", {
        "token": token, "pending": pending, "result": result, "error": error,
        "headers": headers, "fields": fields,
        "rows": result.preview[:60] if result else [],
        "has_part_no": any(h["field"] == "supplier_part_no" for h in headers),
    })


def batch_detail(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related("source_file__supplier"), pk=pk)
    status = request.GET.get("status", "")
    records = batch.records.prefetch_related("issues").order_by("pk")
    counts = dict(batch.records.values_list("diff_status").annotate(n=Count("id")))
    if status:
        records = records.filter(diff_status=status)
    return render(request, "ingestion/batch_detail.html", {
        "batch": batch, "records": records, "status": status, "counts": counts,
        "file_issues": batch.issues.filter(source_record__isnull=True),
    })
