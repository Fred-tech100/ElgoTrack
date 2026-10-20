from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from businesses.views import RegisterView
from core.ratelimit import rate_limit

from . import forms, views

app_name = "accounts"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("register/", rate_limit("register", 5)(RegisterView.as_view()), name="register"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", rate_limit("pwreset", 8)(auth_views.PasswordResetView.as_view(
        form_class=forms.StyledPasswordResetForm,
        template_name="accounts/password_reset_form.html",
        email_template_name="accounts/password_reset_email.txt",
        subject_template_name="accounts/password_reset_subject.txt",
        success_url=reverse_lazy("accounts:password_reset_done"))), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.AccountPasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("password-change/", views.AccountPasswordChangeView.as_view(), name="password_change"),
    path("2fa/", views.TwoFactorView.as_view(), name="two_factor"),
    path("security/", views.SecurityView.as_view(), name="security"),
    path("users/<int:pk>/reset-2fa/", views.ResetTwoFactorView.as_view(), name="user_reset_2fa"),
    path("users/", views.UserListView.as_view(), name="users"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_edit"),
    path("users/<int:pk>/invite/", views.ResendInvitationView.as_view(), name="user_invite"),
    path("roles/", views.RoleListView.as_view(), name="roles"),
    path("roles/new/", views.RoleCreateView.as_view(), name="role_create"),
    path("roles/<int:pk>/", views.RoleEditView.as_view(), name="role_edit"),
]
