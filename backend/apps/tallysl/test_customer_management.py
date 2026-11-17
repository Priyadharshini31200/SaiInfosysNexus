"""GSTR2Tally customer list filters/exports and the three-dot credential actions, with per-product permissions."""
import re

from django.core import mail

from .models import TallyAdminPermission, TallyCustomer
from .tests import ADM, DEV_A, SIGNUP, Base

P = f"{ADM}/products/gstr2tally/customers/"


class CustomerManagementTests(Base):
    def pw(self):
        body = next(m for m in reversed(mail.outbox) if "Welcome" in m.subject).body
        return re.search(r"Temporary Password: (\S+)", body).group(1)

    def test_list_filters_and_columns(self):
        self.registered("700000001", "a@example.com")
        c2 = self.registered("700000002", "b@example.com")
        self.activate(c2, "LIFETIME")
        s = self.staff(self.super)
        rows = s.get(P).json()["results"]
        self.assertEqual(len(rows), 2)
        for k in ("name", "company_name", "contact_number", "email", "tally_serial", "license_plan", "state", "expires_at", "password_status", "credentials_email_status"):
            self.assertIn(k, rows[0])
        self.assertEqual([r["tally_serial"] for r in s.get(P + "?license_plan=LIFETIME").json()["results"]], ["700000002"])
        self.assertEqual([r["tally_serial"] for r in s.get(P + "?state=pending").json()["results"]], ["700000001"])
        self.assertEqual(s.get(P + "?state=active&license_plan=TRIAL").json()["total"], 0)
        self.assertEqual(s.get(P + "?search=700000002").json()["total"], 1)

    def test_exports_follow_filters_and_permission(self):
        self.registered("700000001", "a@example.com")
        c2 = self.registered("700000002", "b@example.com")
        self.activate(c2, "LIFETIME")
        s = self.staff(self.super)
        t = s.post(P + "export/", {"fmt": "print", "filters": {"license_plan": "LIFETIME"}}, format="json").json()
        self.assertEqual(len(t["rows"] if "rows" in t else t["body"]), 1)
        x = s.post(P + "export/", {"fmt": "xlsx", "filters": {}}, format="json")
        self.assertEqual((x.status_code, x["Content-Type"].split(";")[0]), (200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        self.assertEqual(s.post(P + "export/", {"fmt": "pdf", "filters": {}}, format="json").status_code, 200)
        self.assertEqual(self.staff(self.admin).post(P + "export/", {"fmt": "xlsx"}, format="json").status_code, 403)  # no View grant

    def test_resend_and_reset_credentials_permissions_and_effects(self):
        c = self.registered()
        old = self.pw()
        url = f"{P}{c.pk}/credentials/"
        adm = self.staff(self.admin)
        self.assertEqual(adm.post(url + "resend/").status_code, 403)  # no grants
        TallyAdminPermission.objects.create(user=self.admin, product=self.product, can_view=True, can_entry=True)
        mail.outbox.clear()
        r = adm.post(url + "resend/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(next(m for m in reversed(mail.outbox) if "Welcome" in m.subject).to, ["ravi@example.com"])
        new = self.pw()
        self.assertNotEqual(new, old)
        self.assertNotIn("temporary_password", r.json())  # only returned when the email failed
        self.assertEqual(self.call("auth/login/", {"user_id": "700123456", "password": old, "device": DEV_A}).status_code, 401)
        self.assertEqual(self.call("auth/login/", {"user_id": "700123456", "password": new, "device": DEV_A}).status_code, 200)
        self.assertEqual(adm.post(url + "reset-password/").status_code, 403)  # Super Admin only
        sr = self.staff(self.super).post(url + "reset-password/")
        self.assertEqual(sr.status_code, 200)
        self.assertTrue(TallyCustomer.objects.get(pk=c.pk).password_hash)
        self.assertEqual(self.staff(self.super).post(url + "bogus/").status_code, 404)

    def test_resend_refused_after_customer_changed_their_own_password(self):
        c = self.registered()
        pw = self.pw()
        tok = self.call("auth/login/", {"user_id": "700123456", "password": pw, "device": DEV_A}).data["token"]
        self.call("auth/change-password/", {"current_password": pw, "new_password": "Mine#Pass1"}, token=tok)
        r = self.staff(self.super).post(f"{P}{c.pk}/credentials/resend/")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "password_already_changed"))
        row = self.staff(self.super).get(f"{P}{c.pk}/").json()["customer"]
        self.assertEqual(row["password_status"], "Changed by customer")
        self.assertTrue(row["password_changed_at"])

    def test_reissue_key_does_not_email_and_delete_keeps_audit(self):
        c = self.registered()
        self.activate(c, "LICENSED")
        mail.outbox.clear()
        r = self.staff(self.super).post(f"{P}{c.pk}/license/reissue-key/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mail.outbox, [])  # the key is only emailed when the customer asks via Activate User
        d = self.staff(self.super).delete(f"{P}{c.pk}/")
        self.assertEqual(d.status_code, 204)
        from .models import TallyAuditLog
        self.assertTrue(TallyAuditLog.objects.filter(action="CUSTOMER_DELETED").exists())
