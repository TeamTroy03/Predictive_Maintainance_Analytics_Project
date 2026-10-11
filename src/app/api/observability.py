"""Correlation IDs and structured (JSON) logs.

Every request gets an ID (the caller's X-Correlation-ID if valid, otherwise a new UUID).
It is returned in the response header and written in every log line of that request.
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import Request

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
logger = logging.getLogger("api")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SECRET_WORDS = ("key", "token", "secret", "password", "authorization")
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        for name, value in vars(record).items():  # fields passed with extra={...}
            if name not in _STANDARD:
                line[name] = "***" if any(w in name.lower() for w in _SECRET_WORDS) else value
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our request log


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
