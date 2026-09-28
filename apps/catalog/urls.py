from django.urls import path

from . import views

app_name = "catalog"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("search/", views.search, name="search"),
    path("products/<str:code>/", views.product_detail, name="product_detail"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
    path("items/<int:pk>/images/", views.item_images_upload, name="item_images_upload"),
    path("items/<int:pk>/stock/", views.item_stock, name="item_stock"),
    path("images/<int:pk>/", views.item_image, name="item_image"),
    path("images/<int:pk>/primary/", views.item_image_primary, name="item_image_primary"),
    path("images/<int:pk>/delete/", views.item_image_delete, name="item_image_delete"),
    path("sources/<int:pk>/download/", views.source_download, name="source_download"),
    path("stock/import/", views.stock_import, name="stock_import"),
    path("stock/imports/<int:pk>/", views.stock_import_detail, name="stock_import_detail"),
]
