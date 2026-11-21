from rest_framework.test import APIClient
from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.accounts.models import UserModulePermission, UserProfile
from .models import IntegrationApiConfig, IntegrationAudit, IntegrationProject
from .services import issue_credential


class ProjectManagementTests(TestCase):
    def setUp(self):
        self.project = IntegrationProject.objects.create(code="QA001", name="ORIGINAL", license_policy="CALENDAR")
        self.config = IntegrationApiConfig.objects.create(project=self.project, developer_email="first@example.invalid")
        self.credential, _ = issue_credential(self.project)
        self.url = "/api/integrations/admin/projects/QA001/"
        self.client = APIClient()

    def sign_in(self, role):
        user = get_user_model().objects.create_user(role.lower())
        UserProfile.objects.create(user=user, role=role)
        self.client.force_authenticate(user)
        return user

    def test_rename_preserves_identity_and_credentials(self):
        admin = self.sign_in("ADMIN")
        UserModulePermission.objects.create(user=admin, module="QA001:manage", can_edit=True)
        response = self.client.patch(self.url, {"name": "New Name", "developer_email": "new@example.invalid"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.config.refresh_from_db()
        self.assertEqual((self.project.code, self.project.name, self.config.developer_email), ("QA001", "NEW NAME", "new@example.invalid"))
        self.assertEqual(self.project.credentials.get(active=True).pk, self.credential.pk)
        self.assertNotEqual(self.client.patch(self.url, {"tally_serial_required": True}, format="json").status_code, 200)

    def test_archive_is_reversible_and_preserves_configuration_and_history(self):
        self.sign_in("SUPERADMIN")
        self.assertEqual(self.client.patch(self.url, {"lifecycle": "archive"}, format="json").status_code, 200)
        self.project.refresh_from_db()
        self.assertFalse(self.project.enabled)
        self.assertEqual(self.project.status, "ARCHIVED")
        self.assertEqual(self.client.post(self.url + "activate/", {}, format="json").status_code, 409)
        self.assertEqual(self.client.patch(self.url, {"lifecycle": "restore"}, format="json").status_code, 200)
        self.project.refresh_from_db()
        self.assertTrue(self.project.enabled)
        self.assertTrue(IntegrationApiConfig.objects.filter(pk=self.config.pk).exists())
        self.assertTrue(IntegrationAudit.objects.filter(project=self.project, action="project.archived").exists())

    def test_delete_is_only_for_empty_new_projects(self):
        """Approved design: permanent delete only for an EMPTY new (CALENDAR) project; data -> 409 (archive instead); legacy -> 403."""
        from .models import IntegrationCustomer
        admin = self.sign_in("ADMIN")  # no project grants: may not delete
        self.assertEqual(self.client.delete(self.url).status_code, 403)
        self.assertTrue(IntegrationProject.objects.filter(code="QA001").exists())
        self.sign_in("SUPERADMIN")
        legacy = IntegrationProject.objects.create(code="LEG001", name="LEGACY", license_policy="LEGACY")
        self.assertEqual(self.client.delete("/api/integrations/admin/projects/LEG001/").status_code, 403)
        self.assertTrue(IntegrationProject.objects.filter(pk=legacy.pk).exists())
        busy = IntegrationProject.objects.create(code="BUSY001", name="BUSY", license_policy="CALENDAR")
        customer = IntegrationCustomer.objects.create(project=busy, external_customer_id="fixture", name="Fixture",
                                                      email="fixture@example.invalid", serial_number="999000002", user_id="999000002")
        self.assertEqual(self.client.delete("/api/integrations/admin/projects/BUSY001/").status_code, 409)
        self.assertTrue(IntegrationCustomer.objects.filter(pk=customer.pk, project=busy).exists())
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertFalse(IntegrationProject.objects.filter(code="QA001").exists())

    def test_archive_waits_for_running_sync_and_retains_customer_licence_and_session(self):
        from django.utils import timezone
        from .models import IntegrationCustomer, IntegrationLicense, IntegrationSession, IntegrationSyncCheckpoint
        from .pull import pull_sync
        from .services import IntegrationError

        self.sign_in("SUPERADMIN")
        customer = IntegrationCustomer.objects.create(project=self.project, external_customer_id="fixture", name="Fixture",
                                                       email="fixture@example.invalid", serial_number="999000001", user_id="999000001")
        licence = IntegrationLicense.objects.create(customer=customer, license_type="TRIAL", status="ACTIVE")
        session = IntegrationSession.objects.create(license=licence, token_hash="x" * 64, device_id="fixture")
        checkpoint = IntegrationSyncCheckpoint.objects.create(project=self.project, status="RUNNING", last_started_at=timezone.now())
        self.assertEqual(self.client.patch(self.url, {"lifecycle": "archive"}, format="json").status_code, 409)
        checkpoint.status = "OK"
        checkpoint.save(update_fields=["status"])
        self.assertEqual(self.client.patch(self.url, {"lifecycle": "archive"}, format="json").status_code, 200)
        with self.assertRaisesRegex(IntegrationError, "disabled"):
            pull_sync(self.project)  # a worker holding an older project object must still reject it
        self.assertTrue(IntegrationSession.objects.filter(pk=session.pk, license__customer__project=self.project).exists())

    def test_api_grant_is_project_scoped_and_does_not_grant_lifecycle_or_credentials(self):
        admin = self.sign_in("ADMIN")
        UserModulePermission.objects.create(user=admin, module="QA001:api", can_edit=True)
        IntegrationProject.objects.create(code="QA002", name="OTHER", license_policy="CALENDAR")
        self.project.backend_url = "https://approved.example.invalid"
        self.project.save(update_fields=["backend_url"])
        self.config.credential_ref = "FIXTURE_REFERENCE"
        self.config.save(update_fields=["credential_ref"])

        self.assertEqual(self.client.get(self.url + "api-config/").status_code, 200)
        self.assertEqual(self.client.get(self.url.replace("QA001", "QA002") + "api-config/").status_code, 403)
        self.assertEqual(self.client.post(self.url + "rotate-credentials/", {}, format="json").status_code, 403)
        self.assertEqual(self.client.patch(self.url, {"lifecycle": "archive"}, format="json").status_code, 403)
        self.assertEqual(self.client.patch(self.url, {"backend_url": "http://approved.example.invalid"}, format="json").status_code, 403)
