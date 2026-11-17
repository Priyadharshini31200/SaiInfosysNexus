from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import UserProfile, UserModulePermission, AdminPermission
from .permissions import user_can
from .serializers import ChangePasswordSerializer


class ScopedAccessTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('scoped-admin', password='old')
        UserProfile.objects.create(user=self.admin, role='ADMIN')
        self.super = get_user_model().objects.create_user('scoped-super', password='old')
        UserProfile.objects.create(user=self.super, role='SUPERADMIN')
        self.client = APIClient()

    def test_global_switch_cannot_grant_an_unassigned_project(self):
        AdminPermission.objects.update_or_create(action='view', defaults={'name': 'View Access', 'enabled': True})
        self.assertFalse(user_can(self.admin, 'prn-auto-print', 'view'))

    def test_project_grants_are_isolated_and_view_does_not_allow_edit(self):
        UserModulePermission.objects.create(user=self.admin, module='gstr2tally', can_view=True, can_entry=False)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/projects/gstr2tally/customers/').status_code, 200)
        self.assertEqual(self.client.get('/api/projects/prn-auto-print/customers/').status_code, 403)
        self.assertEqual(self.client.patch('/api/projects/gstr2tally/customers/1/', {}, format='json').status_code, 403)

    def test_only_superadmin_can_assign_and_cannot_target_superadmin(self):
        url = '/api/config/project-permissions/'
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.put(url, {}, format='json').status_code, 403)
        self.client.force_authenticate(self.super)
        response = self.client.put(url, {'user_id': self.super.pk, 'grants': []}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.put(url, {'user_id': self.admin.pk, 'grants': [
            {'module': 'gstr2tally', 'actions': ['view']}]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(user_can(self.admin, 'gstr2tally', 'view'))
        self.assertFalse(user_can(self.admin, 'gstr2tally', 'edit'))

    def test_recent_superadmin_can_change_without_current_password(self):
        from types import SimpleNamespace
        request = SimpleNamespace(user=self.super, session={'authenticated_at': timezone.now().timestamp()})
        serializer = ChangePasswordSerializer(data={'new_password': '7', 'confirm_password': '7'}, context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        request.session = {}
        serializer = ChangePasswordSerializer(data={'new_password': '7', 'confirm_password': '7'}, context={'request': request})
        self.assertFalse(serializer.is_valid())

    def test_password_change_sends_notice_without_credentials(self):
        from apps.registrations import portal
        from types import SimpleNamespace
        customer = SimpleNamespace(pk=1, project='prn-auto-print', name='Customer', email='c@example.com')
        with patch.object(portal, '_send_status', return_value='accepted') as send:
            portal.send_password_changed(customer)
        args, kwargs = send.call_args
        self.assertIn('Password Changed', args[0])
        self.assertNotIn('Temporary', args[1])
        self.assertEqual(kwargs['kind'], 'password_changed')


    def make_customer(self, project="prn-auto-print", serial="700000001", **extra):
        from apps.registrations.models import ProjectCustomer
        from apps.registrations.services import hash_password
        from datetime import timedelta
        data = dict(project=project, name="Customer", company_name="Company", user_id=serial,
                    serial_number=serial, password_hash=hash_password("old"), email=f"{serial}@example.com",
                    license_type="ONE_YEAR", contact_number="9876543210", address="Address",
                    activated_at=timezone.now(), expires_at=timezone.now()+timedelta(days=2),
                    activation_key_hash=serial*5, activation_key_suffix="1234", must_change_password=True)
        data.update(extra)
        return ProjectCustomer.objects.create(**data)

    def test_edit_password_persists_notifies_and_cannot_resend_provisioning(self):
        from apps.registrations import services, portal
        customer = self.make_customer()
        with patch.object(portal, "send_password_changed", return_value=True) as notice:
            with self.captureOnCommitCallbacks(execute=True):
                services.edit(customer, {"password": "7"}, self.super)
        customer.refresh_from_db()
        self.assertTrue(services.verify_password(customer, "7"))
        self.assertFalse(services.verify_password(customer, "old"))
        self.assertIsNotNone(customer.password_changed_at)
        self.assertFalse(customer.must_change_password)
        notice.assert_called_once()
        with self.assertRaises(services.RuleError):
            services.reissue_unsent_credentials(customer, self.super, resend=True)

    def test_customer_change_revokes_token_and_notice_failure_preserves_password(self):
        from apps.registrations import portal, services
        customer = self.make_customer()
        token = portal.session_data(customer)["token"]
        with patch.object(portal, "_send_status", return_value="failed"):
            portal.change_password(customer, "old", "7")
        customer.refresh_from_db()
        self.assertTrue(services.verify_password(customer, "7"))
        with self.assertRaises(portal.PortalError):
            portal.customer_from_token(token, customer.project)

    def test_reports_are_scoped_even_when_calling_api_directly(self):
        customer = self.make_customer(project="gstr2tally")
        self.make_customer(serial="700000002")
        UserModulePermission.objects.create(user=self.admin, module="gstr2tally:expiry", can_reports=True)
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/reports/license-expiry/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["results"]], [customer.pk])
        self.assertEqual(self.client.get('/api/reports/usage/').status_code, 403)

    def test_dashboard_returns_only_granted_project(self):
        UserModulePermission.objects.create(user=self.admin, module="gstr2tally:dashboard", can_view=True)
        self.client.force_authenticate(self.admin)
        with patch("apps.dashboard.usage.summary", return_value={"used": 1, "not_used": 2, "expired": 0, "expiring": 0, "total": 3}):
            response = self.client.get('/api/dashboard/project-usage/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["code"] for row in response.data["projects"]], ["gstr2tally"])
        self.assertEqual(self.client.get('/api/dashboard/project-usage/prn-auto-print/used/').status_code, 403)

    def test_expiry_counts_and_drilldowns_share_effective_expiry_rules(self):
        from apps.dashboard import usage
        from datetime import timedelta
        now = timezone.now()
        self.make_customer(first_activated_at=now-timedelta(days=7), expires_at=now-timedelta(seconds=1))
        self.make_customer(serial="700000002", license_type="LICENSED", expires_at=now-timedelta(days=1))
        self.make_customer(serial="700000003", license_type="TRIAL", first_activated_at=None, expires_at=now-timedelta(days=1))
        self.assertEqual(usage._expired("prn-auto-print", now).count(), 1)
        self.assertEqual(usage.listing("prn-auto-print", "expired", now=now)["total"], 1)

    def test_admin_cannot_change_customer_password_with_edit_permission(self):
        customer = self.make_customer()
        UserModulePermission.objects.create(user=self.admin, module=customer.project, can_edit=True)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.patch(f'/api/projects/{customer.project}/customers/{customer.pk}/', {"password": "7"}, format="json").status_code, 403)


    def test_approval_only_grant_can_load_requests_and_cannot_create_unrelated_customer(self):
        UserModulePermission.objects.create(user=self.admin, module="prn-auto-print", can_approve=True)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/projects/prn-auto-print/registration-requests/').status_code, 200)
        self.assertEqual(self.client.post('/api/projects/prn-auto-print/customers/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/projects/prn-auto-print/customers/', {"request_id": 1}, format='json').status_code, 400)

    def test_old_provisioning_email_is_refused_after_password_change(self):
        from apps.registrations import services, portal
        customer = self.make_customer()
        stale_customer = type(customer).objects.get(pk=customer.pk)
        with patch.object(portal, "send_password_changed", return_value=True):
            with self.captureOnCommitCallbacks(execute=True):
                services.edit(customer, {"password": "7"}, self.super)
        with patch.object(portal, "_send_status") as send:
            self.assertFalse(portal.send_credentials(stale_customer, "old", "unused"))
        send.assert_not_called()

    def test_device_management_requires_its_own_grant(self):
        customer = self.make_customer()
        self.client.force_authenticate(self.admin)
        url = f'/api/projects/{customer.project}/customers/{customer.pk}/device/'
        self.assertEqual(self.client.get(url).status_code, 403)
        UserModulePermission.objects.create(user=self.admin, module=customer.project, can_device_transfer=True)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_console_mail_does_not_print_passwords(self):
        from django.test import override_settings
        from apps.registrations import mailer
        from io import StringIO
        from contextlib import redirect_stdout
        output = StringIO()
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"), redirect_stdout(output):
            result = mailer.deliver("Credentials", "secret-password", "c@example.com", kind="credentials")
        self.assertEqual(result, "not_delivered")
        self.assertNotIn("secret-password", output.getvalue())

    def test_admin_reset_and_recent_superadmin_change_persist_on_next_login(self):
        from .passwords import check_user_password
        self.client.force_authenticate(self.super)
        response = self.client.post(f'/api/config/admins/{self.admin.pk}/reset-password/',
            {"new_password": "7", "confirm_password": "7"}, format='json')
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(check_user_password(self.admin, "7"))
        self.assertFalse(check_user_password(self.admin, "old"))
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post('/api/auth/login/', {"username": self.super.username, "password": "old"}, format='json').status_code, 200)
        response = self.client.post('/api/auth/change-password/', {"new_password": "7", "confirm_password": "7"}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login/', {"username": self.super.username, "password": "7"}, format='json').status_code, 200)


    def test_registration_rechecks_request_status_inside_transaction(self):
        from apps.registrations import services
        from apps.registrations.models import RegistrationRequest, ProjectCustomer
        req = RegistrationRequest.objects.create(project="prn-auto-print", name="Customer", email="c@example.com",
            contact_number="9876543210", status=RegistrationRequest.PROCESSED)
        with self.assertRaisesMessage(services.RuleError, "already been processed"):
            services.register(req.project, {"request_id": req.pk, "license_type": "TRIAL", "activation_key": "unused"}, self.admin)
        self.assertFalse(ProjectCustomer.objects.exists())
