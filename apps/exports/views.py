import io

from django.http import FileResponse, Http404

from .exporters import EXPORTS, export_search, scope_from_search, search_filename


def _xlsx(buffer: io.BytesIO, filename: str) -> FileResponse:
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=filename)


def download(request, key):
    """Full-catalog exports."""
    if key not in EXPORTS:
        raise Http404
    name, fn = EXPORTS[key]
    buffer = io.BytesIO()
    fn(buffer)  # openpyxl saves to any binary file-like object
    return _xlsx(buffer, name)


def search_results(request):
    """Export exactly what the search page shows for the same q / supplier / state."""
    scope = scope_from_search(request.GET.get("q", "").strip(),
                              request.GET.get("supplier", "").strip(),
                              request.GET.get("state", "").strip())
    buffer = io.BytesIO()
    export_search(buffer, scope)
    return _xlsx(buffer, search_filename(scope.criteria))
