"""Bounded Meeting AI control-plane state.

This module intentionally persists only the single active assignment required
by the LiveKit worker after an AI-process restart. Meeting Service remains the
owner of durable runtime, transcript and minutes data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from uuid import uuid4

from meeting_ai.core.assignment_state import AssignmentStateStore


@dataclass
class AISession:
    runtime_session_id: str
    meeting_id: str
    assignment_generation: int
    payload: dict[str, object]
    status: str = "STARTING"
    reason: str | None = None
    agent_status: str = "STOPPED"
    participant_revision: int = 1
    idempotency_keys: set[str] = field(default_factory=set)


class SessionConflict(ValueError):
    """The requested session transition is incompatible with active state."""


class SessionManager:
    """Single-active-session control plane without Meeting Service persistence."""

    def __init__(self, state_store: AssignmentStateStore) -> None:
        self._state_store = state_store
        self._lock = RLock()
        self._sessions: dict[str, AISession] = {}
        self._active_runtime_id: str | None = None
        self._generation_counter = 0
        self._epoch = uuid4().hex
        self._agent_status: dict[str, object] = {
            "status": "STOPPED",
            "assignment_generation": 0,
            "runtime_session_id": None,
            "reason": None,
        }
        self._restore()

    @property
    def lock(self) -> RLock:
        return self._lock

    @property
    def epoch(self) -> str:
        return self._epoch

    def _state(self, session: AISession) -> dict[str, object]:
        return {
            "schema_version": 1,
            "runtime_session_id": session.runtime_session_id,
            "assignment_generation": session.assignment_generation,
            "assignment_epoch": self._epoch,
            "status": session.status,
            "reason": session.reason,
        }

    def _assignment(self, session: AISession, default_livekit_url: str) -> dict[str, object]:
        livekit = dict(session.payload.get("livekit") or {})
        livekit.setdefault("url", default_livekit_url)
        livekit.setdefault("room", f"meeting-{session.meeting_id}")
        return {
            **self._state(session),
            "meeting_id": session.meeting_id,
            "livekit": livekit,
            "participants": list(session.payload.get("participants") or []),
            "hotwords": list(session.payload.get("hotwords") or []),
            "callback": dict(session.payload.get("callback") or {}),
        }

    @staticmethod
    def _persist_payload(session: AISession) -> dict[str, object]:
        return {
            "runtime_session_id": session.runtime_session_id,
            "meeting_id": session.meeting_id,
            "assignment_generation": session.assignment_generation,
            "payload": dict(session.payload),
            "participant_revision": session.participant_revision,
        }

    def _persist_locked(self) -> None:
        session = self._sessions.get(self._active_runtime_id or "")
        if session is None or session.status in {"COMPLETED", "FAILED"}:
            self._state_store.clear()
            if session is not None:
                self._active_runtime_id = None
            return
        self._state_store.save(
            assignment_generation_counter=self._generation_counter,
            active=self._persist_payload(session),
        )

    def _restore(self) -> None:
        saved = self._state_store.load()
        if not saved:
            return
        active = saved.get("active") or {}
        runtime_id = str(active.get("runtime_session_id") or "").strip()
        meeting_id = str(active.get("meeting_id") or "").strip()
        payload = active.get("payload")
        if not runtime_id or not meeting_id or not isinstance(payload, dict):
            self._state_store.clear()
            return
        generation = max(1, int(active.get("assignment_generation") or 1))
        self._sessions[runtime_id] = AISession(
            runtime_session_id=runtime_id,
            meeting_id=meeting_id,
            assignment_generation=generation,
            payload=dict(payload),
            status="READY",
            participant_revision=max(1, int(active.get("participant_revision") or 1)),
        )
        self._active_runtime_id = runtime_id
        self._generation_counter = max(generation, int(saved.get("assignment_generation_counter") or 0))

    def is_active(self) -> bool:
        with self._lock:
            return self._active_runtime_id is not None

    def get(self, runtime_session_id: str) -> AISession | None:
        with self._lock:
            return self._sessions.get(runtime_session_id)

    def create(self, payload: dict[str, object], idempotency_key: str | None) -> tuple[dict[str, object], list[str]]:
        runtime_id = str(payload.get("runtime_session_id") or "").strip()
        meeting_id = str(payload.get("meeting_id") or "").strip()
        if not runtime_id or not meeting_id:
            raise SessionConflict("runtime_session_id and meeting_id are required")
        generation = int(payload.get("assignment_generation") or 1)
        if generation < 1:
            raise SessionConflict("assignment_generation must be positive")
        with self._lock:
            existing = self._sessions.get(runtime_id)
            if existing:
                if existing.status in {"COMPLETED", "FAILED"}:
                    self._generation_counter = max(self._generation_counter + 1, generation)
                    existing.payload = dict(payload)
                    existing.assignment_generation = self._generation_counter
                    existing.status, existing.reason, existing.agent_status = "READY", None, "STOPPED"
                    self._active_runtime_id = runtime_id
                elif idempotency_key:
                    existing.idempotency_keys.add(idempotency_key)
                    return self._state(existing), []
                else:
                    return self._state(existing), []
                self._persist_locked()
                return self._state(existing), self._participant_names(payload)
            active = self._sessions.get(self._active_runtime_id or "")
            if active and active.status not in {"COMPLETED", "FAILED"}:
                raise SessionConflict("another AI session is active")
            session = AISession(
                runtime_session_id=runtime_id,
                meeting_id=meeting_id,
                assignment_generation=max(self._generation_counter + 1, generation),
                payload=dict(payload),
                status="READY",
            )
            if idempotency_key:
                session.idempotency_keys.add(idempotency_key)
            self._generation_counter = session.assignment_generation
            self._sessions[runtime_id] = session
            self._active_runtime_id = runtime_id
            self._persist_locked()
            return self._state(session), self._participant_names(payload)

    @staticmethod
    def _participant_names(payload: dict[str, object]) -> list[str]:
        return [
            str(item.get("display_name"))
            for item in payload.get("participants", [])
            if isinstance(item, dict) and str(item.get("display_name") or "").strip()
        ]

    def state(self, runtime_session_id: str) -> dict[str, object] | None:
        with self._lock:
            session = self._sessions.get(runtime_session_id)
            return self._state(session) if session else None

    def stop(self, runtime_session_id: str, idempotency_key: str | None) -> dict[str, object] | None:
        with self._lock:
            session = self._sessions.get(runtime_session_id)
            if session is None:
                return None
            session.status, session.reason = "COMPLETED", "stopped_by_meeting_service"
            if idempotency_key:
                session.idempotency_keys.add(idempotency_key)
            if self._active_runtime_id == runtime_session_id:
                self._active_runtime_id = None
            self._persist_locked()
            return self._state(session)

    def update_participants(self, runtime_session_id: str, payload: dict[str, object]) -> tuple[dict[str, object] | None, list[str]]:
        with self._lock:
            session = self._sessions.get(runtime_session_id)
            if session is None:
                return None, []
            if session.status in {"COMPLETED", "FAILED"}:
                raise SessionConflict("AI session is no longer active")
            session.participant_revision = int(payload.get("snapshot_revision") or session.participant_revision + 1)
            session.payload["participants"] = list(payload.get("participants") or [])
            session.payload["hotwords"] = list(payload.get("hotwords") or [])
            self._persist_locked()
            return self._state(session), self._participant_names(payload)

    def assignment(self, after_generation: int, default_livekit_url: str) -> dict[str, object]:
        with self._lock:
            session = self._sessions.get(self._active_runtime_id or "")
            if session and session.status in {"READY", "RECORDING"} and session.assignment_generation > after_generation:
                return self._assignment(session, default_livekit_url)
            return {
                "assignment_epoch": self._epoch,
                "assignment_generation": max(after_generation, int(self._agent_status.get("assignment_generation") or 0)),
                "status": "IDLE",
            }

    def update_agent(self, payload: dict[str, object]) -> str:
        with self._lock:
            runtime_id = str(payload.get("runtime_session_id") or "")
            session = self._sessions.get(runtime_id) if runtime_id else None
            if runtime_id and session is None:
                return "stale"
            if session:
                if str(payload.get("assignment_epoch") or "").strip() != self._epoch:
                    return "stale"
                incoming_generation = int(payload.get("assignment_generation") or 0)
                if incoming_generation and incoming_generation != session.assignment_generation:
                    return "stale"
            self._agent_status = dict(payload)
            if session:
                session.agent_status = str(payload.get("status") or "STOPPED")
                if session.agent_status == "READY":
                    session.status = "RECORDING"
                elif session.agent_status == "FAILED":
                    session.status, session.reason = "FAILED", str(payload.get("reason") or "")
                self._persist_locked()
            return "accepted"

    def validate_websocket(self, runtime_session_id: str, meeting_id: str | None) -> bool:
        with self._lock:
            session = self._sessions.get(runtime_session_id)
            return bool(session and session.status in {"READY", "RECORDING"} and (not meeting_id or session.meeting_id == meeting_id))

    def mark_websocket_recording(self, runtime_session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(runtime_session_id)
            if session:
                session.status, session.agent_status = "RECORDING", "READY"
                self._persist_locked()
