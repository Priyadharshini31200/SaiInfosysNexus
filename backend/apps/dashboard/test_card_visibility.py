from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ProjectDashboardVisibility as V, ProjectDashboardVisibilityAudit as Audit

User = get_user_model()
URL = "/api/dashboard/card-visibility/"


@override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True)
class CardVisibilityTests(TestCase):
    def setUp(self):
        call_command("seed_initial_users", verbosity=0)
        self.client = APIClient()
        self.admin = User.objects.get(username__iexact="Superadmin")
        self.client.force_authenticate(self.admin)

    def put(self, code, **body):
        return self.client.put(f"{URL}{code}/", body, format="json")

    def test_schedule_with_end_time_window(self):
        now = timezone.now()
        start, end = now + timedelta(hours=1), now + timedelta(hours=3)
        self.assertEqual(self.put("gstr2tally", mode="scheduled", visible_from_utc=start.isoformat(), visible_until_utc=start.isoformat(), version=0).status_code, 400)  # end must follow start
        r = self.put("gstr2tally", mode="scheduled", visible_from_utc=start.isoformat(), visible_until_utc=end.isoformat(), version=0)
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.data["visible_until_utc"])
        row = V.objects.get(project_code="gstr2tally")
        self.assertFalse(row.is_effective(now))                         # before the window
        self.assertTrue(row.is_effective(now + timedelta(hours=2)))     # inside
        self.assertFalse(row.is_effective(now + timedelta(hours=4)))    # after the end: hidden again
        r = self.put("gstr2tally", mode="show", version=1)
        self.assertIsNone(r.data["visible_until_utc"])                  # Show Now clears the window

    def test_default_visible_without_rows(self):
        r = self.client.get(f"{URL}gstr2tally/")
        self.assertEqual((r.data["mode"], r.data["effective"], r.data["version"]), ("show", True, 0))
        self.assertEqual(self.client.get(URL).data["settings"], {})

    def test_hide_show_and_audit(self):
        r = self.put("gstr2tally", mode="hidden", version=0)
        self.assertEqual((r.status_code, r.data["effective"]), (200, False))
        r = self.put("gstr2tally", mode="show", version=1)
        self.assertTrue(r.data["effective"])
        self.assertEqual(Audit.objects.filter(project_code="gstr2tally").count(), 2)

    def test_schedule_requires_future_and_flips_by_server_clock(self):
        past = (timezone.now() - timedelta(minutes=1)).isoformat()
        self.assertEqual(self.put("gstr2tally", mode="scheduled", visible_from_utc=past, version=0).status_code, 400)
        self.assertEqual(self.put("gstr2tally", mode="scheduled", visible_from_utc="garbage", version=0).status_code, 400)
        when = timezone.now() + timedelta(hours=1)
        r = self.put("gstr2tally", mode="scheduled", visible_from_utc=when.isoformat(), version=0)
        self.assertFalse(r.data["effective"])
        V.objects.update(visible_from_utc=timezone.now() - timedelta(seconds=1))  # the due time passes
        self.assertTrue(self.client.get(f"{URL}gstr2tally/").data["effective"])

    def test_show_hidden_clear_schedule(self):
        when = (timezone.now() + timedelta(hours=1)).isoformat()
        self.put("gstr2tally", mode="scheduled", visible_from_utc=when, version=0)
        self.put("gstr2tally", mode="hidden", visible_from_utc=when, version=1)
        self.assertIsNone(V.objects.get().visible_from_utc)

    def test_stale_version_conflict(self):
        self.put("gstr2tally", mode="hidden", version=0)
        self.assertEqual(self.put("gstr2tally", mode="show", version=0).status_code, 409)

    def test_unknown_project_and_invalid_mode(self):
        self.assertEqual(self.put("nope", mode="hidden", version=0).status_code, 404)
        self.assertEqual(self.put("gstr2tally", mode="x", version=0).status_code, 400)

    def test_admin_without_grant_denied_and_unauthenticated(self):
        admin = User.objects.get(username__iexact="Admin")
        c = APIClient(); c.force_authenticate(admin)
        self.assertEqual(c.put(f"{URL}gstr2tally/", {"mode": "hidden", "version": 0}, format="json").status_code, 403)
        self.assertFalse(c.get(f"{URL}gstr2tally/").data["can_edit"])
        self.assertEqual(APIClient().get(URL).status_code, 401)
        self.assertFalse(V.objects.exists())

    def test_admin_with_edit_grant_can_manage_prn_and_settings_persist(self):
        from apps.accounts.models import DashboardVisibilityGrant
        admin = User.objects.get(username__iexact="Admin")
        DashboardVisibilityGrant.objects.create(user=admin, project_code="prn-auto-print")
        c = APIClient(); c.force_authenticate(admin)
        r = c.put(f"{URL}prn-auto-print/", {"mode": "hidden", "version": 0}, format="json")
        self.assertEqual((r.status_code, r.data["effective"]), (200, False))
        # a fresh client (new login / refresh) sees the same stored state; the checkbox turns it back on
        fresh = APIClient(); fresh.force_authenticate(self.admin)
        self.assertFalse(fresh.get(f"{URL}prn-auto-print/").data["effective"])
        r = fresh.put(f"{URL}prn-auto-print/", {"mode": "show", "version": 1}, format="json")
        self.assertTrue(r.data["effective"])
        self.assertEqual(c.put(f"{URL}gstr2tally/", {"mode": "hidden", "version": 0}, format="json").status_code, 403)

    def test_config_list_includes_hidden_and_respects_grants(self):
        from apps.accounts.models import DashboardVisibilityGrant
        self.put("gstr2tally", mode="hidden", version=0)
        codes = {p["code"]: p for p in self.client.get(f"{URL}projects/").data["projects"]}
        self.assertFalse(codes["gstr2tally"]["effective"])
        self.assertIn("prn-auto-print", codes)
        admin = User.objects.get(username__iexact="Admin")
        c = APIClient(); c.force_authenticate(admin)
        self.assertEqual(c.get(f"{URL}projects/").data["projects"], [])
        DashboardVisibilityGrant.objects.create(user=admin, project_code="gstr2tally")
        self.assertEqual([p["code"] for p in c.get(f"{URL}projects/").data["projects"]], ["gstr2tally"])
