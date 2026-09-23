import tempfile
from pathlib import Path

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.matching.engine import run_matching

from .forms import UploadForm
from .models import ImportBatch
from .services import ImportFailed, import_source


@require_http_methods(["GET", "POST"])
def batch_list(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        with tempfile.TemporaryDirectory() as tmp:
            # Keep the original extension: file type detection and readers rely on it.
            path = Path(tmp) / f"upload{Path(upload.name).suffix.lower()}"
            with path.open("wb") as fh:
                for chunk in upload.chunks():
                    fh.write(chunk)
            try:
                result = import_source(
                    path, form.cleaned_data["supplier"] or None,
                    supplier_name=form.cleaned_data["supplier_name"] or None,
                    partial=form.cleaned_data["partial"], user=request.user,
                    original_name=upload.name,
                )
            except ImportFailed as exc:
                form.add_error("file", str(exc))
                result = None
        if result:
            if result.duplicate:
                messages.warning(request, "该文件内容已导入过，未产生任何数据变化。")
            else:
                summary = run_matching()
                messages.success(request, f"导入完成，已重新匹配：{summary.as_text()}")
            return redirect("ingestion:batch_detail", pk=result.batch.pk)
    batches = ImportBatch.objects.select_related("source_file__supplier", "created_by")
    return render(request, "ingestion/batch_list.html", {"form": form, "batches": batches})


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
