from django.urls import path

from reports.views import DashboardView

from .search import SearchView

from . import views

app_name = "core"
urlpatterns = [
    # path("", DashboardView.as_view(), name="home"),
    path("", views.home, name="home"),
    path("search/", SearchView.as_view(), name="search"),
    path("health/", views.health, name="health"),
    path("health/ready/", views.ready, name="ready"),
]
