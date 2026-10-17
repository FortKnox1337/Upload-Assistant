"""Structured tracker activity and results shared by the uploader and WebUI."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
from collections.abc import Iterator, Mapping
from urllib.parse import urlsplit

from rich.text import Text

from src.cogs.redaction import Redaction
from src.webui_progress import has_progress_callback, publish_progress

_retry_context: contextvars.ContextVar[_TrackerRetries | None] = contextvars.ContextVar("webui_tracker_retries", default=None)


class _TrackerRetries(logging.Handler):
    """Expose existing retry reports only within their tracker request context."""

    def __init__(self, tracker: str) -> None:
        super().__init__(logging.INFO)
        self.tracker = tracker
        self.last_error = ""

    def emit(self, record: logging.LogRecord) -> None:
        if _retry_context.get() is not self:
            return
        try:
            message = plain_detail(record.getMessage())
            message = re.sub(rf"^{re.escape(self.tracker)}:\s*", "", message, flags=re.IGNORECASE)
            if re.search(r"^retrying\b|\bretrying\s+(?:in|upload|request|with)\b|\bwill retry\b|\bretry in\b", message, re.IGNORECASE):
                detail = message
                if self.last_error and message.upper().strip(".! ") == "RETRYING UPLOAD":
                    detail = f"{self.last_error}\n{message}"
                publish_tracker(self.tracker, "Retrying…", detail)
            elif re.search(r"\b(?:error|invalid|failed|timeout|connection)\b|timed out", message, re.IGNORECASE):
                self.last_error = message
        except Exception:
            self.handleError(record)


@contextlib.contextmanager
def tracker_retry_context(logger: logging.Logger, tracker: str) -> Iterator[None]:
    """Keep retries isolated across concurrent trackers and propagated threads."""
    if not has_progress_callback():
        yield
        return
    handler = _TrackerRetries(tracker)
    token = _retry_context.set(handler)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        _retry_context.reset(token)
        handler.close()


def public_result_url(value: object) -> str:
    """Only expose complete public HTTP links, never credential-bearing URLs."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
    except ValueError:
        return ""
    if any(char.isspace() or ord(char) < 32 for char in value) or "[REDACTED]" in value:
        return ""
    if Redaction.redact_private_info(value) != value:
        return ""
    return value


def plain_detail(value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(Redaction.redact_private_info(value))
    text = Text.from_ansi(str(value or "")).plain
    with contextlib.suppress(Exception):
        text = Text.from_markup(text).plain
    for start, end in reversed(Redaction.extract_json_blocks(text)):
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(text[start:end])
            text = text[:start] + json.dumps(Redaction.redact_private_info(value)) + text[end:]
    return "\n".join(str(Redaction.redact_private_info(line)) for line in text.splitlines()).strip()


def tracker_result(tracker: str, status: Mapping[str, object], *, debug: bool = False) -> dict[str, str]:
    if status.get("upload_success") is True:
        outcome = "Debug completed" if debug else "Uploaded"
    elif status.get("upload_success") is False:
        outcome = "Failed"
    elif status.get("skipped") or status.get("upload") is False:
        outcome = "Skipped"
    else:
        outcome = "No upload result reported"
    detail = ""
    if outcome in {"Skipped", "Failed"}:
        detail = plain_detail(status.get("status_message"))
        if outcome == "Skipped":
            detail = str(status.get("skip_reason") or detail)
            if not detail:
                if status.get("redirected_to"):
                    detail = f"Redirected to {status['redirected_to']}"
                elif status.get("banned"):
                    detail = "Release group is banned"
                elif status.get("dupe"):
                    detail = "Duplicate found"
                else:
                    detail = "See Console for details"
    result = {"tracker": tracker, "outcome": outcome, "detail": plain_detail(detail)}
    if outcome == "Uploaded":
        url = public_result_url(status.get("uploaded_url")) or public_result_url(status.get("status_message"))
        if url:
            result["url"] = url
    return result


def publish_tracker(tracker: str, outcome: str, detail: str = "", url: str = "") -> None:
    publish_progress(f"tracker:{tracker}", tracker, status=outcome, detail=plain_detail(detail), group="tracker", url=public_result_url(url))


def publish_tracker_result(tracker: str, status: Mapping[str, object], *, debug: bool = False) -> None:
    result = tracker_result(tracker, status, debug=debug)
    publish_tracker(tracker, result["outcome"], result["detail"], result.get("url", ""))
