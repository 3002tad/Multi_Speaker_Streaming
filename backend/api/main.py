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
from backend.minutes_composer import (
    MinutesCompositionError,
    OllamaMinutesComposer,
    empty_minutes_document,
    normalize_minutes_document,
)


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
meeting_started_at: float | None = None
minutes_epoch = 0
minutes_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
minutes_worker_task: asyncio.Task[None] | None = None


def _minutes_response() -> dict[str, Any]:
    stored = repository.get_minutes(settings.meeting_room)
    if stored is not None:
        return stored
    return {
        "meeting_id": settings.meeting_room,
        "version": 0,
        "status": "idle",
        "updated_at": None,
        "metadata": {},
        "document": empty_minutes_document(
            current_meeting_title, started_at=meeting_started_at
        ),
    }


async def _compose_minutes_once(epoch: int) -> None:
    """Compose only from persisted final segments, never partial ASR text."""
    if epoch != minutes_epoch:
        return
    segments = repository.list_for_meeting(settings.meeting_room)
    if not segments:
        return
    existing = repository.get_minutes(settings.meeting_room)
    processed_ids = set(
        (existing or {}).get("document", {}).get("source_segment_ids", [])
    )
    pending_segments = [
        segment
        for segment in segments
        if segment.get("segment_id") not in processed_ids
    ]
    composer = OllamaMinutesComposer(settings)
    if not pending_segments and not composer.uses_transcript_timeline:
        return
    # The deterministic fallback is a complete current view, not an
    # incremental LLM patch.  Rebuild it from every final segment so one new
    # event can also replace an older low-quality generated document.
    composition_segments = (
        segments if composer.uses_transcript_timeline else pending_segments
    )
    try:
        document, metadata = await composer.compose(
            meeting_title=current_meeting_title,
            existing_document=(existing or {}).get("document"),
            segments=composition_segments,
            started_at=meeting_started_at,
        )
    except MinutesCompositionError as exc:
        if epoch == minutes_epoch:
            await hub.broadcast(
                {
                    "type": "minutes.status",
                    "status": "error",
                    "message": str(exc),
                    "timestamp": time.time(),
                }
            )
        return
    except Exception as exc:  # keep an ASR event from crashing the worker
        if epoch == minutes_epoch:
            await hub.broadcast(
                {
                    "type": "minutes.status",
                    "status": "error",
                    "message": f"Minutes composer failed: {type(exc).__name__}",
                    "timestamp": time.time(),
                }
            )
        return
    if epoch != minutes_epoch:
        return
    stored = repository.upsert_minutes(
        settings.meeting_room,
        document=document,
        status="ready",
        metadata=metadata,
        updated_at=time.time(),
    )
    await hub.broadcast({"type": "minutes.updated", **stored})


