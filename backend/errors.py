"""Most recent error per source adapter, surfaced by `/api/health`.

Routers call `record_adapter_error` when they catch `SourceUnavailable`.
Only the error message and time are kept (no request data, no user data).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .dto import AdapterErrorInfo, Source

last_adapter_errors: dict[str, AdapterErrorInfo] = {}


def record_adapter_error(source: Source, message: str) -> None:
    last_adapter_errors[source] = AdapterErrorInfo(
        source=source,
        message=message,
        occurredAt=datetime.now(timezone.utc).isoformat(),
    )


def clear_adapter_error(source: Source) -> None:
    last_adapter_errors.pop(source, None)
