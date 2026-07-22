import torch
import soundfile as sf
import numpy as np
from transformers import Wav2Vec2FeatureExtractor, WavLMForXVector

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
extractor = Wav2Vec2FeatureExtractor.from_pretrained('microsoft/wavlm-base-sv')
model = WavLMForXVector.from_pretrained('microsoft/wavlm-base-sv').to(DEVICE)
model.eval()

def get_emb(path, start_s=0, duration_s=10):
    audio, sr = sf.read(path)
    if audio.ndim > 1: audio = audio.mean(axis=1)
    start_sample = int(start_s * sr)
    end_sample = int((start_s + duration_s) * sr)
    clip = audio[start_sample:end_sample]
    inputs = extractor(clip, sampling_rate=16000, return_tensors='pt')
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}
    with torch.no_grad():
        emb = model(**inputs).embeddings
    emb = torch.nn.functional.normalize(emb, dim=-1)
    return emb.squeeze(0).cpu().numpy()

print("--- ENROLLMENT EMBEDDINGS ---")
emb_dung_goc = get_emb("audio/thayDung_goc.wav", 0, 15)
emb_phuoc_goc = get_emb("audio/thayPhuoc_goc.wav", 0, 15)

sim_goc = np.dot(emb_dung_goc, emb_phuoc_goc)
print(f"Similarity [thayDung_goc] vs [thayPhuoc_goc]: {sim_goc:.4f}")

print("\n--- STREAM AUDIO COMPARISONS ---")
# Mic A (thayDung_noi.wav)
emb_micA_turn1 = get_emb("audio/thayDung_noi.wav", 0, 10)  # "Thì ở đây 1.2..."
emb_micA_turn2 = get_emb("audio/thayDung_noi.wav", 10, 15) # "Vấn đề gì hết..."

# Mic B (thayPhuoc_noi.wav)
emb_micB_turn1 = get_emb("audio/thayPhuoc_noi.wav", 0, 10)
emb_micB_turn2 = get_emb("audio/thayPhuoc_noi.wav", 10, 15)

print("\n[Mic_A turn 1 (0-10s)] vs thayDung_goc:", np.dot(emb_micA_turn1, emb_dung_goc))
print("[Mic_A turn 1 (0-10s)] vs thayPhuoc_goc:", np.dot(emb_micA_turn1, emb_phuoc_goc))

print("\n[Mic_B turn 2 (10-25s)] vs thayDung_goc:", np.dot(emb_micB_turn2, emb_dung_goc))
print("[Mic_B turn 2 (10-25s)] vs thayPhuoc_goc:", np.dot(emb_micB_turn2, emb_phuoc_goc))
