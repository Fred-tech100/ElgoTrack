import time

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.hashers import check_password  # noqa: F401
from django.db.models import Q
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.conf import settings
from django.views.generic import CreateView, FormView, ListView, TemplateView, UpdateView

from audit.services import log_action
from core.mixins import BusinessFormMixin, BusinessQuerysetMixin, TenantRequiredMixin
from core.permissions import PERMISSION_GROUPS

from . import services
from core import qr
from core.utils import client_ip

from . import throttle, totp
from core.crypto import encrypt
from .forms import (
    RoleForm,
    SecurityActionForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    ThrottledAuthenticationForm,
    TwoFactorForm,
    UserForm,
)
from .models import Role, User


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = ThrottledAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        if user.totp_enabled:
            # Password was right, but the user is not signed in until the second factor is verified.
            self.request.session["pending_2fa"] = {"id": user.pk, "ts": time.time(), "next": self.get_redirect_url(),
                                                   "backend": getattr(user, "backend", "django.contrib.auth.backends.ModelBackend")}
            return redirect("accounts:two_factor")
        return super().form_valid(form)

    def get_success_url(self):
        url = self.get_redirect_url()
        if url:
            return url
        return reverse("platform:dashboard") if self.request.user.is_superuser else reverse("core:home")


class UserListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = ("users.manage", "roles.manage")
    model = User
    template_name = "accounts/user_list.html"
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().select_related("role").order_by("full_name")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["roles"] = Role.objects.filter(business=self.request.business)
        return ctx


# Users are tenant-scoped through the ``business`` FK, so reuse the mixin.
class UserCreateView(TenantRequiredMixin, BusinessFormMixin, CreateView):
    permission_required = "users.manage"
    model = User
    form_class = UserForm
    template_name = "components/form_page.html"
    success_url = reverse_lazy("accounts:users")
    extra_context = {"title": "Add user", "cancel_url": reverse_lazy("accounts:users"),
                     "intro": "We will email the new user a link to set their password.", "submit_label": "Create user"}

    def form_valid(self, form):
        form.instance.business = self.request.business
        user = form.save(commit=False)
        user.set_unusable_password()
        user.save()
        services.send_invitation(self.request, user, inviter=self.request.user)
        log_action("user.create", obj=user, after={"email": user.email, "role": user.role.name})
        messages.success(self.request, f"{user.full_name} was added. Their invitation is being delivered.")
        return redirect(self.success_url)


class UserUpdateView(TenantRequiredMixin, BusinessQuerysetMixin, BusinessFormMixin, UpdateView):
    permission_required = "users.manage"
    model = User
    form_class = UserForm
    template_name = "components/form_page.html"
    success_url = reverse_lazy("accounts:users")
    extra_context = {"title": "Edit user", "cancel_url": reverse_lazy("accounts:users"), "submit_label": "Save changes"}

    def form_valid(self, form):
        if self.object == self.request.user and not form.cleaned_data["is_active"]:
            form.add_error("is_active", "You cannot deactivate your own account.")
            return self.form_invalid(form)
        before = {"role": User.objects.get(pk=self.object.pk).role.name}
        response = super().form_valid(form)
        log_action("user.update", obj=self.object, before=before,
                   after={"role": self.object.role.name, "active": self.object.is_active})
        if before["role"] != self.object.role.name:
            from emailing.services import queue_email

            app_url = f"{settings.ELGOTRACK_PUBLIC_URL}{reverse('accounts:security')}" if settings.ELGOTRACK_PUBLIC_URL else ""
            queue_email(
                email_type="security_notification",
                recipient=self.object.email,
                subject="Your ElgoTrack role was changed",
                template="security_notification",
                context={
                    "first_name": self.object.get_short_name(),
                    "message": f"Your role at {self.object.business.name} was changed to {self.object.role.name}.",
                    "cta_label": "Review account",
                    "cta_url": app_url,
                },
                business=self.object.business,
                user=self.object,
                related=self.object,
            )
        messages.success(self.request, "User updated.")
        return response


class ResendInvitationView(TenantRequiredMixin, View):
    permission_required = "users.manage"

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk, business=request.business)
        services.send_invitation(request, user, inviter=request.user)
        messages.success(request, f"An invitation is being delivered to {user.email}.")
        return redirect("accounts:users")


class RoleListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "roles.manage"
    model = Role
    template_name = "accounts/role_list.html"


class RoleCreateView(TenantRequiredMixin, BusinessFormMixin, CreateView):
    permission_required = "roles.manage"
    model = Role
    form_class = RoleForm
    template_name = "components/form_page.html"
    extra_context = {"title": "New role", "cancel_url": reverse_lazy("accounts:roles"), "submit_label": "Create role"}

    def get_success_url(self):
        return reverse("accounts:role_edit", args=[self.object.pk])


class RoleEditView(TenantRequiredMixin, BusinessQuerysetMixin, TemplateView):
    permission_required = "roles.manage"
    template_name = "accounts/role_edit.html"

    def get_role(self):
        return get_object_or_404(Role, pk=self.kwargs["pk"], business=self.request.business)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        role = self.get_role()
        ctx.update(role=role, groups=PERMISSION_GROUPS, granted=role.codes())
        return ctx

    def post(self, request, pk):
        role = self.get_role()
        if role.is_owner:
            raise PermissionDenied
        codes = set(request.POST.getlist("codes"))
        valid = {c for _, items in PERMISSION_GROUPS for c, _ in items}
        before = sorted(role.codes())
        services.set_role_permissions(role, codes & valid)
        log_action("role.permissions", obj=role, before={"codes": before}, after={"codes": sorted(codes & valid)})
        messages.success(request, f"Permissions for {role.name} saved.")
        return redirect("accounts:roles")


# ------------------------------------------------------------------ two-factor authentication
PENDING_SECONDS = 5 * 60


def _verify_second_factor(user, code, request):
    """Accept a TOTP code (once per time step) or a one-time recovery code."""
    code = (code or "").strip()
    step = totp.verify(user.totp_secret(), code, last_step=user.totp_last_step)
    if step is not None:
        # Atomic replay guard: only the first request to claim this step wins.
        claimed = User.objects.filter(pk=user.pk).filter(Q(totp_last_step__isnull=True) | Q(totp_last_step__lt=step)).update(totp_last_step=step)
        return bool(claimed)
    return user.use_recovery_code(code) if "-" in code else False


class TwoFactorView(FormView):
    template_name = "accounts/two_factor.html"
    form_class = TwoFactorForm

    def dispatch(self, request, *args, **kwargs):
        pending = request.session.get("pending_2fa")
        if not pending or time.time() - pending["ts"] > PENDING_SECONDS:
            request.session.pop("pending_2fa", None)
            messages.error(request, "Your sign-in timed out. Please sign in again.")
            return redirect("accounts:login")
        self.pending = pending
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = get_object_or_404(User, pk=self.pending["id"], is_active=True)
        ip = client_ip(self.request)
        key = f"2fa:{user.pk}"
        if throttle.is_locked(key, ip):
            form.add_error("code", "Too many attempts. Please wait 15 minutes and try again.")
            return self.form_invalid(form)
        if not _verify_second_factor(user, form.cleaned_data["code"], self.request):
            throttle.register_failure(key, ip)
            log_action("auth.2fa_failed", user=user, business=user.business, obj=user)
            form.add_error("code", "That code is not valid. Check the time on your phone and try again.")
            return self.form_invalid(form)
        throttle.clear(key, ip)
        self.request.session.pop("pending_2fa", None)
        login(self.request, user, backend=self.pending["backend"])
        log_action("auth.2fa_success", user=user, business=user.business, obj=user)
        target = self.pending.get("next") or (reverse("platform:dashboard") if user.is_superuser else reverse("core:home"))
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={self.request.get_host()}):
            target = reverse("core:home")
        return redirect(target)


class SecurityView(LoginRequiredMixin, TemplateView):
    """Account security: set up / disable two-factor authentication, recovery codes."""

    template_name = "accounts/security.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        user = self.request.user
        pending = self.request.session.get("totp_pending")
        ctx.update(enabled=user.totp_enabled, recovery_left=len(user.totp_recovery_hashes),
                   recovery_codes=self.request.session.pop("recovery_codes_once", None),
                   forced=bool(getattr(self.request, "business", None) and self.request.business.require_2fa) or user.is_superuser,
                   action_form=SecurityActionForm(), code_form=TwoFactorForm())
        if pending and not user.totp_enabled:
            uri = totp.provisioning_uri(pending, user.email, issuer=(self.request.business.name if getattr(self.request, "business", None) else "ElgonNova"))
            ctx.update(pending_secret=" ".join(pending[i:i + 4] for i in range(0, len(pending), 4)), qr_svg=qr.svg_inline(uri, scale=5, title="Authenticator QR code"))
        return ctx

    def _reauth(self, request):
        form = SecurityActionForm(request.POST)
        if not form.is_valid() or not request.user.check_password(form.cleaned_data["password"]):
            messages.error(request, "Your password was not correct.")
            return None
        return form

    def post(self, request):
        user, action = request.user, request.POST.get("action")
        ip = client_ip(request)
        if action == "start" and not user.totp_enabled:
            request.session["totp_pending"] = totp.new_secret()
        elif action == "cancel":
            request.session.pop("totp_pending", None)
        elif action == "enable" and not user.totp_enabled:
            secret = request.session.get("totp_pending")
            step = totp.verify(secret, request.POST.get("code", "")) if secret else None
            if step is None:
                messages.error(request, "That code did not match. Check your phone's clock and try again.")
            else:
                codes = totp.new_recovery_codes()
                user.totp_secret_enc, user.totp_enabled, user.totp_last_step = encrypt(secret, "totp"), True, step
                user.set_recovery_codes(codes)
                user.save(update_fields=["totp_secret_enc", "totp_enabled", "totp_last_step", "totp_recovery_hashes"])
                request.session.pop("totp_pending", None)
                request.session["recovery_codes_once"] = codes
                log_action("auth.2fa_enabled", user=user, business=user.business, obj=user)
                messages.success(request, "Two-factor authentication is on. Save your recovery codes now.")
        elif action in ("disable", "regenerate") and user.totp_enabled:
            if throttle.is_locked(f"2faset:{user.pk}", ip):
                messages.error(request, "Too many attempts. Please wait and try again.")
            else:
                form = self._reauth(request)
                code_ok = form is not None and _verify_second_factor(user, form.cleaned_data["code"], request)
                if form is not None and not code_ok:
                    messages.error(request, "That authentication code is not valid.")
                if form is None or not code_ok:
                    throttle.register_failure(f"2faset:{user.pk}", ip)
                elif action == "disable":
                    forced = (user.is_superuser and settings.REQUIRE_2FA_PLATFORM_ADMIN) or (getattr(request, "business", None) and request.business.require_2fa)
                    if forced:
                        messages.error(request, "Your organisation requires two-factor authentication, so it cannot be turned off.")
                    else:
                        user.clear_two_factor()
                        log_action("auth.2fa_disabled", user=user, business=user.business, obj=user)
                        messages.success(request, "Two-factor authentication is off.")
                else:
                    codes = totp.new_recovery_codes()
                    user.set_recovery_codes(codes)
                    user.save(update_fields=["totp_recovery_hashes"])
                    request.session["recovery_codes_once"] = codes
                    log_action("auth.2fa_recovery_regenerated", user=user, business=user.business, obj=user)
        return redirect("accounts:security")


class ResetTwoFactorView(TenantRequiredMixin, View):
    """A business owner can clear a team member's two-factor set-up (lost phone)."""

    permission_required = "users.manage"

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk, business=request.business)
        user.clear_two_factor()
        log_action("auth.2fa_reset", obj=user, message=f"Reset by {request.user}")
        messages.success(request, f"Two-factor authentication was reset for {user.full_name}. They must set it up again.")
        return redirect("accounts:users")


class AccountPasswordChangeView(auth_views.PasswordChangeView):
    form_class = StyledPasswordChangeForm
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("core:home")

    def form_valid(self, form):
        response = super().form_valid(form)
        from emailing.services import send_password_changed

        send_password_changed(self.request.user)
        return response


class AccountPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    form_class = StyledSetPasswordForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        from emailing.services import send_password_changed

        send_password_changed(self.user)
        return response
