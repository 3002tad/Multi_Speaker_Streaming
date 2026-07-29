from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel, Field

from backend.api.database import TranscriptRepository
from backend.config import PROJECT_ROOT, settings


app = FastAPI(title="Paperless Meeting Demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

repository = TranscriptRepository(settings.database_path)


class WebSocketHub:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self.locks: dict[WebSocket, asyncio.Lock] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        self.locks[websocket] = asyncio.Lock()

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)
        self.locks.pop(websocket, None)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        stale: list[WebSocket] = []
        for connection in list(self.connections):
            try:
                lock = self.locks.get(connection)
                if lock is None:
                    continue
                async with lock:
                    await connection.send_text(encoded)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


hub = WebSocketHub()
final_event_lock = asyncio.Lock()
meeting_reset_lock = asyncio.Lock()
current_meeting_title = ""


async def _reset_meeting_transcripts(reason: str) -> float:
    async with meeting_reset_lock:
        reset_at = time.time()
        repository.clear(settings.meeting_room)
        await hub.broadcast(
            {
                "type": "transcript.cleared",
                "reason": reason,
                "reset_at": reset_at,
            }
        )
        return reset_at


def _word_similarity(left: str, right: str) -> float:
    normalize = lambda value: set(
        re.findall(r"\w+", value.lower(), flags=re.UNICODE)
    )
    left_words = normalize(left)
    right_words = normalize(right)
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / min(
        len(left_words), len(right_words)
    )


def _overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = float(left.get("start_time", 0))
    left_end = float(left.get("end_time", 0))
    right_start = float(right.get("start_time", 0))
    right_end = float(right.get("end_time", 0))
    return min(left_end, right_end) - max(left_start, right_start) >= 1.0


def _find_cross_mic_duplicate(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    raw_text = payload.get("raw_text") or payload.get("text", "")
    for existing in reversed(
        repository.list_for_meeting(settings.meeting_room)
    ):
        if existing.get("source_id") == payload.get("source_id"):
            continue
        payload_turn = payload.get("global_turn_id")
        existing_turn = existing.get("global_turn_id")
        if (
            payload_turn
            and existing_turn
            and payload_turn != existing_turn
        ):
            # Coordinated VAD has already established that these are
            # different room-wide turns; do not collapse overlapping speech.
            continue
        if not _overlaps(existing, payload):
            continue
        existing_raw = existing.get("raw_text") or existing.get("text", "")
        if _word_similarity(raw_text, existing_raw) >= 0.62:
            return existing
    return None


class CreateMeetingRequest(BaseModel):
    host_name: str = Field(min_length=1, max_length=80)
    meeting_title: str | None = Field(default=None, max_length=180)


class JoinMeetingRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    meeting_code: str = Field(min_length=1, max_length=32)


class InternalEventRequest(BaseModel):
    payload: dict[str, Any]


async def _prepare_adaptive_dictionary(
    meeting_title: str | None,
) -> dict[str, Any]:
    """Ask the private AI service to prepare the next room's glossary.

    Creating/joining a meeting must remain available if the optional AI
    preparation endpoint is temporarily restarting. The result is returned to
    the host UI for diagnostics rather than blocking access to the room.
    """
    title = " ".join((meeting_title or "").split())
    if not title:
        return {"status": "skipped", "reason": "no_meeting_title"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.ai_server_http_url}/api/adaptive-dictionary/prepare",
                json={"meeting_title": title},
                headers={"X-Internal-Key": settings.internal_api_key},
            )
            response.raise_for_status()
            return {"status": "ready", **response.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "unavailable",
            "reason": type(exc).__name__,
        }


def _issue_token(display_name: str, role: str) -> tuple[str, str]:
    try:
        settings.validate_livekit()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    identity = f"user-{uuid.uuid4().hex[:12]}"
    metadata = json.dumps(
        {"display_name": display_name, "role": role}, ensure_ascii=False
    )
    grants = VideoGrants(
        room_join=True,
        room=settings.meeting_room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_metadata(metadata)
        .with_grants(grants)
        .to_jwt()
    )
    return identity, token


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "meeting-backend",
        "meeting_room": settings.meeting_room,
        "livekit_configured": settings.livekit_configured,
    }


@app.get("/api/meeting")
async def meeting_info() -> dict[str, Any]:
    return {
        "room": settings.meeting_room,
        "code": settings.meeting_code,
        "title": current_meeting_title,
        "livekit_url": settings.livekit_url,
    }


