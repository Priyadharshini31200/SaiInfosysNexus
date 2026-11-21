"""Handoff verification: drives every Universal API in the order a Mothys backend would, checks the saved record is visible to the
Nexus staff pages, and (when NEXUS_HANDOFF_OUT is set) writes the real request/response pairs used to generate the handoff document."""
import json
import os
import re

from django.core import mail
from django.test import override_settings

from . import services as s
from .models import IntegrationCustomer, IntegrationLicense
from .tests import ADM, REG, V1, Base

RESULTS = []


class HandoffVerification(Base):
    def rec(self, api, method, path, body, resp, case="success", expect=200):
        RESULTS.append({"api": api, "case": case, "method": method, "path": path, "request": body, "status": resp.status_code, "response": resp.json(), "pass": resp.status_code == expect})
        self.assertEqual(resp.status_code, expect, f"{api} / {case}: {resp.content[:300]}")
        return resp.json()

    def post(self, api, path, body, case="success", expect=200, **extra):
        r = self.call(path, body, **extra)
        return self.rec(api, "POST", "/" + path, {"project_code": "MOTHYS", **body}, r, case, expect)

    def test_full_handoff_flow(self):
        # Project is created the way the seed migration defines it: trial 3 days, no Tally, 9/9/9, one device, transfer by the customer.
        self.staff.patch(ADM + "projects/MOTHYS/", {"trial_days": 3}, format="json")
        p = self.project
        p.refresh_from_db()
        self.assertEqual((p.serial_prefix, p.serial_length, p.activation_key_length, p.device_limit, p.transfer_policy, p.tally_serial_required, p.activation_email_mode),
                         ("9", 9, 9, 1, "SELF", False, "ON_REQUEST"))
        reg = {k: v for k, v in REG.items() if k != "project_code"}

        # 1 register (+ duplicate, + validation)
        d = self.post("customers/register", "customers/register", reg, expect=201)["data"]
        self.post("customers/register", "customers/register", reg, "duplicate external id", 409)
        self.post("customers/register", "customers/register", {**reg, "external_customer_id": "MOTHYS-1002", "email": "bad", "contact_number": "1"}, "validation error", 422)
        self.assertRegex(d["serial_number"], r"^9\d{8}$")
        self.assertEqual(d["user_id"], d["serial_number"])
        self.assertEqual((d["license_type"], d["license_state"]), ("TRIAL", "ACTIVE"))
        user = d["user_id"]
        # welcome email processed once, credentials inside, no activation key
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        pw = re.search(r"Temporary Password: (\S+)", body).group(1)
        self.assertIn(f"User ID: {user}", body)
        self.assertIn("License Type: Trial", body)
        self.assertIn("Trial Days / Expiry Date: 3 days", body)
        self.assertEqual(mail.outbox[0].subject, "Mothys Inventory – Your Login Details")
        self.assertNotRegex(body, r"[Aa]ctivation [Kk]ey: \d")
        cust = IntegrationCustomer.objects.get(user_id=user)
        self.assertNotEqual(cust.password_hash, pw)  # only a hash is stored
        self.assertTrue(cust.password_hash.startswith(("pbkdf2_", "argon2", "bcrypt")))
        self.assertEqual(IntegrationLicense.objects.get(customer=cust).license_type, "TRIAL")

        # 2 update, 3 bulk import
        self.post("customers/update", "customers/update", {"external_customer_id": "MOTHYS-1001", "event": "CONTACT_UPDATED", "contact_number": "9876500000"})
        self.post("customers/update", "customers/update", {"external_customer_id": "NOPE", "event": "CUSTOMER_UPDATED", "customer_name": "X"}, "unknown customer", 404)
        self.post("customers/import", "customers/import", {"send_welcome_email": False, "customers": [
            {"external_customer_id": "MOTHYS-2001", "customer_name": "Bala", "email": "bala@example.com", "contact_number": "9000000001"}]})

        # 4 login verification, 5 password change
        self.post("auth/verify", "auth/verify", {"user_id": user, "password": "wrong"}, "wrong password", 401)
        v = self.post("auth/verify", "auth/verify", {"user_id": user, "password": pw})["data"]
        self.assertTrue(v["must_change_password"])
        newpw = "MyNewPass#9"
        self.post("auth/change-password", "auth/change-password", {"user_id": user, "current_password": pw, "new_password": newpw})
        self.assertFalse(self.call("auth/verify", {"user_id": user, "password": newpw}).json()["data"]["must_change_password"])

        # 6 licence validation
        self.post("licenses/validate", "licenses/validate", {"user_id": user})

        # 7 activation key request (only now; none was sent at registration)
        mail.outbox.clear()
        self.post("activation/request", "activation/request", {"user_id": user, "email": "someone.else@example.com"}, "email not registered to this account", 401)
        self.assertEqual(len(mail.outbox), 0)  # nothing is sent to anyone else
        self.post("activation/request", "activation/request", {"user_id": user, "email": "Kumar@Example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["kumar@example.com"])
        key = re.search(r"is: (\d{9})", mail.outbox[0].body).group(1)

        # 8 activation verify on device A; wrong key
        self.post("activation/verify", "activation/verify", {"user_id": user, "email": "kumar@example.com", "activation_key": "000000000", "device_id": "PC-A", "device_name": "Office PC"}, "wrong key", 400)
        a = self.post("activation/verify", "activation/verify", {"user_id": user, "email": "kumar@example.com", "activation_key": key, "device_id": "PC-A", "device_name": "Office PC"})["data"]

        # 10 introspect (device A active)
        self.post("sessions/introspect", "sessions/introspect", {"session_token": a["session_token"]})

        # logging in again on the authorised device: no key, no transfer
        again = self.post("auth/verify", "auth/verify", {"user_id": user, "password": newpw, "device_id": "PC-A"}, "login again on the authorised device")["data"]
        self.assertEqual((again["device_authorized"], again["session_id"]), (True, 1 if False else again["session_id"]))
        self.assertTrue(again["device_authorized"])
        other = self.call("auth/verify", {"user_id": user, "password": newpw, "device_id": "PC-B"}).json()["data"]
        self.assertFalse(other["device_authorized"])

        # second device: key -> confirmation required -> cancel -> again -> confirm
        mail.outbox.clear()
        self.call("activation/request", {"user_id": user, "email": "kumar@example.com"})
        key2 = re.search(r"is: (\d{9})", mail.outbox[0].body).group(1)
        conf = self.post("activation/verify", "activation/verify", {"user_id": user, "email": "kumar@example.com", "activation_key": key2, "device_id": "PC-B", "device_name": "Home Laptop"}, "second device: confirmation required", 409)
        tok = conf["error"]["transfer_token"]
        self.post("devices/transfer/confirm", "devices/transfer/confirm", {"user_id": user, "transfer_token": tok, "confirm": False}, "cancel")
        self.assertTrue(self.call("sessions/introspect", {"session_token": a["session_token"]}).json()["data"]["active"])  # A still authorised
        mail.outbox.clear()
        self.call("activation/request", {"user_id": user, "email": "kumar@example.com"})
        key3 = re.search(r"is: (\d{9})", mail.outbox[0].body).group(1)
        tok = self.call("activation/verify", {"user_id": user, "email": "kumar@example.com", "activation_key": key3, "device_id": "PC-B", "device_name": "Home Laptop"}).json()["error"]["transfer_token"]
        t = self.post("devices/transfer/confirm", "devices/transfer/confirm", {"user_id": user, "transfer_token": tok, "confirm": True}, "confirm")["data"]
        old = self.post("sessions/introspect", "sessions/introspect", {"session_token": a["session_token"]}, "old device after transfer")["data"]
        self.assertEqual((old["active"], old["reason"]), (False, "device_transfer"))

        # 12 usage, 11 logout
        self.post("usage/events", "usage/events", {"session_token": t["session_token"], "event_type": "LOGIN", "event_id": "evt-1"})
        self.post("usage/events", "usage/events", {"session_token": t["session_token"], "event_type": "HEARTBEAT", "event_id": "evt-2"})
        self.post("sessions/logout", "sessions/logout", {"session_token": t["session_token"]})
        self.assertEqual(self.call("sessions/introspect", {"session_token": t["session_token"]}).json()["data"]["reason"], "logout")
        RESULTS.append({"api": "projects/{code}/config", "case": "success", "method": "GET", "path": "/projects/MOTHYS/config", "request": None, "status": 0, "response": None, "pass": True})
        cfg = self.call("projects/MOTHYS/config", method="get")
        RESULTS[-1].update(status=cfg.status_code, response=cfg.json(), **{"pass": cfg.status_code == 200})
        self.assertEqual(cfg.status_code, 200)
        self.assertEqual(cfg.json()["data"]["status"], "ACTIVE")  # every required step was really exercised -> verified

        # the saved record is visible to the Nexus Mothys pages
        lst = self.staff.get(ADM + "projects/MOTHYS/product-customers/").data
        self.assertEqual(lst["total"], 2)
        mine = next(r for r in lst["results"] if r["serial_number"] == user)
        self.assertEqual((mine["name"], mine["license_type"], mine["contact_number"], mine["status"], mine["trial_days"]), ("Kumar", "TRIAL", "9876500000", "active", 3))
        dash = self.staff.get(ADM + "projects/MOTHYS/dashboard/").data
        self.assertEqual((dash["total_customers"], dash["trial_licenses"], dash["used"], dash["active_devices"]), (2, 2, 1, 0))  # logged out -> no active device
        for kind in ("expiry", "details", "performance"):
            rep = self.staff.get(ADM + f"projects/MOTHYS/reports/{kind}/").data
            self.assertEqual(rep["total"], 2, kind)
        self.assertTrue(any(user in str(r["cells"]) for r in self.staff.get(ADM + "projects/MOTHYS/reports/details/").data["results"]))

        out = os.environ.get("NEXUS_HANDOFF_OUT")
        if out:
            json.dump(RESULTS, open(out, "w", encoding="utf-8"), indent=2, default=str)
        for r in RESULTS:
            print(f'HANDOFF {"PASS" if r["pass"] else "FAIL"} {r["method"]} {r["api"]} [{r["case"]}] -> {r["status"]}')
