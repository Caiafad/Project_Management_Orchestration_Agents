"""
Logging for the Project Management Agent.

In Cloud Run (K_SERVICE set) every record is emitted as a single-line JSON object
with the fields Cloud Logging understands, so `severity`, the message and all
context fields are queryable — e.g.

    jsonPayload.run_id="a1b2c3d4"
    jsonPayload.event="agent_finished" AND jsonPayload.status="failed"

Locally the same records print as readable lines.

Context (user, run_id, agent) is held in contextvars and attached to every record
automatically, so a single run can be followed across the orchestrator, the
sub-agents and the tools without threading identifiers through every call.
Contextvars propagate into asyncio tasks, and `bind()` is re-applied inside the
worker thread the orchestrator runs on.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_user: ContextVar[str] = ContextVar("user", default="")
_run_id: ContextVar[str] = ContextVar("run_id", default="")
_agent: ContextVar[str] = ContextVar("agent", default="")

CONTEXT_FIELDS = ("user", "run_id", "agent")
# Attributes already on a LogRecord — anything else passed via extra= is ours.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "asctime", "message", "taskName"}

IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def bind(user: str | None = None, run_id: str | None = None, agent: str | None = None) -> dict:
    """Set context for the current task/thread. Returns the tokens' previous values
    so a caller can restore them (see `context`)."""
    previous = {"user": _user.get(), "run_id": _run_id.get(), "agent": _agent.get()}
    if user is not None:
        _user.set(user)
    if run_id is not None:
        _run_id.set(run_id)
    if agent is not None:
        _agent.set(agent)
    return previous


def current_context() -> dict:
    return {"user": _user.get(), "run_id": _run_id.get(), "agent": _agent.get()}


@contextmanager
def context(**kwargs):
    previous = bind(**kwargs)
    try:
        yield
    finally:
        bind(**previous)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.user = _user.get()
        record.run_id = _run_id.get()
        record.agent = _agent.get()
        return True


class CloudRunFormatter(logging.Formatter):
    """One JSON object per line, shaped for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, "")
            if value:
                payload[field] = value
        for key, value in vars(record).items():
            if key not in _STANDARD and key not in CONTEXT_FIELDS:
                payload[key] = value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
            payload["stack_trace"] = payload["exception"]   # Error Reporting picks this up
        return json.dumps(payload, default=str)


class LocalFormatter(logging.Formatter):
    """Readable single line: time level [run_id agent] logger: message (k=v ...)"""

    def format(self, record: logging.LogRecord) -> str:
        tag = " ".join(v for v in (getattr(record, "run_id", ""), getattr(record, "agent", "")) if v)
        tag = f"[{tag}] " if tag else ""
        extras = {k: v for k, v in vars(record).items()
                  if k not in _STANDARD and k not in CONTEXT_FIELDS}
        suffix = (" " + " ".join(f"{k}={v}" for k, v in extras.items())) if extras else ""
        base = (f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {tag}"
                f"{record.name}: {record.getMessage()}{suffix}")
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure(level: str | None = None) -> None:
    """Install handlers. Safe to call more than once."""
    root = logging.getLogger()
    for handler in root.handlers[:]:          # replace uvicorn's default handlers
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudRunFormatter() if IN_CLOUD_RUN else LocalFormatter())
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    root.setLevel((level or os.environ.get("LOG_LEVEL", "INFO")).upper())

    # Third-party noise that would otherwise dominate the logs.
    for noisy in ("httpx", "httpcore", "google_genai.models", "urllib3",
                  "google.auth", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class Timer:
    """`with Timer() as t: ...` → t.ms holds the elapsed milliseconds."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self._start) * 1000)
        return False
