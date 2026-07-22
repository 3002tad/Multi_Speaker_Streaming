import sherpa_onnx
import soundfile as sf
import numpy as np

asr_dir = 'Zipformer-30M-RNNT-Streaming-6000h'
recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f'{asr_dir}/config.json',
    encoder=f'{asr_dir}/encoder-epoch-31-avg-11-chunk-16-left-128.fp16.onnx',
    decoder=f'{asr_dir}/decoder-epoch-31-avg-11-chunk-16-left-128.fp16.onnx',
    joiner=f'{asr_dir}/joiner-epoch-31-avg-11-chunk-16-left-128.fp16.onnx',
    num_threads=2, sample_rate=16000, feature_dim=80,
    decoding_method='modified_beam_search', max_active_paths=4, provider='cpu'
)

def transcribe(path):
    audio, sr = sf.read(path)
    if audio.ndim > 1: audio = audio.mean(axis=1)
    stream = recognizer.create_stream()
    stream.accept_waveform(sr, audio)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    res = recognizer.get_result(stream)
    return res.text.strip() if hasattr(res, 'text') else str(res).strip()

print("--- TRANSCRIPTS OF ALL AUDIO FILES ---")
print("1. thayDung_goc.wav:", transcribe("audio/thayDung_goc.wav"))
print("2. thayPhuoc_goc.wav:", transcribe("audio/thayPhuoc_goc.wav"))
print("3. thayDung_noi.wav:", transcribe("audio/thayDung_noi.wav"))
print("4. thayPhuoc_noi.wav:", transcribe("audio/thayPhuoc_noi.wav"))
