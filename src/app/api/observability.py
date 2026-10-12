"""Correlation-ID middleware (the JSON log format and `correlation_id` are in `app.core.logging`).

Every request gets an ID (the caller's X-Correlation-ID if valid, otherwise a new UUID).
It is returned in the response header and written in every log line of that request.
"""

import logging
import re
import time
import uuid

from fastapi import Request

from app.core.logging import correlation_id

logger = logging.getLogger("api")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def correlation_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    from app.api.errors import error_response  # local import avoids a circular import

    incoming = request.headers.get("X-Correlation-ID", "")
    token = correlation_id.set(incoming if _SAFE_ID.match(incoming) else str(uuid.uuid4()))
    start = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:  # last resort: log the details, return a clean 500
            logger.exception("unhandled_error")
            response = error_response(500, "internal_error", "An unexpected error occurred")
        response.headers["X-Correlation-ID"] = correlation_id.get()
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        return response
    finally:
        correlation_id.reset(token)