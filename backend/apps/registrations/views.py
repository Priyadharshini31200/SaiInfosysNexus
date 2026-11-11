from django.db import IntegrityError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError, PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import Action, Role
from apps.accounts.permissions import IsSuperAdmin, get_role, user_can

from . import portal, services
from .models import PROJECTS, LicenseAuditLog, ProjectCustomer, RegistrationRequest
from .serializers import (
    ApproveRequestSerializer,
    CustomerEditSerializer,
    CustomerRegisterSerializer,
    ExtendTrialSerializer,
    RejectRequestSerializer,
    ResetPasswordSerializer,
    customer_data,
)


class ProjectAccess(BasePermission):
    """Super Admin may do everything. An Admin may do an action only while it is switched ON in
    Configuration -> Admin Permissions. Each view declares the action per HTTP method."""

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if get_role(request.user) not in (Role.SUPERADMIN, Role.ADMIN):
            return False
        required = view.required_actions.get(request.method)
        if getattr(view, "approval_registration", False) and request.method == "POST" and request.data.get("request_id"):
            required = Action.APPROVE
        if required is None:
            return False
        actions = required if isinstance(required, tuple) else (required,)  # a tuple means "any of these"
        return any(user_can(request.user, view.kwargs.get("project"), a) for a in actions)


class ProjectView(APIView):
    permission_classes = [ProjectAccess]
    required_actions = {}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # authentication and permission first
        if kwargs.get("project") not in PROJECTS:
            raise NotFound("Unknown project.")

    def customer(self, pk):
        try:
            return ProjectCustomer.objects.get(pk=pk, project=self.kwargs["project"])
        except ProjectCustomer.DoesNotExist:
            raise NotFound("Customer not found.")


def _rule_error(exc):
    return Response({"message": str(exc), "errors": {}}, status=status.HTTP_409_CONFLICT)


class CustomerListView(ProjectView):
    approval_registration = True
    required_actions = {"GET": Action.VIEW, "POST": Action.ENTRY}

    def get(self, request, project):
        return Response({"results": [customer_data(c) for c in ProjectCustomer.objects.filter(project=project).select_related("created_by", "updated_by")]})

    def post(self, request, project):
        if request.data.get("request_id") and not user_can(request.user, project, Action.APPROVE):
            raise PermissionDenied("Approval permission is required.")
        serializer = CustomerRegisterSerializer(data=request.data, context={"project": project})
        if not serializer.is_valid():
            # The very same Save sent again (double click, timeout and retry): the key is spent but everything else is valid and
            # matches the customer it already created -> answer with that customer, no second record and no second email.
            existing = services.customer_for_key(project, request.data.get("activation_key") or "")
            same_save = existing is not None and set(serializer.errors) <= {"activation_key", "email"} and all(
                str(request.data.get(field) or "").strip().lower() == str(getattr(existing, field) or "").strip().lower()
                for field in ("name", "email", "contact_number")
            )
            if same_save:
                return Response(
                    {**customer_data(existing), "email_sent": existing.credentials_email_status == "accepted", "already_created": True},
                    status=status.HTTP_200_OK,
                )
            serializer.is_valid(raise_exception=True)
        try:
            customer, replaced = services.register_with_unique_serial(project, serializer.validated_data, request.user)
        except services.RuleError as exc:
            return _rule_error(exc)
        except services.SaveError as exc:
            return Response({"message": exc.message, "code": exc.kind, "errors": {}}, status=status.HTTP_409_CONFLICT)
        # Committed. Only now do the credentials go out by email; a mail failure does not undo the registration.
        email_sent = portal.send_credentials(customer, serializer.validated_data["password"], serializer.validated_data["activation_key"])
        body = {**customer_data(customer), "email_sent": email_sent}
        if replaced:
            body["serial_reassigned_from"] = replaced
        return Response(body, status=status.HTTP_201_CREATED)


class RegistrationRequestListView(ProjectView):
    """Pending sign-ups from this project: the Super Admin / Admin notification. Needs Entry, as processing one is an Add Customer."""

    required_actions = {"GET": (Action.ENTRY, Action.APPROVE)}

    def get(self, request, project):
        pending = RegistrationRequest.objects.filter(project=project, status=RegistrationRequest.PENDING)
        return Response(
            {
                "results": [_request_data(r) for r in pending]
            }
        )


def _request_data(r):
    return {
        "id": r.id,
        "name": r.name,
        "contact_number": r.contact_number,
        "email": r.email,
        "status": r.status.lower(),
        "status_label": {"PENDING": "Pending Approval", "PROCESSED": "Approved", "REJECTED": "Rejected"}.get(r.status, r.status),
        "reject_reason": r.reject_reason,
        "customer_id": r.customer_id,
        "created_at": r.created_at,
        "processed_at": r.processed_at,
    }


class RegistrationRequestDetailView(ProjectView):
    """Open one request (pending or processed). Needs Entry, like the list."""

    required_actions = {"GET": (Action.ENTRY, Action.APPROVE)}

    def get(self, request, project, pk):
        try:
            return Response(_request_data(RegistrationRequest.objects.get(pk=pk, project=project)))
        except RegistrationRequest.DoesNotExist:
            raise NotFound("Registration request not found.")


class ApproveRegistrationRequestView(ProjectView):
    """Create the account and issue the license for a pending request in one step, then email the credentials.
    Username = Serial Number. Same permission as Add Customer (Entry); Super Admin always."""

    required_actions = {"POST": Action.APPROVE}

    def post(self, request, project, pk):
        try:
            req = RegistrationRequest.objects.get(pk=pk, project=project)
        except RegistrationRequest.DoesNotExist:
            raise NotFound("Registration request not found.")
        serializer = ApproveRequestSerializer(data=request.data, context={"project": project})
        serializer.is_valid(raise_exception=True)
        try:
            customer, password, key = services.approve_request(req, serializer.validated_data, request.user)
        except services.RuleError as exc:
            return _rule_error(exc)
        except services.SaveError as exc:
            return Response({"message": exc.message, "code": exc.kind, "errors": {}}, status=status.HTTP_409_CONFLICT)
        except IntegrityError:
            raise ValidationError({"serial_number": ["This serial number is already registered for this project."]})
        email_sent = portal.send_credentials(customer, password, key)
        body = {**customer_data(customer), "request_id": req.id, "email_sent": email_sent}
        if not email_sent:  # shown once so they can be passed on another way; never stored or logged
            body["temporary_password"] = password
            body["activation_key"] = portal.format_activation_key(key)
        return Response(body, status=status.HTTP_201_CREATED)


class RejectRegistrationRequestView(ProjectView):
    """Decline a pending request: nothing is created and nothing is emailed. Same permission as approving (Entry)."""

    required_actions = {"POST": Action.APPROVE}

    def post(self, request, project, pk):
        try:
            req = RegistrationRequest.objects.get(pk=pk, project=project)
        except RegistrationRequest.DoesNotExist:
            raise NotFound("Registration request not found.")
        serializer = RejectRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            req = services.reject_request(req, serializer.validated_data.get("reason", ""), request.user)
        except services.RuleError as exc:
            return _rule_error(exc)
        return Response(_request_data(req))


class SyncView(ProjectView):
    """A cheap fingerprint of one project's records for open sessions to poll. Every change writes an audit entry, so the
    newest entry id (plus the customer count) changes whenever anything a list shows has changed, whoever made the change."""

    required_actions = {"GET": (Action.VIEW, Action.ENTRY, Action.EDIT)}

    def get(self, request, project):
        return Response({"version": services.sync_version(project)})


