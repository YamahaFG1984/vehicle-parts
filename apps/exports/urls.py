from django.urls import path

from . import views

app_name = "exports"
urlpatterns = [path("<str:key>/", views.download, name="download")]
