import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom DRF exception handler that:
    1. Calls the default DRF handler first.
    2. Wraps all error responses in a consistent {error, detail, status_code} structure.
    3. Logs 500-level errors for monitoring.
    """
    response = exception_handler(exc, context)

    if response is not None:
        # Normalize the response body to a consistent format
        error_detail = response.data

        # Flatten list errors to a single string when possible
        if isinstance(error_detail, list) and len(error_detail) == 1:
            error_detail = error_detail[0]
        elif isinstance(error_detail, dict) and "detail" in error_detail:
            error_detail = error_detail["detail"]

        response.data = {
            "success": False,
            "status_code": response.status_code,
            "error": str(error_detail),
        }
    else:
        # Unhandled server-side exception — return a generic 500
        logger.exception(
            f"Unhandled server exception in {context.get('view', 'unknown view')}: {exc}"
        )
        response = Response(
            {
                "success": False,
                "status_code": 500,
                "error": "حدث خطأ داخلي في الخادم. يرجى المحاولة لاحقاً.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response