class ActivityView(ProjectView):
    """Who did what, newest first: this project's audit trail, optionally for one customer. Super Admin only."""

    permission_classes = [IsSuperAdmin]

    def get(self, request, project):
        logs = LicenseAuditLog.objects.filter(project=project).select_related("customer", "actor", "actor__profile")
        customer = request.query_params.get("customer")
        if customer:
            if not customer.isdigit():
                raise ValidationError({"customer": ["Must be a customer id."]})
            logs = logs.filter(customer_id=int(customer))
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 200)
        except ValueError:
            raise ValidationError({"limit": ["Must be a number."]})
        return Response(
            {
                "results": [
                    {
                        "id": log.id,
                        "action": log.action,
                        "detail": log.detail,
                        "actor": log.actor_name or "System / customer",
                        "actor_role": get_role(log.actor) if log.actor else None,
                        "customer_id": log.customer_id,
                        "customer": log.customer.name if log.customer else "",
                        "created_at": log.created_at,
                    }
                    for log in logs[:limit]
                ]
            }
        )


class ActivationKeyView(ProjectView):
    required_actions = {"POST": (Action.ENTRY, Action.APPROVE)}

    def post(self, request, project):
        try:
            key = services.generate_activation_key(project, request.user)
        except services.RuleError as exc:
            return _rule_error(exc)
        return Response({"activation_key": key}, status=status.HTTP_201_CREATED)


class TemporaryPasswordView(ProjectView):
    """A fresh random temporary password for the Add Customer form (nothing is stored until the customer is saved)."""

    required_actions = {"POST": (Action.ENTRY, Action.APPROVE)}

    def post(self, request, project):
        return Response({"temporary_password": services.generate_temporary_password()}, status=status.HTTP_201_CREATED)


class SerialNumberView(ProjectView):
    required_actions = {"POST": (Action.ENTRY, Action.APPROVE)}

    def post(self, request, project):
        try:
            serial = services.generate_serial_number(project)
        except services.RuleError as exc:
            return _rule_error(exc)
        return Response({"serial_number": serial}, status=status.HTTP_201_CREATED)


class CustomerDetailView(ProjectView):
    required_actions = {"GET": (Action.VIEW, Action.EDIT), "PATCH": Action.EDIT, "DELETE": Action.DELETE}  # Edit needs to load the record

    def delete(self, request, project, pk):
        customer = self.customer(pk)  # scoped to this project: another product's customer is a 404
        expected = customer.serial_number or customer.user_id
        if str(request.data.get("confirm_serial") or "").strip() != expected:
            raise ValidationError({"confirm_serial": ["Type the customer's serial number to confirm."]})
        try:
            services.delete_customer(customer, request.user, superadmin=get_role(request.user) == Role.SUPERADMIN)
        except services.RuleError as exc:
            return _rule_error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get(self, request, project, pk):
        return Response(customer_data(self.customer(pk)))

    def patch(self, request, project, pk):
        customer = self.customer(pk)
        if request.data.get("password") and get_role(request.user) != Role.SUPERADMIN:
            raise PermissionDenied("Only Superadmin may reset customer passwords.")
        serializer = CustomerEditSerializer(customer, data=request.data, partial=True, context={"project": project})
        serializer.is_valid(raise_exception=True)
        try:
            customer = services.edit(customer, serializer.validated_data, request.user)
        except IntegrityError:
            raise ValidationError({"serial_number": ["This serial number is already registered for this project."]})
        return Response(customer_data(customer))


class _LicenseActionView(ProjectView):
    def post(self, request, project, pk):
        customer = self.customer(pk)
        try:
            customer = self.perform(request, customer)
        except services.RuleError as exc:
            return _rule_error(exc)
        return Response(customer_data(customer))


class ExtendTrialView(_LicenseActionView):
    required_actions = {"POST": Action.EXTEND_TRIAL}

    def perform(self, request, customer):
        serializer = ExtendTrialSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return services.extend_trial(customer, serializer.validated_data["days"], request.user)


class RenewLicenseSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class RenewLicenseView(_LicenseActionView):
    """Renews a 1-Year licence by one year. Needs the Edit permission, like extending a trial."""

    required_actions = {"POST": Action.RENEW}

    def perform(self, request, customer):
        serializer = RenewLicenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return services.renew_license(customer, request.user, serializer.validated_data["note"].strip())


