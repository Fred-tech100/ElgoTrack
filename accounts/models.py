from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.permissions import ALL_CODES


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._create(email, password, **extra)


class Role(TimeStampedModel):
    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    system_key = models.CharField(max_length=30, blank=True, help_text="Set for built-in roles (owner, manager...).")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="uniq_role_name_per_business")]
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_owner(self):
        return self.system_key == "owner"

    def codes(self):
        if self.is_owner:
            return set(ALL_CODES)
        return set(self.permissions.values_list("codename", flat=True))


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    codename = models.CharField(max_length=60)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "codename"], name="uniq_role_permission")]

    def __str__(self):
        return f"{self.role_id}:{self.codename}"


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)
    business = models.ForeignKey(
        "businesses.Business", null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    # Optional two-factor authentication (authenticator app). The seed is stored encrypted.
    totp_enabled = models.BooleanField(default=False)
    totp_secret_enc = models.TextField(blank=True)
    totp_last_step = models.BigIntegerField(null=True, blank=True)
    totp_recovery_hashes = models.JSONField(default=list, blank=True)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(is_superuser=True) | models.Q(business__isnull=False),
                name="business_user_needs_business",
            )
        ]

    def __str__(self):
        return self.full_name or self.email

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email

    def perm_codes(self):
        if not hasattr(self, "_perm_codes"):
            if self.is_superuser or self.role_id is None:
                self._perm_codes = set()
            else:
                self._perm_codes = self.role.codes()
        return self._perm_codes

    def totp_secret(self):
        from core.crypto import decrypt

        return decrypt(self.totp_secret_enc, "totp") if self.totp_secret_enc else ""

    def set_recovery_codes(self, codes):
        from core.crypto import keyed_hash

        self.totp_recovery_hashes = [keyed_hash(c, "recovery") for c in codes]

    def use_recovery_code(self, code):
        """Consume a one-time recovery code. Returns True if it was valid."""
        from core.crypto import keyed_hash

        digest = keyed_hash(code.strip().lower(), "recovery")
        if digest in self.totp_recovery_hashes:
            self.totp_recovery_hashes = [h for h in self.totp_recovery_hashes if h != digest]
            self.save(update_fields=["totp_recovery_hashes"])
            return True
        return False

    def clear_two_factor(self):
        self.totp_enabled, self.totp_secret_enc, self.totp_last_step, self.totp_recovery_hashes = False, "", None, []
        self.save(update_fields=["totp_enabled", "totp_secret_enc", "totp_last_step", "totp_recovery_hashes"])

    def has_business_perm(self, code):
        return code in self.perm_codes()
