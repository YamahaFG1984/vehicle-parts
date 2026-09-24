from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path

from apps.core.views import LoginView

urlpatterns = [
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("imports/", include("apps.ingestion.urls")),
    path("review/", include("apps.matching.urls")),
    path("exports/", include("apps.exports.urls")),
    path("", include("apps.catalog.urls")),
]
