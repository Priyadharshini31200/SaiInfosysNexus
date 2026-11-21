"""Company branding (name, GSTIN, logo). ONE row for the whole installation; every signed-in staff member may read it, only Superadmin (or an Admin holding an
explicit 'company-profile' edit grant) may change it. The logo is re-encoded with Pillow (rejects anything that is not a real PNG/JPEG/WebP) and kept in the database,
served only to authenticated users - never as a user-supplied file on disk."""
import hashlib
import io
import re

from django.http import HttpResponse
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CompanyProfile
from .permissions import IsAnyStaff, get_role, user_can
from .constants import Role

MAX_LOGO = 2 * 1024 * 1024
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def can_edit(user):
    return get_role(user) == Role.SUPERADMIN or (get_role(user) == Role.ADMIN and user_can(user, "company-profile", "edit"))


def payload(p, user):
    return {"name": p.name, "gstin": p.gstin, "has_logo": bool(p.logo), "logo_version": p.logo_version, "can_edit": can_edit(user)}


class CompanyProfileView(APIView):
    permission_classes = [IsAnyStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        return Response(payload(CompanyProfile.load(), request.user))

    def put(self, request):
        if not can_edit(request.user):
            raise PermissionDenied("You do not have permission to change the company profile.")
        errors = {}
        name = " ".join(str(request.data.get("name", "")).split())
        gstin = str(request.data.get("gstin", "")).strip().upper()
        if not 2 <= len(name) <= 150:
            errors["name"] = ["Company Name is required (2-150 characters)."]
        if gstin and not GSTIN.match(gstin):
            errors["gstin"] = ["Enter a valid 15-character GSTIN, or leave it blank."]
        logo = request.FILES.get("logo")
        data = ctype = None
        if logo is not None:
            if logo.size > MAX_LOGO:
                errors["logo"] = ["The logo must be 2 MB or smaller."]
            else:
                raw = logo.read(MAX_LOGO + 1)
                try:
                    img = Image.open(io.BytesIO(raw))
                    fmt = img.format
                    img.verify()
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                    fmt = None
                if fmt not in FORMATS:
                    errors["logo"] = ["Upload a PNG, JPG or WebP image."]
                else:
                    img = Image.open(io.BytesIO(raw))
                    img.thumbnail((512, 512))  # bounded size; re-encoding drops any embedded payload / metadata
                    out = io.BytesIO()
                    if fmt == "JPEG":
                        img.convert("RGB").save(out, "JPEG", quality=90)
                    else:
                        img.save(out, fmt)
                    data, ctype = out.getvalue(), FORMATS[fmt]
        if errors:
            raise ValidationError(errors)
        p = CompanyProfile.load()
        p.name, p.gstin = name, gstin
        if data is not None:
            p.logo, p.logo_type = data, ctype
            p.logo_version = hashlib.sha1(data).hexdigest()[:10]
        elif str(request.data.get("remove_logo", "")).lower() in ("1", "true", "yes"):
            p.logo, p.logo_type, p.logo_version = None, "", ""
        p.updated_by = request.user
        p.save()
        return Response(payload(p, request.user))


class CompanyLogoView(APIView):
    permission_classes = [IsAnyStaff]

    def get(self, request):
        p = CompanyProfile.load()
        if not p.logo:
            return HttpResponse(status=404)
        resp = HttpResponse(bytes(p.logo), content_type=p.logo_type or "image/png")
        resp["Cache-Control"] = "private, max-age=3600"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp
