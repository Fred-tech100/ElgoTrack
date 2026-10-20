from django.urls import path

from . import views

app_name = "reports"
urlpatterns = [
    path("", views.ReportIndexView.as_view(), name="index"),
    path("daily/", views.DailySummaryView.as_view(), name="daily"),
    path("summary/", views.FinancialSummaryView.as_view(), name="summary"),
    path("builder/", views.BuilderView.as_view(), name="builder"),
    path("saved/<int:pk>/", views.SavedReportView.as_view(), name="saved"),
    path("r/<slug:key>/", views.ReportRunView.as_view(), name="run"),
]
