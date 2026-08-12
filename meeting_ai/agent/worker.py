"""Bridge LiveKit microphone tracks into the existing AI pipeline."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import websockets
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

from meeting_ai.core.audio_pipeline import pack_audio_packet
from meeting_ai.config import settings


@dataclass
class AssignmentCursor:
    """Track the AI control-plane cursor across process restarts.

    ``assignment_generation`` is only monotonic within one AI Core process.
    The process-supplied epoch lets a long-lived Agent discard a stale cursor
    after that process restarts, without rejoining a static fallback room.
    """

    generation: int = 0
    epoch: str | None = None

    def observe(self, assignment: dict) -> bool:
        epoch = str(assignment.get("assignment_epoch") or "").strip() or None
        # A missing epoch after one was observed is treated as a control-plane
        # reset too; retaining the old generation could otherwise hide a new
        # assignment from a legacy/partially upgraded AI endpoint.
        epoch_changed = bool(self.epoch and epoch != self.epoch)
        if epoch != self.epoch:
            self.epoch = epoch
            self.generation = 0
        if assignment.get("status") != "IDLE":
            self.generation = int(
                assignment.get("assignment_generation") or self.generation
            )
        return epoch_changed


def assignment_requires_rejoin(previous: dict, current: dict) -> bool:
    """Return whether an Agent must leave the current room/assignment."""
    if str(current.get("status") or "") == "IDLE":
        return True
    previous_epoch = str(previous.get("assignment_epoch") or "").strip()
    current_epoch = str(current.get("assignment_epoch") or "").strip()
    if previous_epoch and current_epoch != previous_epoch:
        return True
    previous_runtime = str(previous.get("runtime_session_id") or "").strip()
    current_runtime = str(current.get("runtime_session_id") or "").strip()
    if previous_runtime and current_runtime and previous_runtime != current_runtime:
        return True
    previous_generation = int(previous.get("assignment_generation") or 0)
    current_generation = int(current.get("assignment_generation") or 0)
    return bool(
        previous_generation
        and current_generation
        and previous_generation != current_generation
    )


class EventPublisher:
    """Bridge AI results to the Meeting Service event envelope.

    The legacy eCabinet callback remains available when the worker is started
    without an assignment, so old local scripts keep working during rollout.
    """

    def __init__(self, assignment: dict | None = None) -> None:
        self.assignment = assignment or {}
        self.sequence = 0
        self.callback = dict(self.assignment.get("callback") or {})
        self.runtime_session_id = str(
            self.assignment.get("runtime_session_id") or ""
        )
        self.meeting_id = str(self.assignment.get("meeting_id") or "")
        self._spool: deque[dict] = deque()
        self._max_spool = 256
        self._max_attempts = 5
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        self._closing = False
        self.dropped = 0
        self._segment_revisions: dict[str, int] = {}

    @staticmethod
    def _iso_timestamp(value: object) -> str:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        text = str(value or "").strip()
        if text:
            return text
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _speaker(value: object, payload: dict) -> dict:
        if isinstance(value, dict):
            speaker = dict(value)
        else:
            speaker = {"label": str(value or "Unknown")}
        speaker.setdefault("label", "Unknown")
        speaker.setdefault("identity_method", payload.get("identity_method", "mic_fallback"))
        # ``speaker_id`` from the baseline is often a display label (for
        # example ``Dat``), not the external eCabinet UUID required by the
        # event contract. Only forward a UUID-shaped value as user_id.
        candidate_user_id = payload.get("speaker_user_id") or payload.get("user_id")
        if candidate_user_id and "user_id" not in speaker:
            try:
                speaker["user_id"] = str(uuid.UUID(str(candidate_user_id)))
            except (ValueError, AttributeError):
                pass
        if "user_id" in speaker:
            try:
                speaker["user_id"] = str(uuid.UUID(str(speaker["user_id"])))
            except (ValueError, AttributeError):
                speaker.pop("user_id", None)
        return speaker

    def _canonical_payload(self, event_type: str, payload: dict) -> dict:
        if event_type not in {
            "transcript.partial",
            "transcript.updated",
            "transcript.final",
            "transcript.retracted",
        }:
            return {key: value for key, value in payload.items() if key != "type"}
        canonical = {
            "segment_id": str(payload.get("segment_id") or f"seg-{uuid.uuid4().hex}"),
            "source_identity": str(payload.get("source_identity") or payload.get("source_id") or "unknown"),
            "speaker": self._speaker(payload.get("speaker"), payload),
            "content_text": str(payload.get("content_text") or payload.get("text") or ""),
            "revision": int(payload.get("revision") or 1),
        }
        if event_type == "transcript.retracted":
            canonical["reason"] = str(payload.get("reason") or "retracted by AI pipeline")
            return canonical
        if event_type in {"transcript.final", "transcript.updated"}:
            canonical.update(
                {
                    "raw_text": str(payload.get("raw_text") or payload.get("text") or ""),
                    "started_at": self._iso_timestamp(payload.get("started_at", payload.get("start_time"))),
                    "ended_at": self._iso_timestamp(payload.get("ended_at", payload.get("end_time"))),
                }
            )
        if payload.get("global_turn_id") is not None:
            canonical["global_turn_id"] = payload.get("global_turn_id")
        # Partial callbacks are intentionally small: they are realtime draft
        # evidence and must remain valid against transcript.partial's strict
        # contract (quality/pipeline metadata belongs to final/updated).
        if event_type == "transcript.partial":
            return canonical
        if isinstance(payload.get("quality"), dict):
            canonical["quality"] = dict(payload["quality"])
        else:
            canonical["quality"] = {
                key: payload[key]
                for key in ("signal_rms", "signal_snr_db", "clipping_ratio", "speaker_id_ms", "pipeline_ms")
                if payload.get(key) is not None
            }
        canonical["pipeline_meta"] = {
            key: payload[key]
            for key in (
                "final_asr_text",
                "final_turn_redecode",
                "phonetic_recovered_text",
                "phonetic_recovery_applied",
                "phonetic_replacements",
                "refinement",
                "refinement_ms",
                "refinement_pending",
                "discovered_topic",
            )
            if payload.get(key) is not None
        }
        return canonical

    async def start(self, client: httpx.AsyncClient) -> None:
        if self._worker_task is None:
            self._closing = False
            self._worker_task = asyncio.create_task(self._retry_worker(client))

    async def _send(self, client: httpx.AsyncClient, item: dict) -> None:
        callback_url = str(self.callback.get("url") or "").strip()
        if not callback_url or not self.runtime_session_id or not self.meeting_id:
            response = await client.post(
                settings.backend_internal_url,
                headers={"X-Internal-Api-Key": settings.internal_api_key},
                json={"payload": item["legacy_payload"]},
            )
            response.raise_for_status()
            return
        headers = {"X-Service-Key": settings.meeting_service_key}
        response = await client.post(
            callback_url,
            headers=headers,
            json=item["event"],
            timeout=5.0,
        )
        response.raise_for_status()

    def _enqueue(self, item: dict) -> None:
        if len(self._spool) >= self._max_spool:
            self._spool.popleft()
            self.dropped += 1
        self._spool.append(item)
        self._wake.set()

    async def _retry_worker(self, client: httpx.AsyncClient) -> None:
        while not self._closing or self._spool:
            if not self._spool:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                continue
            item = self._spool.popleft()
            attempts = int(item.get("attempts") or 0)
            try:
                await self._send(client, item)
            except Exception:
                attempts += 1
                if attempts < self._max_attempts:
                    item["attempts"] = attempts
                    await asyncio.sleep(min(2.0, 0.1 * (2 ** (attempts - 1))))
                    self._enqueue(item)
                else:
                    self.dropped += 1

    async def publish(self, client: httpx.AsyncClient, payload: dict) -> None:
        if self._worker_task is None:
            await self.start(client)
        event_type = (
            "transcript.updated"
            if payload.get("is_refinement_update")
            else str(payload.get("type") or "transcript.partial")
        )
        segment_id = str(payload.get("segment_id") or "")
        payload_for_event = dict(payload)
        if segment_id and event_type in {
            "transcript.partial",
            "transcript.updated",
            "transcript.final",
            "transcript.retracted",
        }:
            requested_revision = int(payload_for_event.get("revision") or 1)
            revision = max(
                requested_revision,
                self._segment_revisions.get(segment_id, 0) + 1,
            )
            payload_for_event["revision"] = revision
            self._segment_revisions[segment_id] = revision
        self.sequence += 1
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "type": event_type,
            "meeting_id": self.meeting_id,
            "runtime_session_id": self.runtime_session_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "sequence": self.sequence,
            "payload": self._canonical_payload(event_type, payload_for_event),
        }
        item = {"event": event, "legacy_payload": payload, "attempts": 0}
        try:
            await self._send(client, item)
        except Exception:
            self._enqueue(item)

    async def close(self, timeout: float = 5.0) -> None:
        self._closing = True
        self._wake.set()
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                await asyncio.gather(self._worker_task, return_exceptions=True)
            finally:
                self._worker_task = None


async def process_track(
    track: rtc.Track,
    participant: rtc.RemoteParticipant,
    client: httpx.AsyncClient,
    publisher: EventPublisher,
) -> None:
    identity = participant.identity
    display_name = participant.name or identity
    segment_id: str | None = None
    utterance_segments: dict[str, str] = {}
    audio_stream = rtc.AudioStream(
        track,
        sample_rate=16000,
        num_channels=1,
        frame_size_ms=settings.audio_frame_size_ms,
    )
    audio_sequence = 0
    query = urlencode({"display_name": display_name})
    runtime_id = publisher.runtime_session_id
    meeting_id = publisher.meeting_id
    if runtime_id:
        query = f"{query}&runtime_session_id={runtime_id}"
    if meeting_id:
        query = f"{query}&meeting_id={meeting_id}"
    uri = f"{settings.ai_server_ws_url}/ws/{identity}?{query}"

    print(f"[track] Đang xử lý mic: {display_name} ({identity})")
    try:
        async with websockets.connect(
            uri,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=settings.ai_server_ws_open_timeout_seconds,
        ) as websocket:

            async def receive_results() -> None:
                nonlocal segment_id
                async for message in websocket:
                    result = json.loads(message)
                    now = time.time()
                    utterance_id = result.get("utterance_id")
                    is_refinement_update = bool(
                        result.get("is_refinement_update")
                    )
                    if is_refinement_update:
                        resolved_segment_id = utterance_segments.get(
                            utterance_id
                        )
                        if not resolved_segment_id:
                            continue
                    else:
                        segment_id = (
                            segment_id or f"seg-{uuid.uuid4().hex}"
                        )
                        resolved_segment_id = segment_id
                    speaker = result.get("speaker")
                    if not speaker or speaker == identity:
                        speaker = display_name

                    if "partial" in result:
                        payload = {
                            "type": "transcript.partial",
                            "segment_id": resolved_segment_id,
                            "source_id": identity,
                            "speaker": speaker,
                            "identity_method": result.get(
                                "identity_method", "mic_fallback"
                            ),
                            "speaker_confidence": result.get(
                                "speaker_confidence"
                            ),
                            "text": result["partial"],
                            "timestamp": now,
                        }
                    else:
                        payload = {
                            "type": "transcript.final",
                            "segment_id": resolved_segment_id,
                            "source_id": identity,
                            "speaker": speaker,
                            "speaker_id": (
                                speaker
                                if result.get("identity_method")
                                == "voice_profile"
                                else None
                            ),
                            "identity_method": result.get(
                                "identity_method", "mic_fallback"
                            ),
                            "speaker_confidence": result.get(
                                "speaker_confidence"
                            ),
                            "speaker_margin": result.get("speaker_margin"),
                            "speaker_consensus": result.get(
                                "speaker_consensus"
                            ),
                            "speaker_id_ms": result.get("speaker_id_ms"),
                            "raw_text": result.get(
                                "raw_text", result.get("text", "")
                            ),
                            "final_asr_text": result.get("final_asr_text"),
                            "final_turn_redecode": result.get(
                                "final_turn_redecode"
                            ),
                            "phonetic_recovered_text": result.get(
                                "phonetic_recovered_text"
                            ),
                            "phonetic_recovery_applied": result.get(
                                "phonetic_recovery_applied", False
                            ),
                            "phonetic_replacements": result.get(
                                "phonetic_replacements", []
                            ),
                            # Closed Sailor decision (or Qwen baseline
                            # metadata) is persisted for A/B evaluation.
                            "refinement": result.get("refinement"),
                            "text": result.get("text", ""),
                            "start_time": result.get("start_time", now),
                            "end_time": result.get("end_time", now),
                            "refinement_ms": result.get("refinement_ms"),
                            "pipeline_ms": result.get("pipeline_ms"),
                            "signal_rms": result.get("signal_rms", 0),
                            "signal_snr_db": result.get(
                                "signal_snr_db"
                            ),
                            "clipping_ratio": result.get(
                                "clipping_ratio"
                            ),
                            "global_turn_id": result.get(
                                "global_turn_id"
                            ),
                            "discovered_topic": result.get(
                                "discovered_topic"
                            ),
                            "refinement_pending": result.get(
                                "refinement_pending", False
                            ),
                            "is_refinement_update": is_refinement_update,
                            "revision": result.get("revision", 1),
                            "timestamp": now,
                        }
                        if utterance_id:
                            utterance_segments[utterance_id] = (
                                resolved_segment_id
                            )
                        if not is_refinement_update:
                            segment_id = None

                    try:
                        await publisher.publish(client, payload)
                    except Exception as exc:
                        print(
                            f"[backend] Không gửi được transcript "
                            f"({type(exc).__name__}): {exc!r}"
                        )

            result_task = asyncio.create_task(receive_results())
            try:
                async for frame_event in audio_stream:
                    pcm = frame_event.frame.data.tobytes()
                    packet = pack_audio_packet(
                        pcm,
                        sequence=audio_sequence,
                        captured_at=time.monotonic(),
                    )
                    audio_sequence += 1
                    await websocket.send(packet)
            finally:
                # The AI service can still be finalizing the last utterance
                # when a participant mutes or leaves. Keep the result channel
                # alive briefly so that final transcript is not lost.
                print(
                    f"[track] {display_name}: forwarded "
                    f"{audio_sequence} frames "
                    f"({audio_sequence * settings.audio_frame_size_ms / 1000:.2f}s)"
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(result_task), timeout=20.0
                    )
                except asyncio.TimeoutError:
                    result_task.cancel()
                await asyncio.gather(result_task, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[track] Lỗi luồng {display_name}: {exc}")


async def main() -> None:
    settings.validate_livekit()
    room = rtc.Room()
    track_tasks: dict[str, asyncio.Task] = {}
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        if not stop_event.is_set():
            print("\n[worker] Đã nhận tín hiệu dừng.")
            stop_event.set()

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(shutdown_signal, request_shutdown)

    async with httpx.AsyncClient(timeout=10.0) as client:
        publisher = EventPublisher()
        await publisher.start(client)

        @room.on("track_subscribed")
        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            previous = track_tasks.pop(publication.sid, None)
            if previous:
                previous.cancel()
            track_tasks[publication.sid] = asyncio.create_task(
                process_track(track, participant, client, publisher)
            )

        @room.on("track_unsubscribed")
        def on_track_unsubscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            task = track_tasks.pop(publication.sid, None)
            if task:
                task.cancel()

        token = (
            AccessToken(
                settings.livekit_api_key, settings.livekit_api_secret
            )
            .with_identity("meeting-ai-worker")
            .with_name("AI Transcript")
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=settings.meeting_room,
                    can_subscribe=True,
                    can_publish=False,
                )
            )
            .to_jwt()
        )

        print(
            f"[worker] Kết nối {settings.livekit_url} / "
            f"{settings.meeting_room}"
        )
        await room.connect(settings.livekit_url, token)
        print("[worker] Sẵn sàng nhận các luồng microphone.")
        try:
            await stop_event.wait()
        finally:
            print("[worker] Đang dừng các luồng microphone...")
            for task in track_tasks.values():
                task.cancel()
            if track_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *track_tasks.values(), return_exceptions=True
                        ),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    print(
                        "[worker] Một số luồng không phản hồi; "
                        "tiếp tục ngắt LiveKit."
                    )
            try:
                await asyncio.wait_for(room.disconnect(), timeout=5.0)
            except asyncio.TimeoutError:
                print("[worker] LiveKit disconnect quá hạn; buộc kết thúc.")
            except Exception as exc:
                print(f"[worker] LiveKit đã đóng với cảnh báo: {exc}")
            print("[worker] Đã dừng hoàn toàn.")

            await publisher.close()


async def _poll_assignment(client: httpx.AsyncClient, after_generation: int) -> dict:
    response = await client.get(
        f"{settings.ai_server_http_url.rstrip('/')}/internal/v1/agent/assignment",
        params={"after_generation": after_generation},
        headers={"X-Service-Key": settings.internal_api_key},
    )
    response.raise_for_status()
    return response.json()


async def _post_agent_status(
    client: httpx.AsyncClient,
    assignment: dict,
    status: str,
    reason: str | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "assignment_generation": int(assignment.get("assignment_generation") or 1),
        "assignment_epoch": assignment.get("assignment_epoch"),
        "runtime_session_id": assignment.get("runtime_session_id"),
        "status": status,
        "reason": reason,
    }
    try:
        response = await client.post(
            f"{settings.ai_server_http_url.rstrip('/')}/internal/v1/agent/status",
            headers={"X-Service-Key": settings.internal_api_key},
            json=payload,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"[agent] Không cập nhật được trạng thái assignment: {exc}")


async def _run_assignment(
    assignment: dict,
    client: httpx.AsyncClient,
    shutdown_event: asyncio.Event,
) -> None:
    livekit = dict(assignment.get("livekit") or {})
    livekit_url = str(livekit.get("url") or settings.livekit_url)
    room_name = str(livekit.get("room") or settings.meeting_room)
    runtime_id = str(assignment.get("runtime_session_id") or "")
    publisher = EventPublisher(assignment if runtime_id else None)
    room = rtc.Room()
    room_stop = asyncio.Event()
    track_tasks: dict[str, asyncio.Task] = {}

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        print(
            f"[room] track_subscribed participant={participant.identity} "
            f"kind={track.kind} sid={publication.sid}"
        )
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        previous = track_tasks.pop(publication.sid, None)
        if previous:
            previous.cancel()
        track_tasks[publication.sid] = asyncio.create_task(
            process_track(track, participant, client, publisher)
        )

    @room.on("track_unsubscribed")
    def on_track_unsubscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        print(
            f"[room] track_unsubscribed participant={participant.identity} "
            f"sid={publication.sid}"
        )
        task = track_tasks.pop(publication.sid, None)
        if task:
            task.cancel()

    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity("meeting-ai-worker")
        .with_name("AI Transcript")
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_subscribe=True,
                can_publish=False,
            )
        )
        .to_jwt()
    )
    print(f"[worker] Kết nối {livekit_url} / {room_name}")
    await _post_agent_status(client, assignment, "CONNECTING")
    try:
        await publisher.start(client)
        await room.connect(livekit_url, token)
        await _post_agent_status(client, assignment, "READY")
        print("[worker] Sẵn sàng nhận các luồng microphone.")

        async def watch_assignment() -> None:
            if not runtime_id:
                return
            while not room_stop.is_set() and not shutdown_event.is_set():
                try:
                    current = await _poll_assignment(
                        # The assignment endpoint uses ``after_generation``
                        # as a long-poll cursor. A watcher already inside a
                        # room must ask for the current assignment (cursor
                        # zero), otherwise an unchanged READY generation is
                        # intentionally returned as IDLE and the Agent would
                        # disconnect immediately after joining.
                        client, 0
                    )
                    if assignment_requires_rejoin(assignment, current):
                        room_stop.set()
                        return
                except Exception as exc:
                    print(f"[agent] Assignment poll lỗi: {exc}")
                await asyncio.sleep(settings.agent_poll_seconds)

        watcher = asyncio.create_task(watch_assignment())
        shutdown_waiter = asyncio.create_task(shutdown_event.wait())
        room_waiter = asyncio.create_task(room_stop.wait())
        try:
            await asyncio.wait(
                [shutdown_waiter, room_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            shutdown_waiter.cancel()
            room_waiter.cancel()
            watcher.cancel()
            await asyncio.gather(
                shutdown_waiter, room_waiter, watcher, return_exceptions=True
            )
    except Exception as exc:
        await _post_agent_status(client, assignment, "FAILED", str(exc))
        print(f"[worker] LiveKit lỗi: {exc}")
    finally:
        print("[worker] Đang dừng các luồng microphone...")
        for task in track_tasks.values():
            task.cancel()
        if track_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*track_tasks.values(), return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                print("[worker] Một số luồng không phản hồi; tiếp tục ngắt LiveKit.")
        try:
            await asyncio.wait_for(room.disconnect(), timeout=5.0)
        except asyncio.TimeoutError:
            print("[worker] LiveKit disconnect quá hạn; buộc kết thúc.")
        except Exception as exc:
            print(f"[worker] LiveKit đóng với cảnh báo: {exc}")
        await publisher.close()
        await _post_agent_status(client, assignment, "STOPPED")
        print("[worker] Đã dừng assignment.")


async def main() -> None:
    settings.validate_agent_startup()
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        if not shutdown_event.is_set():
            print("\n[worker] Đã nhận tín hiệu dừng.")
            shutdown_event.set()

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(shutdown_signal, request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass

    async with httpx.AsyncClient(timeout=10.0) as client:
        cursor = AssignmentCursor()
        while not shutdown_event.is_set():
            if not settings.agent_assignment_enabled:
                await _run_assignment(
                    {"livekit": {"url": settings.livekit_url, "room": settings.meeting_room}},
                    client,
                    shutdown_event,
                )
                break
            try:
                assignment = await _poll_assignment(client, cursor.generation)
            except Exception as exc:
                print(f"[agent] Chưa lấy được assignment: {exc}")
                await asyncio.sleep(settings.agent_poll_seconds)
                continue
            epoch_changed = cursor.observe(assignment)
            if epoch_changed:
                print("[agent] AI control-plane restart detected; reset assignment cursor.")
            if assignment.get("status") == "IDLE":
                if settings.agent_static_room_fallback:
                    await _run_assignment(
                        {"livekit": {"url": settings.livekit_url, "room": settings.meeting_room}},
                        client,
                        shutdown_event,
                    )
                    continue
                await asyncio.sleep(settings.agent_poll_seconds)
                continue
            await _run_assignment(assignment, client, shutdown_event)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Fallback for platforms where asyncio cannot register signal handlers.
        pass
