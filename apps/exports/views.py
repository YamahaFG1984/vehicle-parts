import io

from django.http import FileResponse, Http404

from .exporters import EXPORTS


def download(request, key):
    if key not in EXPORTS:
        raise Http404
    name, fn = EXPORTS[key]
    buffer = io.BytesIO()
    fn(buffer)  # openpyxl saves to any binary file-like object
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=name)
