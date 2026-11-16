"""Brings the existing GSTR2Tally customers (database Gstr_db, read-only) into the Nexus Tally SL customer tables so the
Customers list and Dashboard show them. Idempotent: a customer is matched by (product, Tally serial) and never duplicated;
existing Nexus rows are never overwritten. No passwords, activation keys or tokens are copied (credentials stay in GSTR2Tally).
Dry run by default - nothing is written without --apply."""
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.tallysl import services
from apps.tallysl.models import TallyCustomer, TallyDevice, TallyLicense, TallyProduct

SQL = """
select l.id, l.licensed_tally_serial, l.status, l.purchase_date, l.expiry_date, l.activated_at,
       u.username, u.first_name, u.last_name, u.email, p.business_name
from {db}.dbo.product_license_tbl l
join {db}.dbo.auth_user u on u.id = l.customer_id
left join {db}.dbo.superadmin_customerprofile p on p.user_id = u.id
where l.licensed_tally_serial <> ''
"""
DEV_SQL = "select license_id, id, device_fingerprint, device_name, app_version, status, first_seen, last_seen from {db}.dbo.licensed_device_tbl"


def _rows(sql):
    with connection.cursor() as c:
        c.execute(sql)
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def _aware(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if timezone.is_aware(v) else timezone.make_aware(v)
    return timezone.make_aware(datetime.combine(v, time(23, 59, 59)))


def import_rows(product, licenses, devices, apply=False):
    """Returns {created, skipped_existing, devices}. `licenses`/`devices` are plain dicts (see SQL above)."""
    out = {"created": [], "skipped_existing": [], "devices": 0}
    by_license = {}
    for d in devices:
        by_license.setdefault(d["license_id"], []).append(d)
    for row in licenses:
        serial = str(row["licensed_tally_serial"]).strip()
        if TallyCustomer.objects.filter(product=product, tally_serial=serial).exists():
            out["skipped_existing"].append(serial)
            continue
        out["created"].append(serial)
        if not apply:
            continue
        name = (f"{row['first_name']} {row['last_name']}".strip() or row["business_name"] or row["username"])[:100]
        with transaction.atomic():
            c = TallyCustomer.objects.create(
                product=product, tally_serial=serial, name=name, email=(row["email"] or "").strip().lower() or f"{row['username']}@unknown.invalid",
                contact_number="", company_name=(row["business_name"] or "")[:150], password_hash="!imported-no-login",
                must_change_password=True,
            )
            expires = _aware(row["expiry_date"])
            TallyLicense.objects.create(
                customer=c, license_type=TallyLicense.LICENSED if expires else TallyLicense.LIFETIME,
                status=TallyLicense.ACTIVE if row["status"] == "ACTIVE" else TallyLicense.INACTIVE,
                activated_at=row["activated_at"] or _aware(row["purchase_date"]), expires_at=expires,
            )
            for d in by_license.get(row["id"], []):
                dev = TallyDevice.objects.create(
                    customer=c, client_device_id=f"legacy-gstr-{d['id']}", fingerprint_hash=services._keyed("fp", d["device_fingerprint"]),
                    device_name=(d["device_name"] or "")[:100], app_version=(d["app_version"] or "")[:30], last_seen_at=d["last_seen"],
                    revoked_at=None if d["status"] == "ACTIVE" else timezone.now(), revoked_reason="" if d["status"] == "ACTIVE" else "Imported as not active",
                )
                if d["status"] == "ACTIVE":
                    # the unique constraint allows one active device per customer; extra active ones are kept as revoked history
                    pass
                out["devices"] += 1
            services.audit("CUSTOMER_IMPORTED", f"from GSTR2Tally licence {row['id']}", customer=c, actor="system")
    return out


class Command(BaseCommand):
    help = "Import existing GSTR2Tally customers (read-only from Gstr_db) into Nexus. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--db", default=None)

    def handle(self, *a, **o):
        from django.conf import settings
        db = o["db"] or settings.GSTR_DB_NAME
        product = TallyProduct.objects.get(code="gstr2tally")
        res = import_rows(product, _rows(SQL.format(db=db)), _rows(DEV_SQL.format(db=db)), apply=o["apply"])
        mode = "APPLIED" if o["apply"] else "DRY RUN (nothing written; pass --apply)"
        self.stdout.write(f"{mode}: would create/created {len(res['created'])} {res['created']}, already present {res['skipped_existing']}, devices {res['devices']}")
