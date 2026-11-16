from django.core.cache import cache
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView
from rest_framework.response import Response

from .constants import Action
from .models import AdminPermission, UserModulePermission
from .permissions import HasModulePermission, IsSuperAdmin

User = get_user_model()

LOGIN, LOGOUT, ME, CHANGE, CSRF = (
    "/api/auth/login/", "/api/auth/logout/", "/api/auth/me/", "/api/auth/change-password/", "/api/auth/csrf/",
)


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class AuthTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient(enforce_csrf_checks=True)

    def _csrf(self):
        self.client.get(CSRF)
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["nexus_csrftoken"].value}

    def login(self, username="Superadmin", password="Superadmin"):
        return self.client.post(LOGIN, {"username": username, "password": password}, format="json", **self._csrf())

    # ---- seed
    def test_seed_is_idempotent_and_keeps_changed_password(self):
        user = User.objects.get(username="Admin")
        user.set_password("Changed#Pass9")
        user.save()
        call_command("seed_initial_users", verbosity=0)
        self.assertEqual(User.objects.count(), 2)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Changed#Pass9"))
        self.assertNotIn("Admin", user.password)  # stored hashed

    def test_repeated_wrong_passwords_are_rate_limited_and_the_right_one_is_blocked_meanwhile(self):
        from unittest.mock import patch

        from rest_framework.throttling import ScopedRateThrottle

        with patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "3/min"}):
            cache.clear()
            codes = [self.login(password="wrong").status_code for _ in range(3)]
            self.assertEqual(codes, [401, 401, 401])
            self.assertEqual(self.login(password="wrong").status_code, 429)
            self.assertEqual(self.login().status_code, 429)  # even the correct password waits out the window
        cache.clear()
        self.assertEqual(self.login().status_code, 200)

    # ---- login
    def test_both_accounts_login(self):
        for name in ("Superadmin", "Admin"):
            r = self.login(name, name)
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.json()["user"]["username"], name)
            self.assertNotIn("password", str(r.json()).lower())
            self.client.post(LOGOUT, **self._csrf())

    def test_username_case_insensitive(self):
        r = self.login("sUPERADMIN", "Superadmin")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["role"], "SUPERADMIN")

    def test_wrong_password_rejected(self):
        r = self.login("Admin", "nope")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["message"], "Invalid username or password.")

    def test_unknown_user_same_error(self):
        r = self.login("ghost", "x")
        self.assertEqual(r.json()["message"], "Invalid username or password.")

    def test_login_requires_csrf(self):
        r = self.client.post(LOGIN, {"username": "Admin", "password": "Admin"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_required_fields(self):
        r = self.login("", "")
        self.assertEqual(r.status_code, 400)
        self.assertIn("username", r.json()["errors"])

    # ---- session
    def test_anonymous_blocked(self):
        for method, url in (("get", ME), ("post", LOGOUT), ("post", CHANGE)):
            self.assertEqual(getattr(self.client, method)(url).status_code, 401, url)

    def test_me_and_logout_invalidate_session(self):
        self.login("Admin", "Admin")
        self.assertEqual(self.client.get(ME).json()["user"]["role"], "ADMIN")
        self.assertEqual(self.client.post(LOGOUT, **self._csrf()).status_code, 200)
        self.assertEqual(self.client.get(ME).status_code, 401)

    def test_api_responses_not_cacheable(self):
        self.assertIn("no-store", self.client.get(ME)["Cache-Control"])

    # ---- change password
    def test_change_password_flow(self):
        self.login("Admin", "Admin")
        other = APIClient()  # a second active session for the same user
        other.post(LOGIN, {"username": "Admin", "password": "Admin"}, format="json")
        csrf = self._csrf()
        payload = {"current_password": "Admin", "new_password": "N3w-Strong!Pass", "confirm_password": "N3w-Strong!Pass"}
        r = self.client.post(CHANGE, payload, format="json", **csrf)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.client.get(ME).status_code, 401)  # must sign in again
        self.assertEqual(self.login("Admin", "Admin").status_code, 401)  # old rejected
        self.assertEqual(self.login("Admin", "N3w-Strong!Pass").status_code, 200)  # new works

    def test_change_password_validation(self):
        self.login("Admin", "Admin")
        csrf = self._csrf()
        post = lambda d: self.client.post(CHANGE, d, format="json", **csrf).json()["errors"]
        self.assertIn("current_password", post({"current_password": "bad", "new_password": "N3w-Strong!Pass", "confirm_password": "N3w-Strong!Pass"}))
        self.assertIn("confirm_password", post({"current_password": "Admin", "new_password": "N3w-Strong!Pass", "confirm_password": "other"}))
        self.assertIn("new_password", post({"current_password": "Admin", "new_password": "", "confirm_password": ""}))  # empty is the only rejected password
        self.assertIn("new_password", post({"current_password": "Admin", "new_password": "ADMIN", "confirm_password": "ADMIN"}))  # same text, case-insensitively

    # ---- authorization
    def test_superadmin_only_permission(self):
        class Probe(APIView):
            permission_classes = [IsSuperAdmin]

            def get(self, request):
                return Response({"ok": True})

        view = Probe.as_view()
        factory = APIRequestFactory()
        results = {}
        for name in ("Superadmin", "Admin"):
            req = factory.get("/probe/")
            from rest_framework.test import force_authenticate
            force_authenticate(req, user=User.objects.get(username=name))
            results[name] = view(req).status_code
        self.assertEqual(results, {"Superadmin": 200, "Admin": 403})

    def test_module_permission_defaults_and_grants(self):
        from .permissions import user_can

        admin, sup = User.objects.get(username="Admin"), User.objects.get(username="Superadmin")
        self.assertTrue(user_can(admin, "projects", Action.ENTRY))
        self.assertFalse(user_can(admin, "projects", Action.VIEW))
        UserModulePermission.objects.create(user=admin, module="projects", can_view=True)
        self.assertTrue(user_can(admin, "projects", Action.VIEW))
        self.assertFalse(user_can(admin, "projects", Action.DELETE))
        self.assertTrue(user_can(sup, "projects", Action.EXPORT_PRINT))
        self.assertTrue(HasModulePermission("projects", Action.VIEW))


PERMS = "/api/config/admin-permissions/"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class AdminPermissionTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient(enforce_csrf_checks=True)

    def _csrf(self):
        self.client.get(CSRF)
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["nexus_csrftoken"].value}

    def login(self, name):
        self.client.post(LOGIN, {"username": name, "password": name}, format="json", **self._csrf())

    def add(self, name):
        return self.client.post(PERMS, {"name": name}, format="json", **self._csrf())

    def toggle(self, name_or_id, enabled):
        pk = name_or_id if isinstance(name_or_id, int) else AdminPermission.objects.get(name=name_or_id).pk
        return self.client.patch(f"{PERMS}{pk}/", {"enabled": enabled}, format="json", **self._csrf())

    def listed(self):
        return [(p["name"], p["enabled"], p["mapped"]) for p in self.client.get(PERMS).json()["permissions"]]

    def test_only_superadmin_can_read_or_change(self):
        self.assertEqual(self.client.get(PERMS).status_code, 401)
        self.login("Admin")
        before = list(AdminPermission.objects.values_list("name", "enabled"))
        self.assertEqual(self.client.get(PERMS).status_code, 403)
        self.assertEqual(self.add("Approval Access").status_code, 403)
        self.assertEqual(self.toggle("Edit Access", True).status_code, 403)
        self.assertEqual(list(AdminPermission.objects.values_list("name", "enabled")), before)

    def test_list_starts_with_entry_edit_delete(self):
        self.login("Superadmin")
        self.assertEqual(
            self.listed(),
            [("Entry Access", True, True), ("Edit Access", False, True), ("Delete Access", False, True)],
        )

    def test_custom_heading_is_appended_on_and_unmapped(self):
        self.login("Superadmin")
        r = self.add("  Approval   Access ")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.listed()[-1], ("Approval Access", True, False))
        self.assertIsNone(AdminPermission.objects.get(name="Approval Access").action)
        self.toggle("Approval Access", False)
        self.assertEqual(self.listed()[-1], ("Approval Access", False, False))

    def test_empty_and_duplicate_names_rejected(self):
        self.login("Superadmin")
        for bad in ("", "   ", "x" * 61):
            self.assertEqual(self.add(bad).status_code, 400, bad)
        self.assertEqual(self.client.post(PERMS, {}, format="json", **self._csrf()).status_code, 400)
        self.assertEqual(self.add("Approval Access").status_code, 201)
        for dup in ("Approval Access", "approval access", " APPROVAL   ACCESS ", "Entry Access", "delete access"):
            r = self.add(dup)
            self.assertEqual(r.status_code, 400, dup)
            self.assertIn("name", r.json()["errors"])
        self.assertEqual(AdminPermission.objects.count(), 4)

    def test_heading_matching_an_action_is_mapped_and_enforced(self):
        from .permissions import user_can

        admin = User.objects.get(username="Admin")
        self.login("Superadmin")
        self.assertFalse(user_can(admin, "gstr2tally", Action.VIEW))  # default deny
        self.add("view access")
        self.assertEqual(self.listed()[-1], ("view access", True, True))
        self.assertTrue(user_can(admin, "gstr2tally", Action.VIEW))
        self.assertTrue(user_can(admin, "prn-auto-print", Action.VIEW))
        self.toggle("view access", False)
        self.assertFalse(user_can(admin, "prn-auto-print", Action.VIEW))

    def test_unmapped_heading_grants_nothing(self):
        from .permissions import user_can

        admin = User.objects.get(username="Admin")
        self.login("Superadmin")
        self.add("Approval Access")
        for action in Action:
            expected = action == Action.ENTRY  # unchanged defaults
            self.assertEqual(user_can(admin, "gstr2tally", action), expected, action)

    def test_unknown_entry_is_404_and_bad_value_400(self):
        self.login("Superadmin")
        self.assertEqual(self.toggle(99999, True).status_code, 404)
        self.assertEqual(self.toggle("Edit Access", "maybe").status_code, 400)

    def test_toggles_are_enforced_for_admin_in_every_project(self):
        from .permissions import user_can

        admin = User.objects.get(username="Admin")
        self.assertTrue(user_can(admin, "gstr2tally", Action.ENTRY))
        self.assertFalse(user_can(admin, "gstr2tally", Action.EDIT))
        self.login("Superadmin")
        self.toggle("Edit Access", True)
        for project in ("gstr2tally", "prn-auto-print", "anything-else"):
            self.assertTrue(user_can(admin, project, Action.EDIT))
        self.toggle("Entry Access", False)
        for project in ("gstr2tally", "prn-auto-print"):
            self.assertFalse(user_can(admin, project, Action.ENTRY))
        self.toggle("Delete Access", True)
        self.assertTrue(user_can(admin, "prn-auto-print", Action.DELETE))

    def test_endpoint_guard_returns_403_when_switch_is_off(self):
        class Probe(APIView):
            permission_classes = [HasModulePermission("gstr2tally", Action.DELETE)]

            def delete(self, request):
                return Response({"ok": True})

        from rest_framework.test import force_authenticate

        admin = User.objects.get(username="Admin")

        def call():
            req = APIRequestFactory().delete("/probe/")
            force_authenticate(req, user=admin)
            return Probe.as_view()(req).status_code

        self.assertEqual(call(), 403)
        self.login("Superadmin")
        self.toggle("Delete Access", True)
        self.assertEqual(call(), 200)
        self.toggle("Delete Access", False)
        self.assertEqual(call(), 403)

    def test_me_exposes_effective_permissions(self):
        self.login("Superadmin")
        self.toggle("Edit Access", True)
        self.client.post(LOGOUT, **self._csrf())
        self.login("Admin")
        perms = self.client.get(ME).json()["user"]["permissions"]
        self.assertTrue(perms["entry"])
        self.assertTrue(perms["edit"])
        self.assertFalse(perms["delete"])


