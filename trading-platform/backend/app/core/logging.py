"""Structured JSON logs with a request id, and a guard against logging secrets."""

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# Anything shaped like "password=...", "token: ..." or an Authorization header
# is masked before it reaches a log line, in case a caller slips.
_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|otp|recovery[_-]?code)\b"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


def redact(text: str) -> str:
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Access logs would repeat every URL, including single-use tokens in query
    # strings; the application logs what matters with the request id instead.
    logging.getLogger("uvicorn.access").disabled = True
