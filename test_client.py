import asyncio
import soundfile as sf
import numpy as np
import requests
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

URL = "ws://127.0.0.1:7880"
API_KEY = "devkey"
API_SECRET = "secret"
AI_SERVER_ENROLL_URL = "http://127.0.0.1:8000/enroll"
SILENCE_PAD_SECONDS = 2  # Khoảng lặng đệm trước/sau audio để VAD hoạt động đúng

def enroll_speaker_file(speaker_name, wav_path):
    print(f"\n[Ghi danh Client] Đang gửi file giọng mẫu ({speaker_name}: {wav_path}) lên AI Server...")
    try:
        with open(wav_path, "rb") as f:
            res = requests.post(
                AI_SERVER_ENROLL_URL,
                data={"speaker_name": speaker_name},
                files={"file": (wav_path, f, "audio/wav")}
            )
        res.raise_for_status()  # Ném exception nếu AI Server trả lỗi HTTP
        print(f" -> Kết quả từ AI Server: {res.json()}")
    except Exception as e:
        print(f" -> [!] Lỗi gửi đăng ký vân tay: {e}")

async def simulate_client(identity, wav_path, delay=0):
    await asyncio.sleep(delay)
    grant = VideoGrants(room_join=True, room="meeting_room")
    token = AccessToken(API_KEY, API_SECRET).with_identity(identity).with_grants(grant).to_jwt()
    
    room = rtc.Room()
    await room.connect(URL, token)
    
    source = rtc.AudioSource(16000, 1)
    track = rtc.LocalAudioTrack.create_audio_track(f"track_{identity}", source)
    options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await room.local_participant.publish_track(track, options)
    
    audio, sr = sf.read(wav_path)
    if audio.ndim > 1: audio = audio.mean(axis=1)
    
    chunk_size = 320
    print(f"\n[{identity}] Đã vào phòng. Chuẩn bị stream Audio...")
    
    silence = np.zeros(16000 * SILENCE_PAD_SECONDS, dtype=np.float32)
    audio = np.concatenate([silence, audio, silence])

    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i+chunk_size]
        if len(chunk) < chunk_size: break
        pcm16 = (chunk * 32768).astype(np.int16)
        frame = rtc.AudioFrame(data=pcm16.tobytes(), sample_rate=16000, num_channels=1, samples_per_channel=chunk_size)
        await source.capture_frame(frame)
        await asyncio.sleep(0.02)
        
    print(f"\n[{identity}] Đã hoàn tất Stream Audio.")
    return room

async def main():
    print("\n==================================================")
    print("=== BƯỚC 2: MÔ PHỎNG 2 MICRO TRONG PHÒNG HỌP ===")
    print("==================================================")
    room_a, room_b = await asyncio.gather(
        simulate_client("Mic_A", "audio/thayDung_noi.wav", delay=0),
        simulate_client("Mic_B", "audio/thayPhuoc_noi.wav", delay=0)
    )
    # Giữ phòng mở cho đến khi cả 2 Mic hoàn tất xuất Biên bản chính thức
    await asyncio.sleep(4.0)
    print("\n[Hệ thống] Hoàn tất phiên họp, rời phòng cho cả 2 Mic...")
    await room_a.disconnect()
    await room_b.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
