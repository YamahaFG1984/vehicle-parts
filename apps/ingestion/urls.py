from django.urls import path

from . import views

app_name = "ingestion"
urlpatterns = [
    path("", views.batch_list, name="batch_list"),
    path("<int:pk>/", views.batch_detail, name="batch_detail"),
]
