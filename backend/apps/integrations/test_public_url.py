"""Public Nexus frontend URL composition for a shared-domain deployment (SPA lives under /nexus/, API under /api/integrations/v1/)."""
from django.test import SimpleTestCase, override_settings
from django.urls import resolve

from . import services as s


class PublicFrontendUrl(SimpleTestCase):
    @override_settings(NEXUS_PUBLIC_FRONTEND_URL="https://saiinfosysmdu.in/nexus/", NEXUS_FRONTEND_BASE_PATH="/nexus")
    def test_configured_url_keeps_nexus_path_without_duplicates(self):
        self.assertEqual(s.public_frontend_url(), "https://saiinfosysmdu.in/nexus")
        self.assertEqual(s.developer_credentials_link("a:b-c_d"), "https://saiinfosysmdu.in/nexus/developer-credentials?token=a:b-c_d")

    @override_settings(NEXUS_PUBLIC_FRONTEND_URL="https://saiinfosysmdu.in//nexus//", NEXUS_FRONTEND_BASE_PATH="/nexus")
    def test_no_double_slash_or_nexus_nexus(self):
        link = s.developer_credentials_link("t")
        self.assertEqual(link, "https://saiinfosysmdu.in/nexus/developer-credentials?token=t")
        self.assertNotIn("/nexus/nexus", link)
        self.assertNotIn("//developer-credentials", link)

    @override_settings(NEXUS_PUBLIC_FRONTEND_URL="", NEXUS_PUBLIC_APP_URL="", NEXUS_FRONTEND_BASE_PATH="/nexus")
    def test_origin_without_path_gets_the_base_path(self):  # the confirmed production bug: Origin was the bare domain
        self.assertEqual(s.developer_credentials_link("t", "https://saiinfosysmdu.in"), "https://saiinfosysmdu.in/nexus/developer-credentials?token=t")
        self.assertEqual(s.developer_credentials_link("t", "https://saiinfosysmdu.in/nexus/"), "https://saiinfosysmdu.in/nexus/developer-credentials?token=t")

    @override_settings(NEXUS_PUBLIC_FRONTEND_URL="", NEXUS_PUBLIC_APP_URL="", NEXUS_FRONTEND_BASE_PATH="/")
    def test_deployment_at_root_is_supported(self):
        self.assertEqual(s.developer_credentials_link("t", "https://nexus.example.com"), "https://nexus.example.com/developer-credentials?token=t")

    @override_settings(NEXUS_PUBLIC_FRONTEND_URL="", NEXUS_PUBLIC_APP_URL="")
    def test_no_usable_address_gives_no_link(self):
        self.assertEqual(s.developer_credentials_link("t", ""), "")
        self.assertEqual(s.developer_credentials_link("t", "javascript:alert(1)"), "")

    def test_universal_api_routes_are_unchanged(self):
        for path, view in (("/api/integrations/v1/customers/register", "RegisterView"), ("/api/integrations/v1/sessions/introspect", "IntrospectView"),
                           ("/api/integrations/developer-credentials/redeem/", "DeveloperCredentialRedeemView")):
            self.assertEqual(resolve(path).func.view_class.__name__, view)
