"""Usage and device reports for Universal Integration projects, computed ONLY from stored events (never from login counts alone).

Duration rules (documented, deterministic):
  * Events of one customer are ordered by time and processed per device.
  * Between two consecutive events the elapsed time is ACTIVE if the customer was not idle, IDLE if an IDLE_START was the last marker.
  * Active time is only credited when the gap is <= HEARTBEAT_GAP (the client must keep sending heartbeats / activity); a longer gap is
    "unknown" and is not claimed. An explicit idle period (IDLE_START .. next event) is credited in full up to IDLE_CAP.
  * LOGOUT ends the session: nothing accrues after it until the next event of a new session.
  * A customer without a single HEARTBEAT has duration = null ("telemetry unavailable"), never a guessed number.
Time is attributed to the IST calendar day on which the interval started."""
from collections import defaultdict
from datetime import datetime, timedelta

from django.utils import timezone

from . import usage
from .models import IntegrationCustomer, IntegrationSession, IntegrationUsageEvent

HEARTBEAT_GAP = timedelta(minutes=5)
IDLE_CAP = timedelta(hours=12)
IST = timezone.get_fixed_timezone(330)


def _day(dt):
    return dt.astimezone(IST).date().isoformat()


def _durations(events):
    """events: ordered list of (time, type, device). Returns {day: [active_seconds, idle_seconds]}."""
    out = defaultdict(lambda: [0.0, 0.0])
    last = {}  # device -> (time, type)
    idle = {}  # device -> bool
    for t, kind, device in events:
        prev = last.get(device)
        if prev is not None:
            gap = t - prev[0]
            if prev[1] == "LOGOUT":
                pass
            elif idle.get(device):
                out[_day(prev[0])][1] += min(gap, IDLE_CAP).total_seconds()
            elif gap <= HEARTBEAT_GAP:
                out[_day(prev[0])][0] += gap.total_seconds()
        if kind == "IDLE_START":
            idle[device] = True
        elif kind in ("IDLE_END", "APP_OPEN", "LOGIN", "HEARTBEAT", "DEVICE_ACTIVATION"):
            idle[device] = False
        last[device] = (t, kind)
    return out


def _range(params):
    def parse(v, end=False):
        if not v:
            return None
        d = datetime.fromisoformat(str(v)[:10])
        d = d.replace(tzinfo=IST)
        return d + timedelta(days=1) if end else d
    return parse(params.get("from")), parse(params.get("to"), end=True)


def usage_report(project, params):
    """Daily rows + per-customer summary. Filters: from, to (IST dates), customer (customer id), device."""
    start, end = _range(params)
    qs = IntegrationUsageEvent.objects.filter(project=project).select_related("license__customer")
    if start:
        qs = qs.filter(occurred_at__gte=start)
    if end:
        qs = qs.filter(occurred_at__lt=end)
    if params.get("customer"):
        qs = qs.filter(license__customer_id=params["customer"])
    if params.get("device"):
        qs = qs.filter(device_id=params["device"])
    by_lic = defaultdict(list)
    for e in qs.order_by("occurred_at", "id"):
        by_lic[e.license_id].append(e)
    stats = {r["license_id"]: r for r in usage.customer_rows(project)}
    daily, summary = [], []
    for lic_id, events in by_lic.items():
        row = stats.get(lic_id)
        if not row:
            continue
        has_hb = any(e.event_type == "HEARTBEAT" for e in events)
        dur = _durations([(e.occurred_at, e.event_type, e.device_id) for e in events]) if has_hb else {}
        days = defaultdict(lambda: {"opens": 0, "logins": 0, "failed": 0})
        for e in events:
            d = days[_day(e.occurred_at)]
            d["opens"] += e.event_type == "APP_OPEN"
            d["logins"] += e.event_type == "LOGIN"
        for day in sorted(days):
            a, i = dur.get(day, [0.0, 0.0]) if has_hb else (None, None)
            daily.append({"date": day, "customer_id": row["id"], "customer": row["name"], "user_id": row["user_id"], "opens": days[day]["opens"],
                          "logins": days[day]["logins"], "active_minutes": None if a is None else round(a / 60, 1), "idle_minutes": None if i is None else round(i / 60, 1)})
        tot_a = sum(v[0] for v in dur.values()) if has_hb else None
        tot_i = sum(v[1] for v in dur.values()) if has_hb else None
        last = max((e.occurred_at for e in events if e.event_type in ("APP_OPEN", "LOGIN", "HEARTBEAT", "DEVICE_ACTIVATION")), default=None)
        summary.append({"customer_id": row["id"], "customer": row["name"], "user_id": row["user_id"], "opens": sum(d["opens"] for d in days.values()),
                        "logins": sum(d["logins"] for d in days.values()), "active_days": len(days),
                        "active_minutes": None if tot_a is None else round(tot_a / 60, 1), "idle_minutes": None if tot_i is None else round(tot_i / 60, 1),
                        "duration_available": has_hb, "last_used": last, "usage_category": row["usage_category"] or ("not_used" if not row["used"] else None)})
    return {"project": project.code, "rules": project.rules, "heartbeat_gap_minutes": HEARTBEAT_GAP.seconds // 60,
            "note": "Durations come from heartbeat / idle events only. A customer without heartbeats shows no duration.",
            "daily": daily, "customers": sorted(summary, key=lambda r: r["customer"].lower())}


def device_transfer_history(project, params):
    start, end = _range(params)
    qs = IntegrationUsageEvent.objects.filter(project=project, event_type="DEVICE_TRANSFER").select_related("license__customer", "session")
    if start:
        qs = qs.filter(occurred_at__gte=start)
    if end:
        qs = qs.filter(occurred_at__lt=end)
    if params.get("customer"):
        qs = qs.filter(license__customer_id=params["customer"])
    rows = []
    for e in qs.order_by("-occurred_at"):
        prev = IntegrationSession.objects.filter(license=e.license, status="REVOKED", revoked_reason="device_transfer", revoked_at__lte=e.occurred_at + timedelta(seconds=5)).order_by("-revoked_at").first()
        c = e.license.customer
        rows.append({"at": e.occurred_at, "customer_id": c.pk, "customer": c.name, "user_id": c.user_id,
                     "from_device": (prev.device_name or prev.device_id) if prev else "", "to_device": (e.session.device_name or e.session.device_id) if e.session else e.device_id})
    return {"project": project.code, "results": rows}
