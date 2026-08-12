"""Bounded dependency probes for Meeting Service readiness."""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from sqlalchemy import text


def _component(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


def _probe_database(session_factory: Any) -> dict[str, Any]:
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return _component("ok")
    except Exception as exc:
        return _component("failed", error=type(exc).__name__)


def _probe_redis(redis_url: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        parsed = urlsplit(redis_url)
        host = parsed.hostname or ""
        port = parsed.port or 6379
        if not host:
            raise ValueError("missing redis host")
        with socket.create_connection((host, port), timeout=timeout_seconds) as conn:
            conn.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = conn.recv(64)
        if not response.startswith(b"+PONG"):
            raise RuntimeError("unexpected redis ping response")
        return _component("ok")
    except Exception as exc:
        return _component("failed", error=type(exc).__name__)


def _probe_storage(storage: Any) -> dict[str, Any]:
    try:
        check = getattr(storage, "readiness_check", None)
        if check is None:
            # The filesystem adapter is a valid local/offline implementation.
            return _component("ok", mode="filesystem")
        if not check():
            raise RuntimeError("bucket unavailable")
        return _component("ok", mode="minio")
    except Exception as exc:
        return _component("failed", error=type(exc).__name__)


def _probe_ai(base_url: str, service_key: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        request = Request(
            f"{base_url.rstrip('/')}/health/ready",
            headers={"X-Service-Key": service_key},
        )
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: internal configured URL
            payload = json.loads(response.read().decode("utf-8"))
        status = str(payload.get("status") or "failed")
        if status not in {"ok", "degraded"}:
            return _component("failed", error=f"ai:{status}")
        return _component(status, ollama=(payload.get("ollama") or {}).get("status"))
    except Exception as exc:
        return _component("failed", error=type(exc).__name__)


def collect_readiness(
    *,
    persistence_enabled: bool,
    session_factory: Any,
    redis_url: str,
    object_storage: Any,
    ai_enabled: bool,
    ai_base_url: str,
    service_key: str,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    """Return an explicit operational report without raising from /health."""
    components: dict[str, dict[str, Any]] = {}
    if persistence_enabled:
        components["database"] = _probe_database(session_factory)
        components["redis"] = _probe_redis(redis_url, timeout_seconds)
        components["object_storage"] = _probe_storage(object_storage)
    else:
        components.update(
            {
                "database": _component("skipped"),
                "redis": _component("skipped"),
                "object_storage": _component("ok", mode="filesystem"),
            }
        )
    components["meeting_ai"] = (
        _probe_ai(ai_base_url, service_key, timeout_seconds)
        if ai_enabled
        else _component("skipped")
    )
    failed = [name for name, item in components.items() if item["status"] == "failed"]
    return {
        "status": "ok" if not failed else "degraded",
        "components": components,
        "failed_components": failed,
    }
