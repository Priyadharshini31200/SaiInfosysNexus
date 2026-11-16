"""GSTR2Tally device-transfer notifications: signed ingest, dedupe, permissions, read state, history. Notification-only."""
import hashlib
import hmac
import json
import re
import time
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from .models import TallyAdminPermission, TallyCustomer, TallyTransferEvent
from .tests import ADM, DEV_A, KEY, SIGNUP, Base

SECRET = "event-secret-for-tests"
URL = "/api/v1/tally-sl/gstr2tally/events/device-transfer/"


def payload(**over):
    return {"event_id": "evt-0000000001", "event_type": "device.transferred", "product": "gstr2tally",
            "tally_serial": "700123456", "old_device_name": "Old Laptop", "new_device_name": "New Laptop",
            "old_device_ref": "ref-old", "new_device_ref": "ref-new", "occurred_at": "2026-09-10T09:30:00Z",
            "initiated_by": "customer", **over}


@override_settings(TALLY_EVENT_SIGNING_SECRET=SECRET)
class TransferEventTests(Base):
    def send(self, body=None, ts=None, secret=SECRET, key=KEY, sign=True, raw=None):
        raw = raw if raw is not None else json.dumps(body if body is not None else payload()).encode()
        ts = str(int(time.time()) if ts is None else ts)
        sig = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest() if sign else "bad"
        h = {"HTTP_X_NEXUS_TIMESTAMP": ts, "HTTP_X_NEXUS_SIGNATURE": sig}
        if key:
            h["HTTP_X_PRODUCT_KEY"] = key
        return self.api.post(URL, raw, content_type="application/json", **h)

    def setUp(self):
        super().setUp()
        self.customer = self.registered()

    def test_valid_event_stored_once_and_retry_is_duplicate(self):
        r = self.send()
        self.assertEqual(r.status_code, 201, r.content)
        again = self.send()
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(TallyTransferEvent.objects.count(), 1)
        e = TallyTransferEvent.objects.get()
        self.assertEqual((e.customer_id, e.customer_name, e.old_device_name, e.new_device_name), (self.customer.pk, "Ravi Kumar", "Old Laptop", "New Laptop"))

    def test_rejects_unauthorised_and_malformed(self):
        self.assertEqual(self.send(key=None).status_code, 401)  # no product key
        self.assertEqual(self.send(sign=False).status_code, 401)  # bad signature
        self.assertEqual(self.send(secret="other").status_code, 401)
        self.assertEqual(self.send(ts=int(time.time()) - 3600).status_code, 401)  # stale (replay)
        self.assertEqual(self.send(body=payload(product="otherapp")).status_code, 403)
        self.assertEqual(self.send(body=payload(event_type="device.approved")).status_code, 400)
        self.assertEqual(self.send(body={"event_id": "x"}).status_code, 400)
        self.assertEqual(self.send(raw=b"{not json").status_code, 400)
        self.assertEqual(self.send(body=payload(tally_serial="999999999")).status_code, 404)  # unknown customer
        self.assertEqual(TallyTransferEvent.objects.count(), 0)

    @override_settings(TALLY_EVENT_SIGNING_SECRET="")
    def test_refused_when_secret_not_configured(self):
        self.assertEqual(self.send(secret="").status_code, 403)

    def test_bell_feed_read_state_and_permissions(self):
        self.send()
        feed = self.staff(self.super).get(f"{ADM}/notifications/").json()
        self.assertEqual(feed["unread_count"], 1)
        n = feed["results"][0]
        self.assertEqual(n["title"], "GSTR2Tally – Device Transferred")
        self.assertIn("transferred their", n["message"])
        self.assertIn("Old Laptop to New Laptop", n["message"])
        self.assertEqual(n["customer_id"], self.customer.pk)
        # an Admin without view on the product sees nothing and cannot mark it read
        self.assertEqual(self.staff(self.admin).get(f"{ADM}/notifications/").json()["unread_count"], 0)
        self.assertEqual(self.staff(self.admin).post(f"{ADM}/notifications/{n['id']}/read/").status_code, 404)
        TallyAdminPermission.objects.create(user=self.admin, product=self.product, can_view=True)
        self.assertEqual(self.staff(self.admin).get(f"{ADM}/notifications/").json()["unread_count"], 1)
        self.assertEqual(self.staff(self.super).post(f"{ADM}/notifications/{n['id']}/read/").status_code, 200)
        self.assertEqual(self.staff(self.super).get(f"{ADM}/notifications/").json()["unread_count"], 0)

    def test_detail_history_list_and_dashboard_count(self):
        self.send()
        s = self.staff(self.super)
        d = s.get(f"{ADM}/products/gstr2tally/customers/{self.customer.pk}/").json()
        self.assertEqual(d["transfer_history"][0]["old_device"], "Old Laptop")
        self.assertEqual(d["device_summary"]["previous"], "Old Laptop")
        lst = s.get(f"{ADM}/products/gstr2tally/customers/").json()["results"][0]
        self.assertIn("active_device", lst)
        self.assertIsNotNone(lst["last_transfer_at"])
        self.assertEqual(s.get(f"{ADM}/products/gstr2tally/transfers/").json()["total"], 1)
        self.assertEqual(s.get(f"{ADM}/dashboard/").json()["registrations"]["recent_device_transfers"], 0)  # event is older than 7 days
        self.send(body=payload(event_id="evt-0000000002", occurred_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        self.assertEqual(s.get(f"{ADM}/dashboard/").json()["registrations"]["recent_device_transfers"], 1)

    def test_no_edit_or_delete_endpoints_for_events(self):
        self.send()
        n = TallyTransferEvent.objects.get()
        s = self.staff(self.super)
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(s, method)(f"{ADM}/notifications/{n.pk}/read/").status_code, 405)

    def test_accepts_gstr2tally_outbox_shape(self):
        body = {"event_id": "6f1d2c3a-0000-4000-8000-000000000001", "event_type": "DEVICE_TRANSFER_COMPLETED", "customer_id": 7,
                "product": "gstr2tally", "tally_serial": "700123456", "old_device": {"id": 1, "name": "Old PC"},
                "new_device": {"id": 2, "name": "New PC"}, "transferred_at": "2026-09-10T09:30:00+00:00", "transfer_id": 5}
        self.assertEqual(self.send(body=body).status_code, 201)
        self.assertEqual(self.send(body=body).status_code, 200)
        e = TallyTransferEvent.objects.get()
        self.assertEqual((e.old_device_name, e.new_device_name, e.old_device_ref), ("Old PC", "New PC", "1"))


class ImportGstrCustomersTests(Base):
    def rows(self):
        lic = {"id": 1, "licensed_tally_serial": "735149529", "status": "ACTIVE", "purchase_date": None, "expiry_date": None,
               "activated_at": None, "username": "u1", "first_name": "Asha", "last_name": "R", "email": "asha@example.com", "business_name": "Asha Traders"}
        dev = {"license_id": 1, "id": 9, "device_fingerprint": "fp-x", "device_name": "OFFICE-PC", "app_version": "1.0", "status": "ACTIVE", "first_seen": None, "last_seen": None}
        return [lic], [dev]

    def test_dry_run_writes_nothing_and_apply_is_idempotent(self):
        from .management.commands.import_gstr_customers import import_rows
        from .models import TallyCustomer
        lics, devs = self.rows()
        self.assertEqual(import_rows(self.product, lics, devs)["created"], ["735149529"])
        self.assertEqual(TallyCustomer.objects.count(), 0)
        import_rows(self.product, lics, devs, apply=True)
        again = import_rows(self.product, lics, devs, apply=True)
        self.assertEqual(again["skipped_existing"], ["735149529"])
        self.assertEqual(TallyCustomer.objects.count(), 1)
        c = TallyCustomer.objects.get()
        self.assertEqual((c.name, c.company_name, c.license.status), ("Asha R", "Asha Traders", "ACTIVE"))
        row = self.staff(self.super).get("/api/v1/tally-sl-admin/products/gstr2tally/customers/").json()
        self.assertEqual((row["total"], row["results"][0]["active_device"]), (1, "OFFICE-PC"))
        self.assertEqual(self.staff(self.super).get("/api/v1/tally-sl-admin/dashboard/").json()["registrations"]["total"], 1)


class RegistrationToCustomerListTests(Base):
    """GSTR2Tally self-registration (v1 API) must appear at once in the Nexus customer list and dashboard, exactly once."""

    def test_registered_customer_is_listed_with_all_fields_and_counted_once(self):
        s = self.staff(self.super)
        list_url = "/api/v1/tally-sl-admin/products/gstr2tally/customers/"
        self.assertEqual(s.get(list_url).json()["total"], 0)
        self.assertEqual(self.signup().status_code, 201)
        self.assertEqual(self.call("register/", {**SIGNUP}).status_code, 409)  # duplicate serial is refused, not duplicated
        data = s.get(list_url).json()
        self.assertEqual(data["total"], 1)
        r = data["results"][0]
        self.assertEqual((r["tally_serial"], r["user_id"], r["email"], r["contact_number"], r["license_status"], r["license_type"], r["active_device"]),
                         ("700123456", "700123456", "ravi@example.com", "9876543210", "PENDING", "TRIAL", ""))
        dash = s.get("/api/v1/tally-sl-admin/dashboard/").json()
        self.assertEqual((dash["registrations"]["total"], dash["registrations"]["last_7_days"], dash["registrations"]["pending_license"]), (1, 1, 1))
        # device activation: the active device appears in the list
        self.enroll(TallyCustomer.objects.get(), self.activate(TallyCustomer.objects.get()))
        self.assertEqual(s.get(list_url).json()["results"][0]["active_device"], "Old Laptop")
