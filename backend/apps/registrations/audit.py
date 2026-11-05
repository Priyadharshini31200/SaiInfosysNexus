from .models import LicenseAuditLog


def record(user, action, detail="", project="", customer=None):
    LicenseAuditLog.objects.create(
        project=project,
        customer=customer,
        action=action,
        detail=detail[:300],
        actor=user if getattr(user, "pk", None) else None,
        actor_name=getattr(user, "username", "")[:150],
    )
