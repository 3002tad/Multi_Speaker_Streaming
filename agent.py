"""Bridge LiveKit microphone tracks into the existing AI pipeline."""

from __future__ import annotations

import asyncio
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
        epoch_changed = bool(self.epoch and epoch and epoch != self.epoch)
        if epoch and epoch != self.epoch:
            self.epoch = epoch
            self.generation = 0
        if assignment.get("status") != "IDLE":
            self.generation = int(
                assignment.get("assignment_generation") or self.generation
            )
        return epoch_changed


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

    async def publish(self, client: httpx.AsyncClient, payload: dict) -> None:
        callback_url = str(self.callback.get("url") or "").strip()
        if not callback_url or not self.runtime_session_id or not self.meeting_id:
            response = await client.post(
                settings.backend_internal_url,
                headers={"X-Internal-Api-Key": settings.internal_api_key},
                json={"payload": payload},
            )
            response.raise_for_status()
            return

        self.sequence += 1
        event_type = str(payload.get("type") or "transcript.partial")
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "type": event_type,
            "meeting_id": self.meeting_id,
            "runtime_session_id": self.runtime_session_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "sequence": self.sequence,
            "payload": {key: value for key, value in payload.items() if key != "type"},
        }
        headers = {"X-Service-Key": settings.meeting_service_key}
        response = await client.post(
            callback_url,
            headers=headers,
            json=event,
        )
        response.raise_for_status()


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
            uri, max_size=None, ping_interval=20, ping_timeout=20
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
                process_track(track, participant, client)
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
    assignment_epoch = str(assignment.get("assignment_epoch") or "").strip()
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
                    current_epoch = str(
                        current.get("assignment_epoch") or ""
                    ).strip()
                    if (
                        assignment_epoch
                        and current_epoch
                        and current_epoch != assignment_epoch
                    ):
                        # The AI process forgot this in-memory session. Leave
                        # the old room and let the main loop reset its cursor
                        # and fetch a fresh assignment.
                        room_stop.set()
                        return
                    if current.get("status") == "IDLE":
                        room_stop.set()
                        return
                    if str(current.get("runtime_session_id") or "") != runtime_id:
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
        await _post_agent_status(client, assignment, "STOPPED")
        print("[worker] Đã dừng assignment.")


async def main() -> None:
    settings.validate_livekit()
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
