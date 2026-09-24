from django.urls import path

from . import views

app_name = "exports"
urlpatterns = [
    path("search/", views.search_results, name="search"),
    path("<str:key>/", views.download, name="download"),
]
