from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from . import usage
from .views import ProjectUsageSummaryView


class SourceFailureTests(SimpleTestCase):
    def test_expiry_database_failure_is_project_unavailable(self):
        with patch.object(usage, '_run', return_value=[(1, 1)]), patch.object(usage, '_expiring', side_effect=DatabaseError('expiry query failed')):
            with self.assertRaises(usage.ProjectUnavailable):
                usage.summary('gstr2tally')

    def test_invalid_source_configuration_is_project_unavailable(self):
        with patch.object(usage, '_base_sql', side_effect=ImproperlyConfigured('invalid database name')):
            with self.assertRaises(usage.ProjectUnavailable):
                usage.summary('gstr2tally')
            with self.assertRaises(usage.ProjectUnavailable):
                usage.listing('gstr2tally', 'used')
            with self.assertRaises(usage.ProjectUnavailable):
                usage.report_rows('gstr2tally')

    def test_summary_isolates_and_logs_real_failure_without_zero_counts(self):
        counts = dict(total=3, used=2, not_used=1, expired=0, expiring=0)
        request = APIRequestFactory().get('/api/dashboard/project-usage/')
        with patch('apps.dashboard.views.user_can', return_value=True), patch.object(usage, 'summary', side_effect=[usage.ProjectUnavailable('source unavailable'), counts]), self.assertLogs('apps.dashboard.views', level='ERROR') as logs:
            response = ProjectUsageSummaryView.as_view(authentication_classes=[])(request)
        self.assertEqual(response.status_code, 200)
        failed, healthy = response.data['projects']
        self.assertFalse(failed['available'])
        self.assertNotIn('used', failed)
        self.assertTrue(healthy['available'])
        self.assertEqual(healthy['used'], 2)
        self.assertIn('gstr2tally', logs.output[0])

    def test_confirmed_sql_errors_remain_unavailable_without_fallback(self):
        for message in ("208: Invalid object name 'PRNPRINT.dbo.Customers'", "916: nexus_app cannot access Gstr_db"):
            with self.subTest(message=message), patch.object(usage, 'legacy_state', return_value={'included': True, 'reason': ''}), patch.object(usage, '_run', side_effect=DatabaseError(message)):
                with self.assertRaises(usage.ProjectUnavailable) as raised:
                    usage.summary('prn-auto-print' if message.startswith('208') else 'gstr2tally')
                self.assertIsInstance(raised.exception.__cause__, DatabaseError)

    def test_diagnostic_retains_nexus_coverage_when_external_access_fails(self):
        import json
        from io import StringIO
        from django.core.management.base import CommandError
        from .management.commands.check_dashboard_sources import Command
        output = StringIO()
        command = Command(stdout=output)
        with patch('apps.dashboard.management.commands.check_dashboard_sources.ProjectCustomer.objects') as customers, patch.object(usage, '_base_sql', side_effect=usage.ProjectUnavailable('external source denied')):
            customers.filter.return_value.count.return_value = 5
            with self.assertRaises(CommandError):
                command.handle()
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result['nexus_by_status']['ACTIVE'], 5)
            self.assertNotIn('summary', result)
            self.assertEqual(result['failure_type'], 'ProjectUnavailable')
