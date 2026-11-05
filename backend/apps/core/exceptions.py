import logging

from django.db import DatabaseError
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """Every error leaves the API as {"message": str, "errors": {field: [str, ...]}}."""
    response = exception_handler(exc, context)
    if response is None:
        # Not an API error: without this Django answers with an HTML 500 page, which a reverse proxy / the SPA cannot read.
        logger.error("Unhandled error in %s", type(context.get("view")).__name__, exc_info=exc)
        if isinstance(exc, DatabaseError):
            return Response(
                {"message": "The database is temporarily unavailable. Please try again in a moment.", "errors": {}, "code": "database_unavailable"},
                status=503,
            )
        return Response({"message": "Something went wrong on the server. Please try again.", "errors": {}, "code": "server_error"}, status=500)

    if isinstance(exc, ValidationError):
        errors = response.data if isinstance(response.data, dict) else {"non_field_errors": response.data}
        response.data = {"message": "Please correct the highlighted fields.", "errors": errors}
    else:
        detail = response.data.get("detail", "Request failed.") if isinstance(response.data, dict) else "Request failed."
        response.data = {"message": str(detail), "errors": {}}
    return response
