from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.urls import reverse

from core.forms import BusinessModelForm, StyledFormMixin
from core.utils import client_ip

from . import throttle
from .models import Role, User


class ThrottledAuthenticationForm(StyledFormMixin, AuthenticationForm):
    username = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean(self):
        email = self.cleaned_data.get("username", "")
        ip = client_ip(self.request)
        if throttle.is_locked(email, ip):
            raise forms.ValidationError(
                "Too many failed sign-in attempts. Please wait 15 minutes and try again.", code="locked"
            )
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            throttle.register_failure(email, ip)
            raise
        throttle.clear(email, ip)
        return cleaned


class UserForm(BusinessModelForm):
    class Meta:
        model = User
        fields = ["full_name", "email", "phone", "role", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].required = True
        self.fields["role"].queryset = Role.objects.filter(business=self.business)
        if self.instance.pk:
            self.fields["email"].disabled = True

    def clean_email(self):
        return self.cleaned_data["email"].lower()


class RoleForm(BusinessModelForm):
    class Meta:
        model = Role
        fields = ["name", "description"]


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from emailing.services import queue_email

        reset_path = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": context["uid"], "token": context["token"]},
        )
        reset_url = f'{context["protocol"]}://{context["domain"]}{reset_path}'
        user = context["user"]
        queue_email(
            email_type="password_reset",
            recipient=to_email,
            subject="Reset your ElgoTrack password",
            template="password_reset",
            context={
                "first_name": user.get_short_name(),
                "cta_label": "Reset password",
                "cta_url": reset_url,
                "expiry_days": max(1, (settings.PASSWORD_RESET_TIMEOUT + 86399) // 86400),
            },
            business=user.business,
            user=user,
            related=user,
        )


class TwoFactorForm(StyledFormMixin, forms.Form):
    code = forms.CharField(label="Authentication code", max_length=20, widget=forms.TextInput(
        attrs={"autocomplete": "one-time-code", "inputmode": "text", "autofocus": True, "placeholder": "123456"}),
        help_text="Enter the 6-digit code from your authenticator app, or one of your recovery codes.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class SecurityActionForm(StyledFormMixin, forms.Form):
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}), label="Your password")
    code = forms.CharField(label="Authentication code", max_length=20, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