class RenewalHistoryView(ProjectView):
    required_actions = {"GET": (Action.VIEW, Action.EDIT, Action.REPORTS)}

    def get(self, request, project, pk):
        customer = self.customer(pk)
        return Response(
            {
                "results": [
                    {
                        "id": r.id,
                        "previous_expires_at": r.previous_expires_at,
                        "new_expires_at": r.new_expires_at,
                        "renewed_by": r.renewed_by.username if r.renewed_by else "",
                        "note": r.note,
                        "created_at": r.created_at,
                    }
                    for r in customer.renewals.select_related("renewed_by")[:50]
                ]
            }
        )


# Cancelling a trial and inactivating a license revoke access, so they need the Delete permission.
class CancelTrialView(_LicenseActionView):
    required_actions = {"POST": Action.CANCEL_TRIAL}

    def perform(self, request, customer):
        return services.cancel_trial(customer, request.user)


class InactivateView(_LicenseActionView):
    required_actions = {"POST": Action.INACTIVATE}

    def perform(self, request, customer):
        return services.inactivate(customer, request.user)


class ReissueActivationKeyView(ProjectView):
    """Super Admin only. Replaces the customer's Activation Key (the old one stops working) and emails the new one. The key is
    returned once only when the email could not be sent."""

    permission_classes = [IsSuperAdmin]

    def post(self, request, project, pk):
        customer = self.customer(pk)
        try:
            customer, key = services.reissue_activation_key(customer, request.user)
        except services.RuleError as exc:
            return _rule_error(exc)
        email_sent = bool(customer.email) and portal.send_new_activation_key(customer, key)
        body = {**customer_data(customer), "email_sent": email_sent}
        if not email_sent:
            body["activation_key"] = portal.format_activation_key(key)
        return Response(body)


class RetryCredentialsEmailView(ProjectView):
    """Retry Email for a customer whose credentials email was not delivered. Issues a fresh temporary password and key
    (the saved password is a hash), emails them, and never returns either. Same permission as Add Customer (Entry)."""

    required_actions = {"POST": Action.ENTRY}

    def post(self, request, project, pk):
        customer = self.customer(pk)
        try:
            customer, password, key, event_id = services.reissue_unsent_credentials(customer, request.user)
        except services.RuleError as exc:
            return _rule_error(exc)
        email_sent = portal.send_credentials(customer, password, key, event_id=event_id)
        customer.refresh_from_db()
        return Response({**customer_data(customer), "email_sent": email_sent})


class ResendCredentialsView(ProjectView):
    """Resend Credentials: the deliberate staff action when a customer never received (or lost) the credentials email.
    Same customer, serial number / User ID and licence; a fresh temporary password and Activation Key are issued (the
    saved password is a hash and is never read back) and emailed, and neither is ever returned. Refused once the customer
    has used the account (Reset Password is the Super Admin action then). Same permission as Add Customer (Entry)."""

    required_actions = {"POST": Action.ENTRY}

    def post(self, request, project, pk):
        customer = self.customer(pk)
        try:
            customer, password, key, event_id = services.reissue_unsent_credentials(customer, request.user, resend=True)
        except services.RuleError as exc:
            return _rule_error(exc)
        email_sent = portal.send_credentials(customer, password, key, event_id=event_id)
        customer.refresh_from_db()
        return Response({**customer_data(customer), "email_sent": email_sent})


class ResetPasswordView(ProjectView):
    """Super Admin only. Issues a new temporary password (emailed to the customer) and forces a change at next sign-in.
    With `legacy_reference` it is also the authorized link between a legacy local account and its Nexus account."""

    permission_classes = [IsSuperAdmin]

    def post(self, request, project, pk):
        customer = self.customer(pk)
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not customer.email:
            raise ValidationError({"email": ["This customer has no email address to send the new password to."]})
        customer, password = services.reset_password(customer, request.user, serializer.validated_data.get("legacy_reference", "").strip())
        email_sent = portal.send_reset_credentials(customer, password)
        body = {**customer_data(customer), "email_sent": email_sent}
        if not email_sent:
            body["temporary_password"] = password  # shown once; never stored or logged
        return Response(body)
