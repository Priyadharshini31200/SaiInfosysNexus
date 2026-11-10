"""Add / Edit Customer form rules: optional Company Name, Address and License Type (default Trial); 9-character activation keys."""
import re
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from . import portal, services
from .models import IssuedActivationKey, ProjectCustomer
from .test_add_customer import PRN, AddCustomerMixin
from .test_portal import REST

NEW_KEY = re.compile(r"^[A-HJ-NP-Z2-9]{9}$")  # the generator's alphabet: no 0/O/1/I
SERIAL = re.compile(r"^7\d{8}$")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class OptionalFieldsTests(AddCustomerMixin, TestCase):
    def setUp(self):
        self.start()

    def test_saves_without_company_name_address_or_license_type(self):
        body = self.form()
        for field in ("company_name", "address", "gstin", "license_type", "trial_days"):
            body.pop(field)
        r = self.save(body)
        self.assertEqual(r.status_code, 201, r.content)
        row = ProjectCustomer.objects.get(pk=r.json()["id"])
        self.assertEqual((row.company_name, row.address, row.gstin, row.license_type), ("", "", "", ProjectCustomer.TRIAL))
        self.assertEqual(row.user_id, row.serial_number)

    def test_blank_company_name_address_and_gstin_are_accepted(self):
        r = self.save(company_name="", address="", gstin="")
        self.assertEqual(r.status_code, 201, r.content)

    def test_a_chosen_license_type_is_kept(self):
        r = self.save(license_type="ONE_YEAR")
        self.assertEqual(r.json()["license_type"], "ONE_YEAR")

    def test_other_mandatory_fields_are_still_required(self):
        for field in ("name", "contact_number", "email", "serial_number", "password", "activation_key"):
            body = self.form()
            body.pop(field)
            r = self.save(body)
            self.assertEqual(r.status_code, 400, field)
            self.assertIn(field, r.json()["errors"], field)

    def test_a_gstin_that_is_given_must_still_be_valid(self):
        r = self.save(gstin="NOT-A-GSTIN")
        self.assertEqual(r.status_code, 400)
        self.assertIn("gstin", r.json()["errors"])

    def test_edit_can_clear_optional_fields_and_leaves_omitted_ones_alone(self):
        pk = self.save().json()["id"]
        url = f"/api/projects/{PRN}/customers/{pk}/"
        self.assertEqual(self.api.patch(url, {"address": ""}, format="json").status_code, 200)
        row = ProjectCustomer.objects.get(pk=pk)
        self.assertEqual((row.address, row.company_name), ("", "Co"))  # company_name was not sent: unchanged
        self.assertEqual(self.api.patch(url, {"name": "Renamed"}, format="json").status_code, 200)
        self.assertEqual(ProjectCustomer.objects.get(pk=pk).company_name, "Co")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REST_FRAMEWORK=REST)
class ActivationKeyFormatTests(AddCustomerMixin, TestCase):
    def setUp(self):
        self.start()

    def test_generate_returns_exactly_nine_uppercase_alphanumerics_never_shaped_like_a_serial(self):
        keys = {self.key() for _ in range(40)}
        self.assertEqual(len(keys), 40)  # unique
        for key in keys:
            self.assertRegex(key, NEW_KEY)
            self.assertNotRegex(key, SERIAL)
            self.assertTrue(any(c.isalpha() for c in key) and any(c.isdigit() for c in key))

    def test_the_generator_skips_all_digit_and_all_letter_candidates(self):
        seq = iter("723456789" "ABCDEFGHJ" "A7K9P2X4M")  # shaped like a serial, letters only, then a valid key
        with patch("apps.registrations.services.secrets.choice", side_effect=lambda _alphabet: next(seq)):
            self.assertEqual(services._new_key(), "A7K9P2X4M")

    def test_a_key_already_held_by_a_customer_is_never_issued_again(self):
        body = self.form()
        self.assertEqual(self.save(body).status_code, 201)
        IssuedActivationKey.objects.all().delete()  # like a customer whose key predates the issued-key table
        candidates = iter([body["activation_key"], "B8C3D4E5F"])
        with patch("apps.registrations.services._new_key", side_effect=lambda: next(candidates)):
            self.assertEqual(services.generate_activation_key(PRN, self.staff), "B8C3D4E5F")

    def test_a_key_that_is_not_nine_characters_is_refused_for_a_new_customer(self):
        legacy = "ABCDEFGHJKLMNPQR"
        IssuedActivationKey.objects.create(project=PRN, key_hash=services._hash(legacy), key_suffix=legacy[-4:])
        r = self.save(activation_key="ABCD-EFGH-JKLM-NPQR")
        self.assertEqual(r.status_code, 400)
        self.assertIn("activation_key", r.json()["errors"])

    def test_the_credentials_email_shows_the_nine_character_key_and_it_is_stored_only_hashed(self):
        body = self.form()
        r = self.save(body)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn(f"Activation Key: {body['activation_key']}\n", mail.outbox[-1].body)
        row = ProjectCustomer.objects.get(pk=r.json()["id"])
        self.assertEqual(row.activation_key_hash, services._hash(body["activation_key"]))
        self.assertNotEqual(row.activation_key_hash, row.serial_number)

    def test_older_sixteen_character_keys_still_display_in_groups(self):
        self.assertEqual(portal.format_activation_key("abcdefghjklmnpqr"), "ABCD-EFGH-JKLM-NPQR")
        self.assertEqual(portal.format_activation_key("a7k9p2x4m"), "A7K9P2X4M")
