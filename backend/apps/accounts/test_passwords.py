"""Staff passwords: any non-empty text, case-insensitive, hashed (never plaintext); legacy exact-case hashes upgrade on sign-in."""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .passwords import check_user_password
from .services import set_password

LOGIN, CHANGE = "/api/auth/login/", "/api/auth/change-password/"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class StaffPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient(enforce_csrf_checks=True)

    def csrf(self):
        self.client.get("/api/auth/csrf/")
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["nexus_csrftoken"].value}

    def login(self, u, p):
        return self.client.post(LOGIN, {"username": u, "password": p}, format="json", **self.csrf())

    def test_seeded_accounts_sign_in_with_any_case_of_username_and_password(self):
        for u, p in [("Superadmin", "Superadmin"), ("SUPERADMIN", "superadmin"), ("superadmin", "SUPERADMIN"), ("Admin", "Admin"), ("admin", "ADMIN")]:
            self.assertEqual(self.login(u, p).status_code, 200, (u, p))

    def test_wrong_and_empty_passwords_are_rejected(self):
        self.assertEqual(self.login("Admin", "nope").status_code, 401)
        self.assertEqual(self.login("Admin", "").status_code, 400)
        self.assertEqual(self.login("Admin", "Admins").status_code, 401)

    def test_only_a_hash_is_stored_never_the_text(self):
        stored = get_user_model().objects.get(username="Admin").password
        self.assertTrue(stored.startswith(("pbkdf2_", "argon2", "bcrypt")))
        self.assertNotIn("admin", stored.lower().split("$")[-1][:5])

    def test_short_numeric_and_common_passwords_are_accepted_and_case_does_not_matter(self):
        user = get_user_model().objects.get(username="Admin")
        for pw in ("1234", "admin", "test", "a", "User"):
            set_password(user, pw)
            user.refresh_from_db()
            for variant in {pw, pw.upper(), pw.lower()}:
                self.assertTrue(check_user_password(user, variant), (pw, variant))
            self.assertFalse(check_user_password(user, pw + "x"))
        set_password(user, "user")
        self.assertEqual(self.login("Admin", "USER").status_code, 200)

    def test_change_password_accepts_any_non_empty_password_and_rejects_empty_or_same(self):
        self.login("Admin", "Admin")
        for bad in [("Admin", "", ""), ("Admin", "ADMIN", "ADMIN")]:
            r = self.client.post(CHANGE, dict(zip(("current_password", "new_password", "confirm_password"), bad)), format="json", **self.csrf())
            self.assertEqual(r.status_code, 400, bad)
        r = self.client.post(CHANGE, {"current_password": "ADMIN", "new_password": "1", "confirm_password": "1"}, format="json", **self.csrf())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.login("admin", "1").status_code, 200)
        self.assertEqual(self.login("Admin", "Admin").status_code, 401)

    def test_a_legacy_exact_case_hash_still_works_with_its_exact_text_and_is_upgraded(self):
        user = get_user_model().objects.get(username="Admin")
        user.password = make_password("OldPass")  # saved before passwords became case-insensitive
        user.save()
        self.assertEqual(self.login("Admin", "OldPass").status_code, 200)  # exact text: accepted ...
        user.refresh_from_db()
        self.assertTrue(user.check_password("oldpass"))  # ... and re-hashed in normalised form
        self.assertEqual(self.login("Admin", "OLDPASS").status_code, 200)

    def test_an_unmatchable_legacy_hash_is_refused_not_bypassed(self):
        user = get_user_model().objects.get(username="Admin")
        user.password = make_password("OldPass")
        user.save()
        self.assertEqual(self.login("Admin", "OLDPASS").status_code, 401)  # needs the exact old text or a password reset

    def test_reset_command_is_refused_without_debug(self):
        from django.core.management.base import CommandError

        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_initial_users", "--reset", verbosity=0)
