import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image
from rest_framework.test import APIClient

from .constants import Role
from .services import ensure_account

URL = "/api/company/profile/"


def png(size=(40, 40), fmt="PNG"):
    b = io.BytesIO()
    Image.new("RGB", size, (15, 42, 82)).save(b, fmt)
    return b.getvalue()


class CompanyProfileTests(TestCase):
    def client_for(self, role, name):
        user, _ = ensure_account(name, role, "pw")
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_defaults_and_superadmin_save_with_logo_persist(self):
        c = self.client_for(Role.SUPERADMIN, "co_boss")
        r = c.get(URL)
        self.assertEqual((r.data["name"], r.data["has_logo"], r.data["can_edit"]), ("", False, True))
        r = c.put(URL, {"name": " Acme  Traders ", "gstin": "33abcde1234f1z5", "logo": SimpleUploadedFile("l.png", png(), "image/png")}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.data["name"], r.data["gstin"], r.data["has_logo"]), ("Acme Traders", "33ABCDE1234F1Z5", True))
        again = c.get(URL).data
        self.assertTrue(again["has_logo"] and again["logo_version"])
        img = c.get("/api/company/logo/")
        self.assertEqual((img.status_code, img["Content-Type"]), (200, "image/png"))
        r = c.put(URL, {"name": "Acme Traders", "gstin": "", "remove_logo": "true"}, format="multipart")
        self.assertFalse(r.data["has_logo"])
        self.assertEqual(c.get("/api/company/logo/").status_code, 404)

    def test_validation(self):
        c = self.client_for(Role.SUPERADMIN, "co_boss2")
        self.assertEqual(c.put(URL, {"name": ""}, format="multipart").status_code, 400)
        self.assertEqual(c.put(URL, {"name": "Acme", "gstin": "BAD"}, format="multipart").status_code, 400)
        bad = c.put(URL, {"name": "Acme", "logo": SimpleUploadedFile("x.png", b"not an image", "image/png")}, format="multipart")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("logo", bad.data["errors"])
        big = c.put(URL, {"name": "Acme", "logo": SimpleUploadedFile("b.png", png((10, 10)) + b"0" * (2 * 1024 * 1024 + 1), "image/png")}, format="multipart")
        self.assertEqual(big.status_code, 400)
        gif = c.put(URL, {"name": "Acme", "logo": SimpleUploadedFile("g.gif", png(fmt="GIF"), "image/gif")}, format="multipart")
        self.assertEqual(gif.status_code, 400)

    def test_admin_can_view_but_not_edit_without_grant(self):
        sup = self.client_for(Role.SUPERADMIN, "co_boss3")
        sup.put(URL, {"name": "Acme"}, format="multipart")
        adm = self.client_for(Role.ADMIN, "co_adm")
        r = adm.get(URL)
        self.assertEqual((r.data["name"], r.data["can_edit"]), ("Acme", False))
        self.assertEqual(adm.put(URL, {"name": "Hacked"}, format="multipart").status_code, 403)
        self.assertEqual(sup.get(URL).data["name"], "Acme")
        self.assertEqual(APIClient().get(URL).status_code in (401, 403), True)
