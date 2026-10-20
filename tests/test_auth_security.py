import time
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from accounts import totp
from accounts.models import User
from audit.models import AuditLog
from core.crypto import decrypt, encrypt

from .helpers import PASSWORD, TenantTestCase, make_user


def code_for(user, offset=0, now=None):
    return totp._hotp(user.totp_secret(), int((now or time.time()) // 30) + offset)


class PasswordUiTests(TenantTestCase):
    REG = {"name": "New Shop", "business_type": "retail", "owner_name": "Nina", "owner_email": "nina@new.test",
           "owner_phone": "+256700000001"}

    def test_eye_toggle_and_confirm_on_registration(self):
        r = self.client.get(reverse("accounts:register"))
        self.assertContains(r, "data-toggle-password", count=2)  # password + confirm
        self.assertContains(r, 'name="password_confirm"')
        self.assertContains(r, 'name="owner_phone"')
        self.assertContains(r, 'aria-label="Show password"')
        self.assertContains(r, "js/app.js")  # the script that powers the toggle

    def test_owner_phone_is_required_for_registration(self):
        data = {**self.REG, "password": PASSWORD, "password_confirm": PASSWORD}
        data.pop("owner_phone")
        response = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner&#x27;s phone number")
        self.assertFalse(User.objects.filter(email=self.REG["owner_email"]).exists())

    def test_eye_toggle_on_login_reset_and_change_pages(self):
        self.assertContains(self.client.get(reverse("accounts:login")), "data-toggle-password")
        self.login(self.owner)
        r = self.client.get(reverse("accounts:password_change"))
        self.assertContains(r, "data-toggle-password", count=3)
        self.assertContains(r, 'class="input pr-11"')  # styled, with room for the icon

    def test_confirm_password_must_match(self):
        r = self.client.post(reverse("accounts:register"), {**self.REG, "password": PASSWORD, "password_confirm": PASSWORD + "x"})
        self.assertContains(r, "do not match")
        self.assertFalse(User.objects.filter(email="nina@new.test").exists())
        r = self.client.post(reverse("accounts:register"), {**self.REG, "password": PASSWORD})  # confirm missing
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("accounts:register"), {**self.REG, "password": PASSWORD, "password_confirm": PASSWORD})
        self.assertEqual(r.status_code, 302)

    def test_weak_or_personal_passwords_rejected(self):
        for weak in ("short", "password1234", "nina@new.test", "1234567890123"):
            r = self.client.post(reverse("accounts:register"), {**self.REG, "password": weak, "password_confirm": weak})
            self.assertEqual(r.status_code, 200, weak)
        self.assertFalse(User.objects.filter(email="nina@new.test").exists())

    def test_password_reset_confirm_form_is_styled_and_has_confirmation(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uid, token = urlsafe_base64_encode(force_bytes(self.owner.pk)), default_token_generator.make_token(self.owner)
        r = self.client.get(reverse("accounts:password_reset_confirm", args=[uid, token]), follow=True)
        self.assertContains(r, "data-toggle-password", count=2)
        post_url = r.redirect_chain[-1][0] if r.redirect_chain else reverse("accounts:password_reset_confirm", args=[uid, token])
        r = self.client.post(post_url, {"new_password1": "Another-Strong-Pass-77", "new_password2": "different"})
        self.assertContains(r, "didn")  # "The two password fields didn't match."


class TwoFactorTests(TenantTestCase):
    def enable(self, user):
        self.login(user)
        self.client.post(reverse("accounts:security"), {"action": "start"})
        secret = self.client.session["totp_pending"]
        step = int(time.time() // 30)
        r = self.client.post(reverse("accounts:security"), {"action": "enable", "code": totp._hotp(secret, step)}, follow=True)
        user.refresh_from_db()
        self.client.logout()
        return secret, r

    def test_rfc6238_vector_and_window_and_replay(self):
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(totp._hotp(secret, 59 // 30), "287082")
        self.assertEqual(totp.verify(secret, "287082", now=59), 1)
        self.assertEqual(totp.verify(secret, "287082", now=59 + 30), 1)          # +/- one step tolerated
        self.assertIsNone(totp.verify(secret, "287082", now=59 + 120))            # but not two
        self.assertIsNone(totp.verify(secret, "287082", last_step=1, now=59))     # replay refused
        self.assertIsNone(totp.verify(secret, "12345", now=59))
        self.assertIsNone(totp.verify("", "287082", now=59))

    def test_setup_flow_shows_qr_and_recovery_codes_once(self):
        self.login(self.owner)
        r = self.client.post(reverse("accounts:security"), {"action": "start"}, follow=True)
        self.assertContains(r, "<svg")
        self.assertContains(r, "Scan this QR code")
        bad = self.client.post(reverse("accounts:security"), {"action": "enable", "code": "000000"}, follow=True)
        self.assertContains(bad, "did not match")
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.totp_enabled)
        secret = self.client.session["totp_pending"]
        r = self.client.post(reverse("accounts:security"), {"action": "enable", "code": totp._hotp(secret, int(time.time() // 30))}, follow=True)
        self.assertContains(r, "recovery codes")
        self.assertEqual(len(r.context["recovery_codes"]), 8)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.totp_enabled)
        self.assertEqual(len(self.owner.totp_recovery_hashes), 8)
        self.assertNotIn(secret, self.owner.totp_secret_enc)          # encrypted at rest
        self.assertEqual(decrypt(self.owner.totp_secret_enc, "totp"), secret)
        self.assertNotContains(self.client.get(reverse("accounts:security")), r.context["recovery_codes"][0])  # shown once
        self.assertTrue(AuditLog.objects.filter(action="auth.2fa_enabled").exists())

    def test_login_requires_second_factor(self):
        secret, _ = self.enable(self.owner)
        r = self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.assertRedirects(r, reverse("accounts:two_factor"))
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 302)   # not signed in yet
        r = self.client.post(reverse("accounts:two_factor"), {"code": "123456"})
        self.assertContains(r, "not valid")
        self.assertTrue(AuditLog.objects.filter(action="auth.2fa_failed").exists())
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 302)
        good = code_for(self.owner, offset=1)
        r = self.client.post(reverse("accounts:two_factor"), {"code": good})
        self.assertRedirects(r, reverse("core:home"))
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)

    def test_code_cannot_be_replayed(self):
        self.enable(self.owner)
        self.owner.refresh_from_db()
        code = code_for(self.owner, offset=1)
        for expected in (302, 200):   # first use signs in; second use of the same code is refused
            self.client.logout()
            self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
            r = self.client.post(reverse("accounts:two_factor"), {"code": code})
            self.assertEqual(r.status_code, expected)

    def test_recovery_code_works_once(self):
        self.login(self.owner)
        self.client.post(reverse("accounts:security"), {"action": "start"})
        secret = self.client.session["totp_pending"]
        r = self.client.post(reverse("accounts:security"), {"action": "enable", "code": totp._hotp(secret, int(time.time() // 30))}, follow=True)
        codes = r.context["recovery_codes"]
        self.client.logout()
        self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.assertEqual(self.client.post(reverse("accounts:two_factor"), {"code": codes[0]}).status_code, 302)
        self.client.logout()
        self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.assertEqual(self.client.post(reverse("accounts:two_factor"), {"code": codes[0]}).status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(len(self.owner.totp_recovery_hashes), 7)

    def test_second_factor_is_throttled(self):
        self.enable(self.owner)
        self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        for _ in range(5):
            self.client.post(reverse("accounts:two_factor"), {"code": "000000"})
        self.owner.refresh_from_db()
        r = self.client.post(reverse("accounts:two_factor"), {"code": code_for(self.owner, 1)})  # even the right code is refused now
        self.assertContains(r, "Too many attempts")

    def test_pending_login_expires_and_cannot_be_forged(self):
        self.enable(self.owner)
        self.assertRedirects(self.client.get(reverse("accounts:two_factor")), reverse("accounts:login"))  # no pending login
        self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        with mock.patch("accounts.views.time.time", return_value=time.time() + 600):
            self.assertRedirects(self.client.get(reverse("accounts:two_factor")), reverse("accounts:login"))

    def test_disable_requires_password_and_code(self):
        self.enable(self.owner)
        self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.client.post(reverse("accounts:two_factor"), {"code": code_for(self.owner, 1)})
        self.client.post(reverse("accounts:security"), {"action": "disable", "password": "wrong", "code": code_for(self.owner, 1)})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.totp_enabled)
        with mock.patch("accounts.totp.time.time", return_value=time.time() + 90):
            self.client.post(reverse("accounts:security"), {"action": "disable", "password": PASSWORD, "code": totp._hotp(self.owner.totp_secret(), int(time.time() // 30))})
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.totp_enabled)
        self.assertEqual(self.owner.totp_secret_enc, "")

    def test_business_can_require_two_factor(self):
        self.business.require_2fa = True
        self.business.save()
        self.login(self.owner)
        r = self.client.get(reverse("products:list"))
        self.assertRedirects(r, reverse("accounts:security"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:security")).status_code, 200)   # set-up page is reachable
        self.assertEqual(self.client.get("/health/").status_code, 200)
        self.client.post(reverse("accounts:security"), {"action": "start"})
        secret = self.client.session["totp_pending"]
        self.client.post(reverse("accounts:security"), {"action": "enable", "code": totp._hotp(secret, int(time.time() // 30))})
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 200)
        # once required, it cannot be switched off by the user
        self.owner.refresh_from_db()
        with mock.patch("accounts.totp.time.time", return_value=time.time() + 90):
            self.client.post(reverse("accounts:security"), {"action": "disable", "password": PASSWORD, "code": totp._hotp(secret, int(time.time() // 30))})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.totp_enabled)
        # other businesses are unaffected
        self.login(self.other_owner)
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 200)

    @override_settings(REQUIRE_2FA_PLATFORM_ADMIN=True)
    def test_platform_admins_must_use_two_factor(self):
        root = User.objects.create_superuser(email="root@x.test", password=PASSWORD, full_name="Root")
        self.login(root)
        self.assertRedirects(self.client.get(reverse("platform:dashboard")), reverse("accounts:security"), fetch_redirect_response=False)

    def test_owner_and_platform_can_reset_a_lost_device(self):
        mgr = make_user(self.business, "manager", "mia@alpha.test")
        self.enable(mgr)
        mgr.refresh_from_db()
        self.assertTrue(mgr.totp_enabled)
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("accounts:users")), "Reset 2FA")
        self.client.post(reverse("accounts:user_reset_2fa", args=[mgr.pk]))
        mgr.refresh_from_db()
        self.assertFalse(mgr.totp_enabled)
        # a business owner cannot reset a user of another business
        self.enable(self.other_owner)
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse("accounts:user_reset_2fa", args=[self.other_owner.pk])).status_code, 404)
        root = User.objects.create_superuser(email="root@x.test", password=PASSWORD, full_name="Root")
        self.login(root)
        self.client.post(reverse("platform:user_reset_2fa", args=[self.other_owner.pk]))
        self.other_owner.refresh_from_db()
        self.assertFalse(self.other_owner.totp_enabled)

    def test_crypto_roundtrip_and_tamper(self):
        token = encrypt("JBSWY3DPEHPK3PXP", "totp")
        self.assertNotIn("JBSWY3DPEHPK3PXP", token)
        self.assertEqual(decrypt(token, "totp"), "JBSWY3DPEHPK3PXP")
        self.assertEqual(decrypt(token, "other-purpose"), "")
        self.assertEqual(decrypt(token[:-4] + "AAAA", "totp"), "")
