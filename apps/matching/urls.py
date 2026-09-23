from django.urls import path

from . import views

app_name = "matching"
urlpatterns = [
    path("", views.review_queue, name="review_queue"),
    path("<int:pk>/", views.candidate_detail, name="candidate_detail"),
    path("issues/", views.issue_list, name="issue_list"),
    path("issues/<int:pk>/decide/", views.issue_decide, name="issue_decide"),
]