@app.post("/api/meeting/create")
async def create_meeting(request: CreateMeetingRequest) -> dict[str, Any]:
    global current_meeting_title
    identity, token = _issue_token(request.host_name, "host")
    current_meeting_title = " ".join((request.meeting_title or "").split())
    dictionary = await _prepare_adaptive_dictionary(current_meeting_title)
    reset_at = await _reset_meeting_transcripts("new_meeting")
    return {
        "status": "success",
        "meeting_code": settings.meeting_code,
        "room": settings.meeting_room,
        "livekit_url": settings.livekit_url,
        "identity": identity,
        "display_name": request.host_name,
        "role": "host",
        "token": token,
        "reset_at": reset_at,
        "meeting_title": current_meeting_title,
        "adaptive_dictionary": dictionary,
    }


@app.post("/api/meeting/join")
async def join_meeting(request: JoinMeetingRequest) -> dict[str, Any]:
    if request.meeting_code.strip().upper() != settings.meeting_code.upper():
        raise HTTPException(status_code=404, detail="Mã phòng không đúng")
    identity, token = _issue_token(request.display_name, "participant")
    return {
        "status": "success",
        "meeting_code": settings.meeting_code,
        "room": settings.meeting_room,
        "livekit_url": settings.livekit_url,
        "identity": identity,
        "display_name": request.display_name,
        "role": "participant",
        "token": token,
        "meeting_title": current_meeting_title,
    }


@app.get("/api/transcripts")
async def list_transcripts() -> dict[str, Any]:
    return {
        "meeting_id": settings.meeting_room,
        "items": repository.list_for_meeting(settings.meeting_room),
    }


@app.delete("/api/transcripts")
async def clear_transcripts() -> dict[str, str]:
    await _reset_meeting_transcripts("manual_clear")
    return {"status": "success"}


@app.post("/api/enrollment")
async def enroll_speaker(
    speaker_name: str = Form(..., min_length=1, max_length=80),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Proxy enrollment audio to the isolated AI process on port 8001."""
    audio = await file.read()
    if len(audio) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File audio quá lớn")

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{settings.ai_server_http_url}/enroll",
                data={"speaker_name": speaker_name},
                files={
                    "file": (
                        file.filename or "enrollment.wav",
                        audio,
                        file.content_type or "audio/wav",
                    )
                },
            )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"AI enrollment chưa sẵn sàng: {exc}",
        ) from exc

    if result.get("status") != "success":
        raise HTTPException(
            status_code=422,
            detail=result.get("message", "Không thể tạo mẫu giọng nói"),
        )
    return result


@app.post("/api/internal/events")
async def publish_internal_event(
    request: InternalEventRequest,
    x_internal_api_key: str | None = Header(default=None),
) -> dict[str, str]:
    if x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = dict(request.payload)
    payload.setdefault("meeting_id", settings.meeting_room)
    payload.setdefault("timestamp", time.time())
    if payload.get("type") == "transcript.final":
        async with final_event_lock:
            payload.setdefault("segment_id", f"seg-{uuid.uuid4().hex}")
            payload.setdefault("created_at", time.time())
            if int(payload.get("revision", 1)) > 1:
                repository.upsert(payload)
                await hub.broadcast(payload)
                return {"status": "updated"}
            duplicate = _find_cross_mic_duplicate(payload)
            if duplicate:
                new_rms = float(payload.get("signal_rms", 0))
                old_rms = float(duplicate.get("signal_rms", 0))
                if new_rms <= old_rms:
                    await hub.broadcast(
                        {
                            "type": "transcript.retracted",
                            "segment_id": payload["segment_id"],
                            "reason": "cross_mic_leakage",
                        }
                    )
                    return {"status": "deduplicated"}

                repository.delete(
                    settings.meeting_room, duplicate["segment_id"]
                )
                await hub.broadcast(
                    {
                        "type": "transcript.retracted",
                        "segment_id": duplicate["segment_id"],
                        "reason": "cross_mic_leakage",
                    }
                )
            repository.upsert(payload)
            await hub.broadcast(payload)
            return {"status": "accepted"}

    await hub.broadcast(payload)
    return {"status": "accepted"}


@app.websocket("/ws/meeting")
async def meeting_websocket(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    await websocket.send_json(
        {
            "type": "connection.ready",
            "meeting_id": settings.meeting_room,
            "timestamp": time.time(),
        }
    )
    try:
        while True:
            # Browser heartbeat keeps Nginx and intermediate connections alive.
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)


# Local demo convenience. On the home server Nginx can still serve this folder
# directly, while WSL can expose the exact same UI from port 8000.
app.mount(
    "/",
    StaticFiles(directory=PROJECT_ROOT / "frontend", html=True),
    name="frontend",
)
