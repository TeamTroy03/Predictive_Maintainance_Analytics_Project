"""Structured (JSON) logs with the per-request correlation ID; secret-looking fields are redacted."""

import json
import logging
import sys
from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

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