async def _minutes_worker() -> None:
    """Serialize Qwen calls and coalesce bursts of final-turn events."""
    while True:
        first_job = await minutes_queue.get()
        jobs = [first_job]
        try:
            await asyncio.sleep(
                max(0.0, settings.minutes_composer_debounce_seconds)
            )
            while True:
                try:
                    jobs.append(minutes_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            latest_epoch, _ = jobs[-1]
            await _compose_minutes_once(latest_epoch)
        except asyncio.CancelledError:
            raise
        finally:
            for _ in jobs:
                minutes_queue.task_done()


async def _schedule_minutes_composition(reason: str) -> None:
    """Queue one eventual update; concurrent final turns are coalesced."""
    global minutes_worker_task
    if not settings.minutes_composer_enabled:
        return
    if minutes_worker_task is None or minutes_worker_task.done():
        minutes_worker_task = asyncio.create_task(_minutes_worker())
    await minutes_queue.put((minutes_epoch, reason))
    await hub.broadcast(
        {
            "type": "minutes.status",
            "status": "queued",
            "reason": reason,
            "timestamp": time.time(),
        }
    )


async def _reset_meeting_transcripts(reason: str) -> float:
    global meeting_started_at, minutes_epoch
    async with meeting_reset_lock:
        reset_at = time.time()
        minutes_epoch += 1
        meeting_started_at = reset_at
        repository.clear(settings.meeting_room)
        repository.clear_minutes(settings.meeting_room)
        await hub.broadcast(
            {
                "type": "transcript.cleared",
                "reason": reason,
                "reset_at": reset_at,
            }
        )
        await hub.broadcast(
            {
                "type": "minutes.cleared",
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


class JoinMeetingRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    meeting_code: str = Field(min_length=1, max_length=32)


class InternalEventRequest(BaseModel):
    payload: dict[str, Any]


class UpdateMinutesRequest(BaseModel):
    document: dict[str, Any]


async def _reset_adaptive_dictionary(
    participant_names: list[str],
) -> dict[str, Any]:
    """Reset generated terms and register the first room participants.

    Creating/joining a meeting must remain available if the optional AI
    dictionary endpoint is temporarily restarting. The result is returned to
    the host UI for diagnostics rather than blocking access to the room.
    """
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.post(
                f"{settings.ai_server_http_url}/api/adaptive-dictionary/reset",
                json={"participant_names": participant_names},
                headers={"X-Internal-Key": settings.internal_api_key},
            )
            response.raise_for_status()
            return {"status": "ready", **response.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "unavailable",
            "reason": type(exc).__name__,
        }


async def _register_dictionary_participant(
    display_name: str,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(
                (
                    f"{settings.ai_server_http_url}"
                    "/api/adaptive-dictionary/participants"
                ),
                json={"display_name": display_name},
                headers={"X-Internal-Key": settings.internal_api_key},
            )
            response.raise_for_status()
            return {"status": "ready", **response.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}


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
        "minutes_composer": {
            "enabled": settings.minutes_composer_enabled,
            "model": settings.minutes_composer_model,
            "mode": settings.minutes_composer_mode,
            "think": False,
        },
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
    current_meeting_title = ""
    dictionary = await _reset_adaptive_dictionary([request.host_name])
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
        "adaptive_dictionary": dictionary,
    }


@app.post("/api/meeting/join")
async def join_meeting(request: JoinMeetingRequest) -> dict[str, Any]:
    if request.meeting_code.strip().upper() != settings.meeting_code.upper():
        raise HTTPException(status_code=404, detail="Mã phòng không đúng")
    identity, token = _issue_token(request.display_name, "participant")
    dictionary = await _register_dictionary_participant(
        request.display_name
    )
    return {
        "status": "success",
        "meeting_code": settings.meeting_code,
        "room": settings.meeting_room,
        "livekit_url": settings.livekit_url,
        "identity": identity,
        "display_name": request.display_name,
        "role": "participant",
        "token": token,
        "adaptive_dictionary": dictionary,
    }


@app.get("/api/transcripts")
async def list_transcripts() -> dict[str, Any]:
    return {
        "meeting_id": settings.meeting_room,
        "items": repository.list_for_meeting(settings.meeting_room),
    }


@app.get("/api/minutes")
async def get_minutes() -> dict[str, Any]:
    return _minutes_response()


@app.post("/api/minutes/refresh")
async def refresh_minutes() -> dict[str, Any]:
    if not repository.list_for_meeting(settings.meeting_room):
        return {"status": "skipped", "reason": "no_transcript"}
    await _schedule_minutes_composition("manual_refresh")
    return {"status": "queued"}


@app.put("/api/minutes")
async def update_minutes(request: UpdateMinutesRequest) -> dict[str, Any]:
    """Persist a human correction without asking Qwen to rewrite it again."""
    segments = repository.list_for_meeting(settings.meeting_room)
    document = normalize_minutes_document(
        request.document,
        meeting_title=current_meeting_title,
        valid_source_ids=[
            str(item.get("segment_id"))
            for item in segments
            if item.get("segment_id")
        ],
        started_at=meeting_started_at,
    )
    stored = repository.upsert_minutes(
        settings.meeting_room,
        document=document,
        status="manual",
        metadata={"editor": "manual", "think": False},
        updated_at=time.time(),
    )
    await hub.broadcast({"type": "minutes.updated", **stored})
    return stored


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
    global current_meeting_title
    if x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = dict(request.payload)
    payload.setdefault("meeting_id", settings.meeting_room)
    payload.setdefault("timestamp", time.time())
    if payload.get("type") == "transcript.final":
        async with final_event_lock:
            discovered_topic = payload.get("discovered_topic")
            if isinstance(discovered_topic, dict):
                topic = " ".join(
                    str(discovered_topic.get("topic", "")).split()
                )[:180]
                try:
                    topic_confidence = float(
                        discovered_topic.get("confidence", 0.0)
                    )
                except (TypeError, ValueError):
                    topic_confidence = 0.0
                if topic and topic_confidence >= 0.65:
                    current_meeting_title = topic
                    await hub.broadcast(
                        {
                            "type": "meeting.topic",
                            "topic": topic,
                            "confidence": topic_confidence,
                            "snapshot_version": discovered_topic.get(
                                "snapshot_version"
                            ),
                            "timestamp": time.time(),
                        }
                    )
            payload.setdefault("segment_id", f"seg-{uuid.uuid4().hex}")
            payload.setdefault("created_at", time.time())
            if int(payload.get("revision", 1)) > 1:
                repository.upsert(payload)
                await hub.broadcast(payload)
                await _schedule_minutes_composition("transcript_revision")
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
            await _schedule_minutes_composition("final_turn")
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


@app.on_event("shutdown")
async def stop_minutes_worker() -> None:
    global minutes_worker_task
    if minutes_worker_task is None:
        return
    minutes_worker_task.cancel()
    try:
        await minutes_worker_task
    except asyncio.CancelledError:
        pass
    minutes_worker_task = None


# Local demo convenience. On the home server Nginx can still serve this folder
# directly, while WSL can expose the exact same UI from port 8000.
app.mount(
    "/",
    StaticFiles(directory=PROJECT_ROOT / "frontend", html=True),
    name="frontend",
)
