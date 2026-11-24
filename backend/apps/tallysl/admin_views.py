"""Staff API for Tally SL: /api/v1/tally-sl-admin/... (session auth, same Superadmin/Admin accounts as the rest of Nexus).
Every endpoint resolves the product(s) first and checks the matching per-product permission server-side."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.registrations import exports

from . import access, emails, events, services
from apps.accounts.constants import Role
from apps.accounts.permissions import get_role

from .models import (
    PERMISSION_ACTIONS, PERMISSION_LABELS, TallyAdminPermission, TallyCustomer, TallyDevice, TallyLicense, TallyProduct,
    TallyTransferEvent,
)

PAGE_SIZES = (10, 25, 50, 100)


class StaffView(APIView):
    permission_classes = [access.IsStaff]

    def handle_exception(self, exc):
        if isinstance(exc, services.TallyError):
            return Response({"message": str(exc), "code": exc.code, "errors": exc.errors}, status=exc.status)
        return super().handle_exception(exc)

    def ip(self):
        return services.client_ip(self.request)

    def product_or_404(self, code):
        product = TallyProduct.objects.filter(code=code).first()
        if product is None:
            raise NotFound("Unknown product.")
        return product

    def customer_or_404(self, product, pk, action):
        """Looks the customer up INSIDE the product and requires `action` on that product."""
        access.require(self.request.user, product, action)
        customer = TallyCustomer.objects.select_related("product", "license").filter(product=product, pk=pk).first()
        if customer is None:
            raise NotFound("Customer not found.")
        return customer


def _dt(value):
    return value.isoformat() if value else None


def customer_row(c):
    lic = c.license
    return {
        "id": c.pk, "product": c.product.code, "product_name": c.product.name, "name": c.name, "email": c.email,
        "contact_number": c.contact_number, "tally_serial": c.tally_serial, "user_id": c.tally_serial,
        "company_name": c.company_name, "gstin": c.gstin,
        "license_type": lic.license_type, "license_plan": services.plan_of(lic), "license_type_label": lic.get_license_type_display(),
        "license_status": lic.status, "state": services.license_state(lic),
        "activated_at": _dt(lic.activated_at), "expires_at": _dt(None if lic.license_type == "LIFETIME" else lic.expires_at),
        "registered_at": _dt(c.registered_at), "must_change_password": c.must_change_password,
        "password_changed_at": _dt(c.password_changed_at),
        "password_status": "Changed by customer" if c.password_changed_at else "Temporary password issued",
        "credentials_email_status": c.welcome_email_status, "welcome_email_status": c.welcome_email_status, "activation_email_status": c.license.activation_key_email_status,
    }


def _paginate(qs_or_list, request):
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
        size = int(request.query_params.get("page_size", PAGE_SIZES[0]))
    except ValueError:
        raise ValidationError({"page": ["Page and page size must be numbers."]})
    if size not in PAGE_SIZES:
        raise ValidationError({"page_size": [f"Page size must be one of {', '.join(map(str, PAGE_SIZES))}."]})
    total = len(qs_or_list) if isinstance(qs_or_list, list) else qs_or_list.count()
    start = (page - 1) * size
    return qs_or_list[start:start + size], {"page": page, "page_size": size, "total": total}


def _dates(request):
    from datetime import date
    out = []
    for k in ("from", "to"):
        v = request.query_params.get(k)
        try:
            out.append(date.fromisoformat(v) if v else None)
        except ValueError:
            raise ValidationError({k: ["Use YYYY-MM-DD."]})
    return out


def _search(qs, term):
    term = " ".join((term or "").split())[:100]
    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(tally_serial__icontains=term) | Q(email__icontains=term) | Q(contact_number__icontains=term))
    return qs


# ------------------------------------------------------------------ access / registry
class MyAccessView(StaffView):
    """Navigation hints: which products and actions this account holds."""

    def get(self, request):
        grants = access.access_map(request.user)
        products = [{"code": p.code, "name": p.name, "kind": p.kind, "actions": grants.get(p.code, {})}
                    for p in TallyProduct.objects.filter(active=True)]
        return Response({"products": products, "actions": PERMISSION_LABELS})


class ProductListView(StaffView):
    def get(self, request):
        rows = []
        for p in TallyProduct.objects.all():
            if not any(access.can(request.user, p, a) for a in PERMISSION_ACTIONS):
                continue
            rows.append({"code": p.code, "name": p.name, "kind": p.kind, "active": p.active, "default_trial_days": p.default_trial_days,
                         "customers": p.customers.count(), "activation_link_base": p.activation_link_base,
                         "password_reset_url": p.password_reset_url})
        return Response({"results": rows})


# ------------------------------------------------------------------ permissions config (Superadmin)
class PermissionsConfigView(StaffView):
    permission_classes = [access.IsSuperAdminOnly]

    def _payload(self):
        User = get_user_model()
        admins = User.objects.filter(profile__role="ADMIN", is_active=True).order_by("username")
        grants = {}
        for g in TallyAdminPermission.objects.select_related("product"):
            grants.setdefault(g.user_id, {})[g.product.code] = {a: getattr(g, f"can_{a}") for a in PERMISSION_ACTIONS}
        products = list(TallyProduct.objects.filter(active=True))
        return {
            "admins": [{"id": u.pk, "username": u.username,
                        "grants": {p.code: grants.get(u.pk, {}).get(p.code, {a: False for a in PERMISSION_ACTIONS}) for p in products}}
                       for u in admins],
            "products": [{"code": p.code, "name": p.name} for p in products],
            "actions": PERMISSION_LABELS,
        }

    def get(self, request):
        return Response(self._payload())

    def put(self, request):
        User = get_user_model()
        try:
            target = User.objects.get(pk=request.data.get("user_id"), profile__role="ADMIN", is_active=True)
        except (User.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"user_id": ["Choose an active Admin account."]})
        grants = request.data.get("grants")
        if not isinstance(grants, dict):
            raise ValidationError({"grants": ["Send {product: [actions]}."]})
        products = {p.code: p for p in TallyProduct.objects.all()}
        for code, actions in grants.items():
            if code not in products or not isinstance(actions, list) or any(a not in PERMISSION_ACTIONS for a in actions):
                raise ValidationError({"grants": [f"Invalid grant for {code}."]})
        with transaction.atomic():
            for code, product in products.items():
                actions = grants.get(code)
                if actions is None:
                    continue
                TallyAdminPermission.objects.update_or_create(
                    user=target, product=product, defaults={f"can_{a}": a in actions for a in PERMISSION_ACTIONS})
            services.audit("PERMISSIONS_UPDATED", f"Admin account {target.pk}", actor=services.actor_name(request.user), ip=self.ip())
        return Response(self._payload())


# ------------------------------------------------------------------ customers
class CustomerListView(StaffView):
    def get(self, request, product):
        p = self.product_or_404(product)
        access.require(request.user, p, "view")
        rows, meta = _paginate(filtered_customers(request.query_params, p), request)
        rows = list(rows)
        active = {d.customer_id: d for d in TallyDevice.objects.filter(customer__in=rows, revoked_at__isnull=True)}
        last = {}
        for e in TallyTransferEvent.objects.filter(customer__in=rows).order_by("occurred_at"):
            last[e.customer_id] = e.occurred_at
        out = []
        for c in rows:
            r = customer_row(c)
            d = active.get(c.pk)
            r["active_device"] = (d.device_name or "") if d else ""
            r["last_transfer_at"] = _dt(last.get(c.pk))
            out.append(r)
        return Response({"results": out, **meta})

    def post(self, request, product):
        """Staff creates a customer (Entry). Same rules as self-registration; license stays Pending until activated."""
        p = self.product_or_404(product)
        access.require(request.user, p, "entry")
        from .client_views import RegisterSerializer
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        customer, raw = services.register_customer(p, d, actor=services.actor_name(request.user), ip=self.ip())
        emails.send_welcome(customer, raw)
        return Response(customer_row(customer), status=201)


def filtered_customers(params, product):
    """Search + License Type (plan) + Status filters shared by the list and its exports."""
    qs = _search(TallyCustomer.objects.select_related("product", "license").filter(product=product), params.get("search"))
    plan = params.get("license_plan")
    if plan and plan != "all":
        qs = qs.filter(license__license_type=services.STORED_OF.get(plan, "?"))
    state = params.get("state")
    if state and state != "all":
        return [c for c in qs if services.license_state(c.license) == state]
    return qs


def _customers_table(rows):
    body = []
    for i, c in enumerate(rows, 1):
        r = customer_row(c)
        body.append([str(i), r["name"], r["company_name"] or "-", r["contact_number"], r["email"], r["tally_serial"],
                     PLAN_LABEL[r["license_plan"]], r["state"].title(), "Never" if r["license_type"] == "LIFETIME" else _fmt_iso(r["expires_at"])])
    return exports.Table("GSTR2Tally Customers", exports._subtitle(len(body), ""),
                         ["S.No", "Customer", "Company", "Contact", "Email", "Tally Serial / User ID", "License Type", "License Status", "Expiry Date"],
                         body, [3, 11, 10, 8, 14, 9, 8, 7, 8])


class CustomerExportView(StaffView):
    """POST {fmt: xlsx|pdf|print, filters: {search, license_plan, state}} - needs View on the product, like the list."""

    def post(self, request, product):
        p = self.product_or_404(product)
        access.require(request.user, p, "view")
        rows = list(filtered_customers(request.data.get("filters") or {}, p))
        return _export(request, _customers_table(rows), request.data.get("fmt"))


class CredentialsActionView(StaffView):
    """POST .../customers/<id>/credentials/<resend|reset-password>/ - emails a NEW temporary password to the registered address.
    Resend needs Entry (only while the customer still has the emailed password); Reset Password is Superadmin-only (as in PRN)."""

    def post(self, request, product, pk, action):
        p = self.product_or_404(product)
        if action == "resend":
            c = self.customer_or_404(p, pk, "entry")
            if c.password_changed_at:
                raise services.TallyError("This customer already chose their own password. Use Reset Password instead.", 409, "password_already_changed")
        elif action == "reset-password":
            if get_role(request.user) != Role.SUPERADMIN:
                raise PermissionDenied("Only a Super Admin can reset a customer's password.")
            c = self.customer_or_404(p, pk, "edit")
        else:
            raise NotFound("Unknown action.")
        if services.license_state(c.license) == "inactive":
            raise services.TallyError("This customer's license is inactive.", 409, "license_inactive")
        c, raw = services.issue_temp_password(c, request.user, self.ip(), action)
        status = emails.send_welcome(c, raw)
        out = {"customer": customer_row(TallyCustomer.objects.select_related("product", "license").get(pk=c.pk)),
               "credentials_email": "submitted" if status == "accepted" else status, "email_sent_to": emails.mask_email(c.email)}
        if status != "accepted":
            out["temporary_password"] = raw  # shown once so staff can pass it on another way; never stored
        return Response(out)


def customer_detail(c):
    lic = c.license
    info = getattr(c, "tally_info", None)
    availability = json.loads(info.availability) if info and info.availability else {}
    rows = services.usage_rows(TallyCustomer.objects.filter(pk=c.pk))
    usage = rows[0][1] if rows else {}
    devices = [{
        "id": str(d.device_id), "name": d.device_name, "app_version": d.app_version, "generation": d.generation,
        "enrolled_at": _dt(d.enrolled_at), "last_seen_at": _dt(d.last_seen_at), "active": d.revoked_at is None,
        "revoked_at": _dt(d.revoked_at), "revoked_reason": d.revoked_reason,
    } for d in c.devices.all()]
    history = [{"id": a.pk, "action": a.action, "detail": a.detail, "actor": a.actor, "at": _dt(a.created_at)}
               for a in c.audit.all()[:100]]
    transfers = [{"kind": t.kind, "at": _dt(t.created_at), "by": t.performed_by,
                  "from": t.from_device.device_name if t.from_device else "", "to": t.to_device.device_name if t.to_device else ""}
                 for t in c.transfers.select_related("from_device", "to_device")[:50]]
    active_dev = next((d for d in devices if d["active"]), None)
    revoked = [d for d in devices if not d["active"]]
    evs = [{"id": e.pk, "old_device": e.old_device_name, "new_device": e.new_device_name, "at": _dt(e.occurred_at),
            "initiated_by": e.initiated_by, "status": e.status} for e in c.transfer_events.all()[:100]]
    return {
        "device_summary": {
            "current": active_dev["name"] if active_dev else "", "current_activated_at": active_dev["enrolled_at"] if active_dev else None,
            "previous": evs[0]["old_device"] if evs else (revoked[0]["name"] if revoked else ""),
            "last_transfer_at": evs[0]["at"] if evs else None,
        },
        "transfer_history": evs,
        "customer": customer_row(c),
        "tally": {
            "tally_serial": c.tally_serial,
            "edition": info.edition if info else "", "tss_details": info.tss_details if info else "",
            "virtual_user_limit": info.virtual_user_limit if info else None,
            "license_administrator": info.license_administrator if info else "", "gateway_server": info.gateway_server if info else "",
            "registered_email": info.registered_email if info else "",
            "tss_expiry_date": info.tss_expiry_date.isoformat() if info and info.tss_expiry_date else None,
            "availability": availability, "reported_at": _dt(info.reported_at) if info else None,
        },
        "license": {
            "plan": services.plan_of(lic), "type": lic.license_type, "type_label": lic.get_license_type_display(), "status": lic.status, "state": services.license_state(lic),
            "trial_days": lic.trial_days, "activated_at": _dt(lic.activated_at),
            "expires_at": _dt(None if lic.license_type == "LIFETIME" else lic.expires_at),
            "activation_key_suffix": lic.activation_key_suffix, "activation_key_issued_at": _dt(lic.activation_key_issued_at),
        },
        "devices": devices, "transfers": transfers, "activation_history": history,
        "last_activity": {"last_opened": _dt(usage.get("last_opened")), "opens": usage.get("opens", 0), "active_days": usage.get("active_days", 0)},
        "classification": {"category": usage.get("category"), "label": services.CATEGORY_LABELS.get(usage.get("category"), "No usage yet")},
    }


class CustomerDetailView(StaffView):
    def get(self, request, product, pk):
        c = self.customer_or_404(self.product_or_404(product), pk, "view")
        return Response(customer_detail(c))

    def patch(self, request, product, pk):
        c = self.customer_or_404(self.product_or_404(product), pk, "edit")

        class S(serializers.Serializer):
            name = serializers.CharField(max_length=100, required=False)
            email = serializers.EmailField(required=False)
            contact_number = serializers.CharField(max_length=20, required=False)
            company_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
            gstin = serializers.RegexField(r"^$|^[0-9]{2}[A-Za-z0-9]{13}$", required=False, allow_blank=True)

        s = S(data=request.data)
        s.is_valid(raise_exception=True)
        services.update_customer(c, s.validated_data, request.user, self.ip())
        c.refresh_from_db()
        return Response(customer_row(TallyCustomer.objects.select_related("product", "license").get(pk=c.pk)))

    def delete(self, request, product, pk):
        c = self.customer_or_404(self.product_or_404(product), pk, "delete")
        services.delete_customer(c, request.user, self.ip())
        return Response(status=204)


class LicenseActionView(StaffView):
    """POST .../customers/<id>/license/<action>/ - activate | extend-trial | cancel-trial | renew | inactivate | reissue-key."""

    ACTIONS = {"activate": "license_activation", "extend-trial": "license_management", "cancel-trial": "license_management",
               "renew": "license_management", "inactivate": "license_management", "reissue-key": "license_management"}

    def post(self, request, product, pk, action):
        if action not in self.ACTIONS:
            raise NotFound("Unknown license action.")
        c = self.customer_or_404(self.product_or_404(product), pk, self.ACTIONS[action])
        ip, user, body = self.ip(), request.user, request.data
        key = None
        if action == "activate":
            c, key = services.activate_license(c, body.get("license_plan") or body.get("license_type"), body.get("trial_days"), user, ip,
                                               years=body.get("years"), custom_days=body.get("custom_days"), password=body.get("password") or None)
        elif action == "extend-trial":
            c = services.extend_trial(c, body.get("days"), user, ip)
        elif action == "cancel-trial":
            c = services.cancel_trial(c, user, ip)
        elif action == "renew":
            c = services.renew_license(c, user, ip)
        elif action == "inactivate":
            c = services.inactivate_license(c, user, ip)
        else:
            c, key = services.reissue_key(c, user, ip)
        # Activation Keys are emailed ONLY when the customer requests activation (Activate User), never by a staff action.
        if key:
            TallyLicense.objects.filter(customer=c).update(activation_key_email_status="on_request")
        c = TallyCustomer.objects.select_related("product", "license").get(pk=c.pk)
        out = {"customer": customer_row(c)}
        if key:
            out.update(activation_key=key, activation_key_email="on_request")
        if action == "activate" and body.get("password"):
            # AFTER the commit: a mail failure is reported separately and never undoes or repeats the activation.
            from . import emails
            status = emails.send_license_activated(c, body["password"])
            out["credentials_email"] = "submitted" if status == "accepted" else status
            out["email_sent_to"] = emails.mask_email(c.email)
        return Response(out)


class DeviceRevokeView(StaffView):
    def post(self, request, product, pk):
        c = self.customer_or_404(self.product_or_404(product), pk, "device_management")
        services.revoke_device(c, request.user, str(request.data.get("reason") or "Revoked by an administrator"), self.ip())
        return Response({"message": "Device revoked. The customer must use Activate User to enrol a computer again."})


# ------------------------------------------------------------------ usage dashboard
def _scoped_customers(user, action, product_code=None):
    products = access.permitted_products(user, action)
    if product_code:
        products = products.filter(code=product_code)
    return TallyCustomer.objects.filter(product__in=products), products


def usage_counts(rows):
    """Project card counts, same shape as Barcode AutoPrint's Used / Not Used / Expire.
    Used = at least one successful application opening/sign-in recorded by Nexus; Not Used = none yet;
    Expire = Nexus licence expired (Trial/Annual past its expiry). Every registered customer is counted once."""
    used = sum(1 for _c, u in rows if u["opens"] >= 1)
    expired = sum(1 for c, _u in rows if services.license_state(c.license) == "expired")
    return {"used": used, "not_used": len(rows) - used, "expired": expired, "total": len(rows)}


class DashboardView(StaffView):
    def get(self, request):
        customers, products = _scoped_customers(request.user, "view", request.query_params.get("product"))
        rows = services.usage_rows(customers)
        counts = {k: 0 for k in services.CATEGORIES}
        internal = {"zero_use": 0, "dormant": 0}
        for _c, u in rows:
            if u["category"] in counts:
                counts[u["category"]] += 1
            else:
                internal[u["category"]] += 1
        now = timezone.now()
        by_code = {}
        for c, u in rows:
            by_code.setdefault(c.product.code, []).append((c, u))
        return Response({
            "categories": [{"key": k, "label": services.CATEGORY_LABELS[k], "count": counts[k]} for k in services.CATEGORIES],
            "usage_counts": usage_counts(rows),
            "products": [{"code": p.code, "name": p.name, **usage_counts(by_code.get(p.code, []))} for p in products if p.active],
            "internal": internal,
            "registrations": {
                "total": customers.count(),
                "last_7_days": customers.filter(registered_at__gte=now - timedelta(days=7)).count(),
                "pending_license": customers.filter(license__status=TallyLicense.PENDING).count(),
                "recent_device_transfers": events.visible_events(request.user, request.query_params.get("product")).filter(occurred_at__gte=now - timedelta(days=7)).count(),
                "by_product": [{"code": p.code, "name": p.name, "customers": customers.filter(product=p).count()} for p in products],
            },
            "rule": _rule_data(),
        })


class NotificationListView(StaffView):
    """Bell feed: completed GSTR2Tally device transfers the caller may view (per-product `view`), newest first."""

    def get(self, request):
        from apps.dashboard.models import NotificationDismissal, dismissed_ids
        qs = events.visible_events(request.user).exclude(pk__in=dismissed_ids(request.user, NotificationDismissal.TALLY))  # this user's X only
        return Response({"unread_count": qs.filter(read_at__isnull=True).count(),
                         "results": [events.event_data(e) for e in qs[:30]]})


class NotificationReadView(StaffView):
    def post(self, request, pk):
        e = events.visible_events(request.user).filter(pk=pk).first()
        if e is None:
            raise NotFound("Notification not found.")
        if e.read_at is None:
            TallyTransferEvent.objects.filter(pk=pk, read_at__isnull=True).update(read_at=timezone.now(), read_by=services.actor_name(request.user))
        return Response({"id": e.pk, "read": True})


class NotificationDismissView(StaffView):
    """X on a bell row: hidden for the current user only. The transfer event itself is never deleted or changed."""

    def post(self, request, pk):
        from apps.dashboard.models import NotificationDismissal as D
        if D.objects.filter(user=request.user, source=D.TALLY, notification_id=pk).exists():
            return Response({"ok": True, "dismissed": pk})  # repeat click: already hidden
        if not events.visible_events(request.user).filter(pk=pk).exists():
            raise NotFound("Notification not found.")
        D.objects.get_or_create(user=request.user, source=D.TALLY, notification_id=pk)
        return Response({"ok": True, "dismissed": pk})


class TransferListView(StaffView):
    """Read-only list of completed device transfers (newest first); ?recent=1 limits it to the last 7 days."""

    def get(self, request, product):
        p = self.product_or_404(product)
        access.require(request.user, p, "view")
        qs = events.visible_events(request.user, product)
        if request.query_params.get("recent"):
            qs = qs.filter(occurred_at__gte=timezone.now() - timedelta(days=7))
        rows, meta = _paginate(qs, request)
        return Response({"results": [events.event_data(e) for e in rows], **meta})


def _rule_data():
    r = services.usage_rule()
    return {"new_user_days": r.new_user_days, "speed_window_days": r.speed_window_days, "speed_min_active_days": r.speed_min_active_days}


def _usage_list(request, category):
    customers, _ = _scoped_customers(request.user, "view", request.query_params.get("product"))
    customers = _search(customers, request.query_params.get("search"))
    rows = services.usage_rows(customers)
    if category:
        rows = [(c, u) for c, u in rows if u["category"] == category]
    return sorted(rows, key=lambda r: (r[1]["last_opened"] is None, -(r[1]["last_opened"].timestamp() if r[1]["last_opened"] else 0)))


def usage_item(c, u):
    return {"id": c.pk, "product": c.product.code, "product_name": c.product.name, "name": c.name, "tally_serial": c.tally_serial,
            "last_opened": _dt(u["last_opened"]), "opens": u["opens"], "active_days": u["active_days"], "category": u["category"]}


class UsageListView(StaffView):
    def get(self, request, category):
        if category not in services.CATEGORIES:
            raise NotFound("Unknown category.")
        rows, meta = _paginate(_usage_list(request, category), request)
        return Response({"category": category, "label": services.CATEGORY_LABELS[category], "results": [usage_item(c, u) for c, u in rows], **meta})


# ------------------------------------------------------------------ reports
def _fmt_date(value):
    return timezone.localtime(value).strftime("%d/%m/%Y") if value else "-"


def expiry_rows(request):
    customers = access.permitted_products(request.user, "reports")
    qs = TallyCustomer.objects.select_related("product", "license").filter(product__in=customers)
    p = request.query_params
    if p.get("product"):
        qs = qs.filter(product__code=p["product"])
    if p.get("license_type"):
        qs = qs.filter(license__license_type=services.STORED_OF.get(p["license_type"], p["license_type"]))
    qs = _search(qs, p.get("search"))
    start, end = _dates(request)
    if start:
        qs = qs.filter(license__expires_at__date__gte=start)
    if end:
        qs = qs.filter(license__expires_at__date__lte=end)
    rows = [customer_row(c) for c in qs]
    if p.get("status"):
        rows = [r for r in rows if r["state"] == p["status"]]
    return rows


def tally_rows(request):
    products = access.permitted_products(request.user, "reports")
    qs = TallyCustomer.objects.select_related("product", "tally_info").filter(product__in=products)
    p = request.query_params
    if p.get("product"):
        qs = qs.filter(product__code=p["product"])
    term = " ".join((p.get("search") or "").split())[:100]
    if term:
        qs = qs.filter(Q(tally_serial__icontains=term) | Q(tally_info__registered_email__icontains=term) | Q(name__icontains=term))
    start, end = _dates(request)
    if start:
        qs = qs.filter(tally_info__tss_expiry_date__gte=start)
    if end:
        qs = qs.filter(tally_info__tss_expiry_date__lte=end)
    out = []
    for c in qs:
        info = getattr(c, "tally_info", None)
        out.append({"id": c.pk, "product": c.product.code, "product_name": c.product.name, "tally_serial": c.tally_serial,
                    "tally_email": (info.registered_email or None) if info else None,
                    "tally_expiry_date": info.tss_expiry_date.isoformat() if info and info.tss_expiry_date else None})
    return out


class ExpiryReportView(StaffView):
    def get(self, request):
        rows, meta = _paginate(expiry_rows(request), request)
        return Response({"results": rows, **meta})


class TallyDetailsReportView(StaffView):
    def get(self, request):
        rows, meta = _paginate(tally_rows(request), request)
        return Response({"results": rows, **meta})


PLAN_LABEL = {"TRIAL": "Trial", "ANNUAL": "Annual (1 Year)", "LIFETIME": "Lifetime"}


def _expiry_table(rows):
    body = [[str(i), r["name"], r["product_name"], r["tally_serial"], PLAN_LABEL[r["license_plan"]],
             _fmt_iso(r["activated_at"]), "Never" if r["license_type"] == "LIFETIME" else _fmt_iso(r["expires_at"]), r["state"].title()]
            for i, r in enumerate(rows, 1)]
    return exports.Table("Tally SL License Expiry Report", exports._subtitle(len(body), ""),
                         ["S.No", "Customer", "Product", "Tally Serial No.", "License Plan", "Nexus Activation Date", "Nexus Expiry Date", "License Status"],
                         body, [3, 12, 9, 9, 8, 8, 8, 7])


def _fmt_iso(value):
    if not value:
        return "-"
    from datetime import datetime
    return datetime.fromisoformat(value).astimezone(exports.IST).strftime("%d/%m/%Y")


def _tally_table(rows):
    body = [[str(i), r["tally_serial"], r["tally_email"] or "Not available", _fmt_plain(r["tally_expiry_date"]) or "Not available"] for i, r in enumerate(rows, 1)]
    return exports.Table("Tally SL Tally Details Report", exports._subtitle(len(body), ""),
                         ["S.No", "Tally Serial No.", "Tally Email ID", "Tally Expiry Date"], body, [3, 10, 16, 9])


def _fmt_plain(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else ""


def _export(request, table, fmt):
    if fmt == "print":
        return Response(table.as_json())
    if fmt == "xlsx":
        data, ctype = exports.build_xlsx(table), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pdf":
        data, ctype = exports.build_pdf(table), "application/pdf"
    else:
        raise ValidationError({"fmt": ["Choose xlsx, pdf or print."]})
    name = exports.file_name(table, fmt)
    response = HttpResponse(data, content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["X-Filename"] = name
    return response


class ExpiryExportView(StaffView):
    def post(self, request):
        request._request.GET = request._request.GET.copy()
        for k, v in (request.data.get("filters") or {}).items():
            request._request.GET[k] = str(v)
        return _export(request, _expiry_table(expiry_rows(request)), request.data.get("fmt"))


class TallyDetailsExportView(StaffView):
    def post(self, request):
        request._request.GET = request._request.GET.copy()
        for k, v in (request.data.get("filters") or {}).items():
            request._request.GET[k] = str(v)
        return _export(request, _tally_table(tally_rows(request)), request.data.get("fmt"))


def performance_rows(request):
    customers = access.permitted_products(request.user, "reports")
    qs = TallyCustomer.objects.filter(product__in=customers)
    p = request.query_params
    if p.get("product"):
        qs = qs.filter(product__code=p["product"])
    qs = _search(qs, p.get("search"))
    start, end = _dates(request)
    rows = []
    for c, u in services.usage_rows(qs):
        if p.get("category") and u["category"] != p["category"]:
            continue
        if p.get("usage") == "used" and u["opens"] < 1:
            continue
        if p.get("usage") == "not_used" and u["opens"] >= 1:
            continue
        opened = timezone.localtime(u["last_opened"], services.IST).date() if u["last_opened"] else None
        if (start or end) and (opened is None or (start and opened < start) or (end and opened > end)):
            continue
        item = usage_item(c, u)
        item["category_label"] = services.CATEGORY_LABELS.get(u["category"], "No usage yet" if u["category"] == "zero_use" else "Inactive")
        rows.append(item)
    return rows


class PerformanceReportView(StaffView):
    def get(self, request):
        rows, meta = _paginate(performance_rows(request), request)
        return Response({"results": rows, **meta})


def _performance_table(rows):
    body = [[str(i), r["name"], r["tally_serial"], r["category_label"], str(r["opens"]), _fmt_iso(r["last_opened"]), str(r["active_days"])]
            for i, r in enumerate(rows, 1)]
    return exports.Table("Tally SL User Performance Report", exports._subtitle(len(body), ""),
                         ["S.No", "Customer Name", "Tally Serial No.", "User Category", "Total Opens", "Last Opened", "Active Days"],
                         body, [3, 14, 10, 9, 6, 9, 6])


class PerformanceExportView(StaffView):
    def post(self, request):
        request._request.GET = request._request.GET.copy()
        for k, v in (request.data.get("filters") or {}).items():
            request._request.GET[k] = str(v)
        return _export(request, _performance_table(performance_rows(request)), request.data.get("fmt"))
