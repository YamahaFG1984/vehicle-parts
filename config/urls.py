from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("imports/", include("apps.ingestion.urls")),
    path("review/", include("apps.matching.urls")),
    path("exports/", include("apps.exports.urls")),
    path("", include("apps.catalog.urls")),
]
