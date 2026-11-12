"""Read-only datasource checks. Run with the same environment/identity as Gunicorn."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.dashboard import usage
from apps.registrations.models import ProjectCustomer

TABLES = {
    'gstr2tally': ('licensed_device_tbl', 'product_license_tbl', 'auth_user', 'superadmin_customerprofile', 'companydetails_tbl', 'local_tally_agent_tbl', 'gst_import_batch_tbl', 'tally_import_job_tbl'),
    'prn-auto-print': ('Customers', 'Licenses', 'LicenseActivations'),
}


class Command(BaseCommand):
    help = 'Read-only dashboard SQL metadata/count checks; no customer identities or credentials are printed.'

    def handle(self, *args, **options):
        failed = False
        for code, project in usage.PROJECTS.items():
            db = getattr(settings, project['db_setting'], '')
            result = {'project': code, 'database': db, 'expected_schema': 'dbo'}
            try:
                # Keep Nexus coverage visible even when a legacy database is inaccessible.
                result['nexus_by_status'] = {status: ProjectCustomer.objects.filter(project=code, status=status).count() for status in (ProjectCustomer.ACTIVE, ProjectCustomer.INACTIVE, ProjectCustomer.TRIAL_CANCELLED)}
                # Validate database identifiers with the same rule as the real queries.
                usage._base_sql(code)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT HAS_DBACCESS(%s)', [db])
                    result['database_access'] = cursor.fetchone()[0]
                    cursor.execute(f'SELECT s.name, t.name FROM [{db}].sys.tables t JOIN [{db}].sys.schemas s ON s.schema_id = t.schema_id')
                    found = set(cursor.fetchall())
                    result['tables'] = []
                    for name in TABLES[code]:
                        obj = f'[{db}].[dbo].[{name}]'
                        cursor.execute("SELECT HAS_PERMS_BY_NAME(%s, 'OBJECT', 'SELECT')", [obj])
                        result['tables'].append({'name': name, 'visible_in_dbo': ('dbo', name) in found, 'visible_schemas': [schema for schema, table in found if table == name], 'can_select': cursor.fetchone()[0]})
                    if code == 'prn-auto-print':
                        cursor.execute(f"""SELECT COUNT(*) FROM [{db}].dbo.Licenses l
                            WHERE l.status <> 'REVOKED' AND (COALESCE(l.serial_number, '') = '' OR NOT EXISTS (
                                SELECT 1 FROM registrations_projectcustomer n
                                WHERE n.project = 'prn-auto-print' AND n.serial_number = l.serial_number))""")
                        result['legacy_nonrevoked_licenses_missing_from_nexus'] = cursor.fetchone()[0]
                        cursor.execute(f"""SELECT COUNT(*) FROM [{db}].dbo.Customers c WHERE NOT EXISTS (
                            SELECT 1 FROM [{db}].dbo.Licenses l WHERE l.customer_id = c.id AND l.status <> 'REVOKED')""")
                        result['legacy_customers_without_nonrevoked_license'] = cursor.fetchone()[0]
                        cursor.execute("SELECT HAS_PERMS_BY_NAME(%s, 'OBJECT', 'SELECT')", [f'[{db}].[dbo].[django_migrations]'])
                        if cursor.fetchone()[0] == 1:
                            cursor.execute(f"SELECT name FROM [{db}].dbo.django_migrations WHERE app = %s ORDER BY name", ['licensing'])
                            result['legacy_licensing_migrations'] = [row[0] for row in cursor.fetchall()]
                        result['migration_note'] = 'Migration names are schema evidence only; they do not prove customer migration completeness. Missing serial coverage or unlinked legacy customers blocks removing legacy counting.'
                result['summary'] = usage.summary(code)
                result['list_totals'] = {category: usage.listing(code, category)['total'] for category in ('used', 'not-used', 'expired')}
                result['report_totals'] = {category: len(usage.report_rows(code, category)) for category in ('used', 'not-used')}
                result['counts_match'] = all(result['list_totals'][category] == result['summary'][field] for category, field in (('used', 'used'), ('not-used', 'not_used'), ('expired', 'expired')))
                result['reports_match'] = result['report_totals'] == {category: result['list_totals'][category] for category in ('used', 'not-used')}
                failed = failed or not result['counts_match'] or not result['reports_match']
            except Exception as exc:
                failed = True
                # Metadata invisibility is not proof a table does not exist. The real SELECT is authoritative.
                result['failure_type'] = type(exc).__name__
                result['error'] = str(exc)
                result['note'] = 'No fallback or zero counts were substituted. Missing metadata may also mean insufficient permissions.'
            self.stdout.write(json.dumps(result, default=str))
        if failed:
            raise CommandError('Dashboard source checks failed; review the per-project diagnostics above.')
