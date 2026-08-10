"""Durable, bounded control-plane state for the LiveKit Agent assignment.

The Meeting AI process must not own meeting business data, but it does need a
small recovery record so an AI process restart does not silently orphan an
active runtime.  This store persists only the current assignment snapshot and
generation counter in the configured runtime directory.  It is intentionally
single-record and atomic; Meeting Service remains the source of truth for the
runtime lifecycle and transcript data.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class AssignmentStateStore:
    """Atomic JSON state for the one active demo assignment."""

    schema_version = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, dict) or value.get("schema_version") != self.schema_version:
            return None
        active = value.get("active")
        if not isinstance(active, dict):
            return None
        runtime_id = str(active.get("runtime_session_id") or "").strip()
        if not runtime_id:
            return None
        return {
            "schema_version": self.schema_version,
            "assignment_generation_counter": max(
                0, int(value.get("assignment_generation_counter") or 0)
            ),
            "active": dict(active),
        }

    def save(self, *, assignment_generation_counter: int, active: dict[str, Any]) -> None:
        runtime_id = str(active.get("runtime_session_id") or "").strip()
        if not runtime_id:
            raise ValueError("active assignment requires runtime_session_id")
        payload = {
            "schema_version": self.schema_version,
            "assignment_generation_counter": max(0, int(assignment_generation_counter)),
            "active": dict(active),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
