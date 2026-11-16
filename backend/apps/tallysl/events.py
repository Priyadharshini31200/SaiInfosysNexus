"""GSTR2Tally -> Nexus device-transfer notifications (notification consumer only; Nexus never approves a transfer).

Delivery: POST /api/v1/tally-sl/<product>/events/device-transfer/
  X-Product-Key:        the product's key (origin authentication, same key family as the product clients)
  X-Nexus-Timestamp:    unix seconds (must be within EVENT_MAX_SKEW seconds - limits replay of a captured request)
  X-Nexus-Signature:    hex HMAC-SHA256(TALLY_EVENT_SIGNING_SECRET, f"{timestamp}.{raw body}")  (integrity)
Retries are safe: the same event_id is stored once and answered 200 {"duplicate": true}."""
import hashlib
import hmac
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, services
from .client_views import ClientThrottle, IsProductClient, ProductKeyAuthentication
from .models import TallyCustomer, TallyTransferEvent

EVENT_MAX_SKEW = 300


def verify_signature(request):
    secret = getattr(settings, "TALLY_EVENT_SIGNING_SECRET", "") or ""
    if not secret:
        raise PermissionDenied("Event delivery is not configured.")
    ts = request.headers.get("X-Nexus-Timestamp", "")
    sig = request.headers.get("X-Nexus-Signature", "")
    try:
        if abs(time.time() - int(ts)) > EVENT_MAX_SKEW:
            raise ValueError
    except ValueError:
        raise AuthenticationFailed("Event timestamp is missing or outside the allowed window.")
    expected = hmac.new(secret.encode(), ts.encode() + b"." + request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig.lower()):
        raise AuthenticationFailed("Event signature is invalid.")


class DeviceTransferEventSerializer(serializers.Serializer):
    event_id = serializers.RegexField(r"^[A-Za-z0-9._:-]{8,64}$")
    event_type = serializers.ChoiceField(choices=["device.transferred"])
    product = serializers.CharField(max_length=40)
    tally_serial = serializers.CharField(max_length=30)
    old_device_name = serializers.CharField(max_length=100, allow_blank=True, default="")
    new_device_name = serializers.CharField(max_length=100)
    old_device_ref = serializers.CharField(max_length=64, allow_blank=True, default="")
    new_device_ref = serializers.CharField(max_length=64, allow_blank=True, default="")
    occurred_at = serializers.DateTimeField()
    initiated_by = serializers.CharField(max_length=150, allow_blank=True, default="customer")


def normalize(data):
    """Accepts the GSTR2Tally outbox shape (DEVICE_TRANSFER_COMPLETED, nested old/new device, transferred_at) as well as the flat shape."""
    if not isinstance(data, dict) or data.get("event_type") != "DEVICE_TRANSFER_COMPLETED":
        return data
    old, new = data.get("old_device"), data.get("new_device")
    old, new = (old if isinstance(old, dict) else {}), (new if isinstance(new, dict) else {})
    return {"event_id": data.get("event_id"), "event_type": "device.transferred", "product": data.get("product"),
            "tally_serial": data.get("tally_serial"), "old_device_name": old.get("name") or "", "new_device_name": new.get("name") or "",
            "old_device_ref": str(old.get("id") or ""), "new_device_ref": str(new.get("id") or ""),
            "occurred_at": data.get("transferred_at"), "initiated_by": "customer"}


class DeviceTransferEventView(APIView):
    authentication_classes = [ProductKeyAuthentication]
    permission_classes = [IsProductClient]
    throttle_classes = [ClientThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, services.TallyError):
            return Response({"message": str(exc), "code": exc.code}, status=exc.status)
        return super().handle_exception(exc)

    def post(self, request, product):
        verify_signature(request)
        s = DeviceTransferEventSerializer(data=normalize(request.data))
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d["product"] != product:
            raise PermissionDenied("Event product does not match the authorised product.")
        if d["occurred_at"] > timezone.now() + timedelta(minutes=5):
            raise serializers.ValidationError({"occurred_at": ["Cannot be in the future."]})
        existing = TallyTransferEvent.objects.filter(event_id=d["event_id"]).first()
        if existing:
            return Response({"duplicate": True, "id": existing.pk})
        customer = TallyCustomer.objects.filter(product=request.auth.product, tally_serial=d["tally_serial"].strip()).first()
        if customer is None:  # unknown customer: 404 so the sender retries once the customer exists
            raise services.TallyError("No customer with this Tally serial number.", 404, "customer_not_found")
        try:
            with transaction.atomic():
                ev = TallyTransferEvent.objects.create(
                    event_id=d["event_id"], product=request.auth.product, customer=customer,
                    customer_name=customer.name, tally_serial=customer.tally_serial,
                    old_device_name=d["old_device_name"], new_device_name=d["new_device_name"],
                    old_device_ref=d["old_device_ref"], new_device_ref=d["new_device_ref"],
                    occurred_at=d["occurred_at"], initiated_by=d["initiated_by"] or "customer",
                )
        except IntegrityError:  # a concurrent retry won the race
            ev = TallyTransferEvent.objects.get(event_id=d["event_id"])
            return Response({"duplicate": True, "id": ev.pk})
        services.audit("DEVICE_TRANSFER_NOTIFIED", f"{ev.old_device_name or 'previous'} -> {ev.new_device_name}",
                       customer=customer, actor="gstr2tally", ip=services.client_ip(request))
        return Response({"duplicate": False, "id": ev.pk}, status=201)


# ------------------------------------------------------------------ staff side
def event_data(e):
    return {
        "id": e.pk, "product": e.product.code, "product_name": e.product.name, "customer_id": e.customer_id,
        "customer_name": e.customer_name, "tally_serial": e.tally_serial,
        "old_device": e.old_device_name, "new_device": e.new_device_name,
        "occurred_at": e.occurred_at.isoformat(), "initiated_by": e.initiated_by, "status": e.status,
        "read": e.read_at is not None,
        "title": f"{e.product.name} – Device Transferred",
        "message": f"{e.customer_name} transferred their {e.product.name} license from {e.old_device_name or 'previous device'} to {e.new_device_name}.",
    }


def visible_events(user, product_code=None):
    products = access.permitted_products(user, "view")
    if product_code:
        products = products.filter(code=product_code)
    return TallyTransferEvent.objects.select_related("product").filter(product__in=products)
