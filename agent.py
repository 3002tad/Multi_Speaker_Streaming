import os
import asyncio
import json
import websockets
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

os.environ["LIVEKIT_URL"] = "ws://127.0.0.1:7880"
os.environ["LIVEKIT_API_KEY"] = "devkey"
os.environ["LIVEKIT_API_SECRET"] = "secret"

async def process_track(track: rtc.Track, identity: str):
    print(f"\n[+] Đang kết nối luồng AI qua WebSocket cho: {identity}")
    
    # Ép AudioStream xuất ra đúng 16kHz, 1 kênh để gửi cho AI Server
    audio_stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
    uri = f"ws://127.0.0.1:8000/ws/{identity}"
    
    try:
        async with websockets.connect(uri) as ws:
            
            last_speaker = None
            last_text = None
            async def receive_from_server():
                nonlocal last_speaker, last_text
                try:
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if "partial" in data:
                            identity_label = data.get("identity", identity)
                            partial_text = data["partial"]
                            if last_speaker != identity_label:
                                last_speaker = identity_label
                                last_text = partial_text
                                disp = partial_text[-75:] if len(partial_text) > 75 else partial_text
                                print(f"\n[💬 Nháp] [{identity_label}]: ...{disp:<75}", end="", flush=True)
                            elif last_text != partial_text:
                                last_text = partial_text
                                disp = partial_text[-75:] if len(partial_text) > 75 else partial_text
                                print(f"\r[💬 Nháp] [{identity_label}]: ...{disp:<75}", end="", flush=True)
                        else:
                            last_speaker = None
                            last_text = None
                            print(f"\n\n[📦 BIÊN BẢN CHÍNH THỨC]: {json.dumps(data, ensure_ascii=False)}\n")
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    print(f"\n[!] Lỗi nhận dữ liệu từ AI Server: {e}")
            
            # Chạy song song tác vụ nhận
            recv_task = asyncio.create_task(receive_from_server())
            
            # Vòng lặp chính: Đọc Audio từ LiveKit và ném sang AI Server
            async for frame_event in audio_stream:
                try:
                    await ws.send(frame_event.frame.data.tobytes())
                except websockets.exceptions.ConnectionClosed:
                    break
            
            recv_task.cancel()
    except Exception as e:
        print(f"\n[!] Không thể kết nối tới AI Server tại cổng 8000. Lỗi: {e}")

async def main():
    room = rtc.Room()

    @room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(process_track(track, participant.identity))

    @room.on("track_unsubscribed")
    def on_track_unsubscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        print(f"\n[-] Tiêu hủy luồng AI của {participant.identity} (Mic đã tắt)")

    print("Đang kết nối vào phòng họp (meeting_room)...")
    grant = VideoGrants(room_join=True, room="meeting_room")
    token = AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]).with_identity("AI_Agent").with_grants(grant).to_jwt()
    await room.connect("ws://127.0.0.1:7880", token)
    print("\n[🚀 Agent] Đã kết nối vào phòng họp thành công và đang trực chiến!")
    
    # Giữ cho tiến trình sống mãi
    try:
        await asyncio.Event().wait()
    except asyncio.exceptions.CancelledError:
        pass
    finally:
        await room.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
