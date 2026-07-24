"""Bridge LiveKit microphone tracks into the existing AI pipeline."""

from __future__ import annotations

import asyncio
import json
import signal
import time
import uuid
from urllib.parse import urlencode

import httpx
import websockets
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

from backend.config import settings


async def publish_event(client: httpx.AsyncClient, payload: dict) -> None:
    response = await client.post(
        settings.backend_internal_url,
        headers={"X-Internal-Api-Key": settings.internal_api_key},
        json={"payload": payload},
    )
    response.raise_for_status()


async def process_track(
    track: rtc.Track,
    participant: rtc.RemoteParticipant,
    client: httpx.AsyncClient,
) -> None:
    identity = participant.identity
    display_name = participant.name or identity
    segment_id: str | None = None
    utterance_segments: dict[str, str] = {}
    audio_stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
    query = urlencode({"display_name": display_name})
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
                            "raw_text": result.get(
                                "raw_text", result.get("text", "")
                            ),
                            "text": result.get("text", ""),
                            "start_time": result.get("start_time", now),
                            "end_time": result.get("end_time", now),
                            "refinement_ms": result.get("refinement_ms"),
                            "pipeline_ms": result.get("pipeline_ms"),
                            "signal_rms": result.get("signal_rms", 0),
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
                        await publish_event(client, payload)
                    except Exception as exc:
                        print(f"[backend] Không gửi được transcript: {exc}")

            result_task = asyncio.create_task(receive_results())
            try:
                async for frame_event in audio_stream:
                    await websocket.send(frame_event.frame.data.tobytes())
            finally:
                # The AI service can still be finalizing the last utterance
                # when a participant mutes or leaves. Keep the result channel
                # alive briefly so that final transcript is not lost.
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Fallback for platforms where asyncio cannot register signal handlers.
        pass
