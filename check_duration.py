import soundfile as sf
import sys
try:
    print("thayDung_noi.wav duration:", sf.info("audio/thayDung_noi.wav").duration)
    print("thayPhuoc_noi.wav duration:", sf.info("audio/thayPhuoc_noi.wav").duration)
except Exception as e:
    print(e)
