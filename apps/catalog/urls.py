from django.urls import path

from . import views

app_name = "catalog"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("search/", views.search, name="search"),
    path("products/<str:code>/", views.product_detail, name="product_detail"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
    path("sources/<int:pk>/download/", views.source_download, name="source_download"),
]
