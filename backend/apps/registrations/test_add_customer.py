"""Add Customer: unique User ID = Serial Number, atomic save, bounded retry on conflict, honest errors, idempotent retry."""
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from . import services
from .models import LicenseAuditLog, ProjectCustomer
from .test_portal import REST, make_customer

PRN, GSTR = "prn-auto-print", "gstr2tally"
RATES = {"portal": "1000/min", "device": "1000/min", "device_enroll": "1000/min", "device_public": "1000/min", "login": "1000/min"}


class AddCustomerMixin:
    def start(self):
        cache.clear()
        patcher = patch.dict(ScopedRateThrottle.THROTTLE_RATES, RATES)
        patcher.start()
        self.addCleanup(patcher.stop)
        with override_settings(BOOTSTRAP_SUPERADMIN_PASSWORD="Superadmin", BOOTSTRAP_ADMIN_PASSWORD="Admin", DEBUG=True):
            call_command("seed_initial_users", verbosity=0)
        self.staff = get_user_model().objects.get(username="Superadmin")
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.n = 0

    def key(self, client=None, project=PRN):
        return (client or self.api).post(f"/api/projects/{project}/activation-key/").json()["activation_key"]

    def form(self, client=None, project=PRN, **over):
        """Exactly what the Add Customer form submits."""
        self.n += 1
        c = client or self.api
        serial = over.pop("serial_number", None) or c.post(f"/api/projects/{project}/serial-number/").json()["serial_number"]
        body = {"name": f"Cust {self.n}", "company_name": "Co", "license_type": "TRIAL", "trial_days": 7, "contact_number": "9876543210",
                "email": f"c{self.n}-{threading.get_ident()}@example.com", "address": "Chennai", "gstin": "33AFHPM6103Q1Z8", "serial_number": serial,
                "user_id": serial, "password": "Temp#Pass1x", "activation_key": self.key(c, project)}
        body.update(over)
        return body

    def save(self, body=None, client=None, project=PRN, **over):
        c = client or self.api
        return c.post(f"/api/projects/{project}/customers/", body or self.form(c, project, **over), format="json")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class AddCustomerTests(AddCustomerMixin, TestCase):
    def setUp(self):
        self.start()

    def test_save_creates_the_customer_licence_key_and_user_id_equals_serial(self):
        r = self.save()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertRegex(body["serial_number"], r"^7\d{8}$")
        self.assertEqual(body["user_id"], body["serial_number"])
        row = ProjectCustomer.objects.get(pk=body["id"])
        self.assertEqual((row.project, row.license_type, row.status, row.user_id == row.serial_number), (PRN, "TRIAL", "ACTIVE", True))
        self.assertTrue(row.password_hash.startswith("pbkdf2_") and len(row.activation_key_hash) == 64 and row.activation_key_suffix)
        self.assertTrue(LicenseAuditLog.objects.filter(customer=row, action="REGISTERED").exists())
        listed = self.api.get(f"/api/projects/{PRN}/customers/").json()["results"]
        self.assertEqual([c["id"] for c in listed], [row.pk])  # the Customer List shows it straight away
        self.assertEqual(self.api.get(f"/api/projects/{GSTR}/customers/").json()["results"], [])  # and only under its own product

    def test_credentials_email_is_processed_once_after_the_save(self):
        r = self.save().json()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(r["serial_number"], mail.outbox[0].body)
        self.assertEqual(r["credentials_email_status"], "accepted")

    def test_a_taken_serial_is_replaced_never_reused_and_the_existing_customer_is_untouched(self):
        owner = make_customer(PRN, "755555555", "owner@example.com", activation_key_hash="o" * 64)
        before = (owner.pk, owner.password_hash, owner.activation_key_hash, owner.expires_at, owner.user_id, owner.serial_number)
        for project in (PRN, GSTR):  # the number is unique across every product
            r = self.save(project=project, serial_number="755555555")
            self.assertEqual(r.status_code, 201, r.content)
            self.assertNotEqual(r.json()["serial_number"], "755555555")
            self.assertEqual(r.json()["serial_reassigned_from"], "755555555")
            self.assertEqual(r.json()["user_id"], r.json()["serial_number"])
        owner.refresh_from_db()
        self.assertEqual((owner.pk, owner.password_hash, owner.activation_key_hash, owner.expires_at, owner.user_id, owner.serial_number), before)
        self.assertEqual(ProjectCustomer.objects.filter(serial_number="755555555").count(), 1)

    def test_a_taken_user_id_that_differs_from_its_serial_is_also_never_reused(self):
        make_customer(PRN, "766666666", "legacy@example.com", user_id="755000001", activation_key_hash="l" * 64)  # legacy: user id != serial
        r = self.save(serial_number="755000001")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotEqual(r.json()["serial_number"], "755000001")

    def test_many_consecutive_saves_all_get_distinct_numbers_and_appear_in_the_list(self):
        serials = [self.save().json()["serial_number"] for _ in range(12)]
        self.assertEqual(len(set(serials)), 12)
        listed = self.api.get(f"/api/projects/{PRN}/customers/").json()["results"]
        self.assertEqual(len(listed), 12)
        self.assertEqual(len(mail.outbox), 12)

    def test_a_conflict_from_sql_server_is_retried_with_a_new_number_within_a_limit(self):
        real, calls = services.register, []

        def flaky(project, data, user):
            calls.append(data["serial_number"])
            if len(calls) <= 2:
                raise IntegrityError("Violation of UNIQUE KEY constraint 'uniq_customer_serial_per_project'. Cannot insert duplicate key")
            return real(project, data, user)

        with patch.object(services, "register", flaky):
            r = self.save()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(calls)), 3)  # a fresh number every time
        self.assertEqual(ProjectCustomer.objects.count(), 1)

        calls.clear()
        with patch.object(services, "register", side_effect=IntegrityError("constraint 'uniq_customer_serial_per_project' duplicate key")) as always:
            r = self.save()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(always.call_count, services.MAX_SERIAL_ATTEMPTS)  # controlled limit, then a clear message
        self.assertIn("unique serial number", r.json()["message"])
        self.assertEqual(ProjectCustomer.objects.count(), 1)

    def test_other_database_refusals_are_reported_as_they_are_not_as_a_duplicate_serial(self):
        null_error = IntegrityError("Cannot insert the value NULL into column 'device_blocked', table 'x'; column does not allow nulls. INSERT fails.")
        with patch.object(services, "register", side_effect=null_error):
            r = self.save()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "missing_value"))
        self.assertIn("device_blocked", r.json()["message"])
        self.assertNotIn("already registered", r.json()["message"])
        self.assertIn("Restart", r.json()["message"])
        with patch.object(services, "register", side_effect=IntegrityError("some other constraint")):
            r = self.save()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "other"))
        self.assertNotIn("already registered", r.json()["message"])
        self.assertEqual(ProjectCustomer.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)  # nothing committed, nothing emailed

    def test_a_failed_save_is_atomic_nothing_half_written_and_the_key_stays_usable(self):
        body = self.form()
        with patch.object(services.audit, "record", side_effect=RuntimeError("boom")):
            failed = self.save(body)
        self.assertEqual((failed.status_code, failed.json()["code"]), (500, "server_error"))  # a readable JSON error, not an HTML page
        self.assertEqual(ProjectCustomer.objects.count(), 0)
        self.assertEqual(self.save(body).status_code, 201)  # the same key and form now save cleanly

    def test_sending_the_same_save_again_returns_the_same_customer_and_one_email(self):
        body = self.form()
        first = self.save(body)
        again = self.save(body)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.json()["already_created"])
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(ProjectCustomer.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        # a different person reusing that spent key is still refused
        other = self.save({**body, "name": "Someone Else", "email": "else@example.com"})
        self.assertEqual(other.status_code, 400)
        self.assertIn("activation_key", other.json()["errors"])

    def test_validation_errors_are_returned_as_they_are(self):
        r = self.save(self.form(contact_number="123"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("contact_number", r.json()["errors"])
        self.assertEqual(ProjectCustomer.objects.count(), 0)

    def test_the_form_serial_field_cannot_be_used_to_overwrite_a_customer(self):
        victim = make_customer(PRN, "788888888", "victim@example.com", activation_key_hash="v" * 64)
        r = self.save(serial_number="788888888", name="Attacker", email="attacker@example.com")
        self.assertEqual(r.status_code, 201)
        victim.refresh_from_db()
        self.assertEqual((victim.name, victim.email, victim.serial_number), ("Ravi", "victim@example.com", "788888888"))

    def test_a_server_that_does_not_know_the_newer_columns_can_still_insert(self):
        """A backend left running after an update inserts without the newer NOT NULL columns; the database now defaults them."""
        with connection.cursor() as c:
            c.execute(
                """insert into registrations_projectcustomer (project,name,company_name,license_type,user_id,password_hash,contact_number,email,
                must_change_password,address,gstin,serial_number,status,activation_key_hash,activation_key_suffix,activated_at,created_at,updated_at)
                values ('prn-auto-print','p','p','TRIAL','799000002','x','9','p@e.com',1,'a','','799000002','ACTIVE','"""
                + "z" * 64
                + """','ABCD',sysdatetimeoffset(),sysdatetimeoffset(),sysdatetimeoffset())"""
            )
        row = ProjectCustomer.objects.get(user_id="799000002")
        self.assertEqual((row.failed_logins, row.device_blocked, row.credentials_email_status), (0, False, ""))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class ConcurrentAddCustomerTests(AddCustomerMixin, TransactionTestCase):
    """Real simultaneous saves on SQL Server (separate connections)."""

    def setUp(self):
        self.start()

    def run_threads(self, count, work):
        results, errors, barrier = [None] * count, [], threading.Barrier(count)

        def runner(i):
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(username="Superadmin"))
                results[i] = work(i, client, barrier)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(repr(exc))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
        [t.start() for t in threads]
        [t.join(120) for t in threads]
        self.assertEqual(errors, [])
        return results

    def test_simultaneous_saves_racing_for_the_same_number_each_get_their_own_customer(self):
        real_check, waits = services.serial_in_use, threading.local()

        def racing_check(serial):
            free = not real_check(serial)
            if not getattr(waits, "done", False) and serial == "755123456":
                waits.done = True
                self.gate.wait(30)  # every thread has now seen the number as free
            return not free

        self.gate = threading.Barrier(4)
        bodies = [self.form(serial_number="755123456", name=f"Racer {i}", email=f"racer{i}@example.com") for i in range(4)]
        with patch.object(services, "serial_in_use", racing_check):
            results = self.run_threads(4, lambda i, client, barrier: self.save(bodies[i], client))
        self.assertEqual([r.status_code for r in results], [201] * 4, [r.content for r in results])
        serials = [r.json()["serial_number"] for r in results]
        self.assertEqual(len(set(serials)), 4)  # SQL Server's unique constraint made three of them take new numbers
        self.assertEqual(serials.count("755123456"), 1)
        self.assertEqual(ProjectCustomer.objects.count(), 4)
        self.assertEqual(len({c.activation_key_hash for c in ProjectCustomer.objects.all()}), 4)
        self.assertEqual(len(mail.outbox), 4)

    def test_simultaneous_saves_with_generated_numbers_never_duplicate(self):
        bodies = [self.form(name=f"Parallel {i}", email=f"par{i}@example.com") for i in range(6)]
        results = self.run_threads(6, lambda i, client, barrier: (barrier.wait(30), self.save(bodies[i], client))[1])
        self.assertTrue(all(r.status_code == 201 for r in results), [r.content for r in results])
        serials = list(ProjectCustomer.objects.values_list("serial_number", flat=True))
        self.assertEqual((len(serials), len(set(serials))), (6, 6))