class DevBootstrapTests(TestCase):
    """The post-migrate development bootstrap: creates missing Superadmin / Admin accounts, never changes existing ones,
    and never runs outside DEBUG."""

    def run_hook(self):
        from apps.accounts.apps import bootstrap_dev_accounts

        bootstrap_dev_accounts(sender=None)

    @override_settings(DEBUG=False, BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin")
    def test_does_nothing_in_production_mode(self):
        self.run_hook()
        self.assertEqual(User.objects.count(), 0)

    @override_settings(DEBUG=True, BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin")
    def test_creates_missing_accounts_hashed_and_never_touches_existing_ones(self):
        self.run_hook()
        sa, admin = User.objects.get(username="Superadmin"), User.objects.get(username="Admin")
        self.assertEqual((sa.profile.role, admin.profile.role), ("SUPERADMIN", "ADMIN"))
        self.assertTrue(sa.check_password("superadmin") and admin.check_password("admin"))  # stored normalised (case-insensitive)
        self.assertTrue(sa.password.startswith("pbkdf2_") and "Superadmin" not in sa.password)
        # an administrator who changed the password keeps it; running again (e.g. every migrate) changes nothing
        sa.set_password("Changed#Pass9")
        sa.save()
        hash_before = User.objects.get(pk=sa.pk).password
        self.run_hook()
        self.run_hook()
        self.assertEqual(User.objects.get(pk=sa.pk).password, hash_before)
        self.assertEqual(User.objects.count(), 2)

    @override_settings(DEBUG=True, BOOTSTRAP_SUPERADMIN_PASSWORD=None, BOOTSTRAP_ADMIN_PASSWORD=None)
    def test_without_configured_passwords_nothing_is_created(self):
        self.run_hook()
        self.assertEqual(User.objects.count(), 0)
