"""Evaluate Zipformer against audio/truth.csv without starting the demo.

Examples:
    venv_linux/bin/python -B scripts/evaluate_asr.py
    venv_linux/bin/python -B scripts/evaluate_asr.py --mode light
    venv_linux/bin/python -B scripts/evaluate_asr.py --mode light --enhancer dpdfnet_baseline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from tempfile import TemporaryDirectory
from pathlib import Path
from collections.abc import Callable

import numpy as np
import sherpa_onnx
import soundfile as sf

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))

from backend.audio_pipeline import (
    AudioQualityTracker,
    DynamicEnhancementController,
    StreamingGuardedEnhancementFrontend,
    StreamingDpdfNetEnhancer,
    StreamingAsrPreprocessor,
)
from backend.adaptive_dictionary import (
    AdaptiveDictionary,
    GlossaryEntry,
    HotwordArtifacts,
    build_hotword_artifacts,
)
from backend.config import PROJECT_ROOT, settings
from backend.evaluation import (
    character_error_rate,
    load_transcript_truth,
    word_error_breakdown,
    word_error_rate,
)
from backend.final_turn import choose_redecode_transcript
from backend.text_refinement import (
    EpitranVietnamesePhonemizer,
    G2POnnxPhonemizer,
    PanphonFeatureScorer,
    PhoneticLexicon,
    SeaG2PVietnamesePhonemizer,
    TriplePhoneticScorer,
    normalize_meeting_terms,
)


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_600


def create_recognizer(
    *,
    max_active_paths: int,
    chunk_size: int,
    blank_penalty: float,
    hotword_artifacts: HotwordArtifacts | None = None,
) -> sherpa_onnx.OnlineRecognizer:
    model_dir = settings.zipformer_model_dir
    if chunk_size not in (16, 32, 64):
        raise ValueError("chunk_size must be one of 16, 32, 64")
    hotword_kwargs: dict[str, str | float] = {}
    if hotword_artifacts and hotword_artifacts.phrase_count:
        hotword_kwargs = {
            "modeling_unit": "bpe",
            "bpe_vocab": str(hotword_artifacts.bpe_vocab_path),
            "hotwords_file": str(hotword_artifacts.hotwords_path),
            "hotwords_score": settings.zipformer_hotwords_score,
        }
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(model_dir / "config.json"),
        encoder=str(
            model_dir
            / f"encoder-epoch-31-avg-11-chunk-{chunk_size}-left-128.fp16.onnx"
        ),
        decoder=str(
            model_dir
            / f"decoder-epoch-31-avg-11-chunk-{chunk_size}-left-128.fp16.onnx"
        ),
        joiner=str(
            model_dir
            / f"joiner-epoch-31-avg-11-chunk-{chunk_size}-left-128.fp16.onnx"
        ),
        num_threads=1,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="modified_beam_search",
        max_active_paths=max(1, max_active_paths),
        blank_penalty=float(blank_penalty),
        provider="cpu",
        **hotword_kwargs,
    )


def build_evaluation_hotwords(
    terms: tuple[str, ...],
    directory: Path,
) -> HotwordArtifacts | None:
    """Make a temporary canonical-only hotword set for controlled A/B tests."""
    if not terms:
        return None
    dictionary = AdaptiveDictionary(
        state_path=directory / "unused-state.json",
        dynamic_entries=tuple(
            GlossaryEntry(
                canonical=term,
                aliases=(),
                source="benchmark",
                confidence=1.0,
                last_seen="benchmark",
            )
            for term in terms
        ),
    )
    return build_hotword_artifacts(
        dictionary,
        model_dir=settings.zipformer_model_dir,
        hotwords_path=directory / "hotwords.txt",
        bpe_vocab_path=directory / "bpe.vocab",
        minimum_confidence=1.0,
    )


def build_phonetic_postprocessor() -> Callable[[str], str]:
    """Mirror the deterministic final-turn gate without importing ai_server."""
    dictionary = AdaptiveDictionary.from_paths(
        seed_path=settings.phonetic_dictionary_path,
        state_path=settings.adaptive_dictionary_state_path,
        manual_path=settings.adaptive_dictionary_manual_path,
    )
    g2p = None
    triple = None
    if settings.phonetic_backend == "g2p_onnx":
        try:
            g2p = G2POnnxPhonemizer(
                settings.phonetic_g2p_model_path,
                language_code=settings.phonetic_g2p_language,
                threads=settings.phonetic_g2p_threads,
            )
            triple = TriplePhoneticScorer(
                EpitranVietnamesePhonemizer(),
                g2p,
                SeaG2PVietnamesePhonemizer(),
                PanphonFeatureScorer(),
                consensus_tolerance=settings.phonetic_triple_consensus_tolerance,
            )
        except Exception as exc:
            print(f"[postprocess] Triple phonetic unavailable: {exc}")
    lexicon = PhoneticLexicon.from_file(
        settings.phonetic_dictionary_path,
        extra_entries=dictionary.dynamic_phonetic_entries(
            minimum_confidence=settings.adaptive_dictionary_phonetic_min_confidence
        ),
        threshold=settings.phonetic_recovery_threshold,
        margin=settings.phonetic_recovery_margin,
        max_words=settings.phonetic_recovery_max_words,
        phonemizer=g2p,
        g2p_weight=settings.phonetic_g2p_weight,
        g2p_prefilter=settings.phonetic_g2p_prefilter,
        g2p_max_calls=settings.phonetic_g2p_max_calls,
        g2p_force=settings.phonetic_g2p_force,
        triple_scorer=triple,
        triple_weight=settings.phonetic_triple_weight,
        triple_min_consensus=settings.phonetic_triple_min_consensus,
    )
    return lambda text: normalize_meeting_terms(lexicon.recover(text).text)


def load_audio(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        output_length = round(len(audio) * SAMPLE_RATE / sample_rate)
        old_axis = np.linspace(0.0, 1.0, len(audio), endpoint=False)
        new_axis = np.linspace(
            0.0, 1.0, output_length, endpoint=False
        )
        audio = np.interp(new_axis, old_axis, audio).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


def decode(
    recognizer: sherpa_onnx.OnlineRecognizer,
    audio: np.ndarray,
    *,
    frontend_name: str,
    denoiser_model_type: str,
    denoiser_model_path: Path,
    alignment_delay_ms: float,
    use_light_preprocessing: bool,
    enhancer_name: str,
    final_padding_seconds: float,
    defer_decode_until_final: bool = False,
) -> tuple[str, dict[str, object] | None]:
    stream = recognizer.create_stream()
    tracker = AudioQualityTracker()
    processor = StreamingAsrPreprocessor(
        high_pass_hz=settings.asr_high_pass_hz,
        target_rms=settings.asr_target_rms,
    )
    guarded_frontend = None
    enhancer = None
    if frontend_name == "dpdfnet":
        guarded_frontend = StreamingGuardedEnhancementFrontend(
            model_path=str(denoiser_model_path),
            num_threads=settings.asr_enhancer_threads,
            model_type=denoiser_model_type,
            alignment_delay_samples=round(alignment_delay_ms * 16),
            preservation_minimum_correlation=(
                settings.asr_preservation_min_correlation
            ),
            preservation_minimum_energy_ratio=(
                settings.asr_preservation_min_energy_ratio
            ),
            preservation_maximum_energy_ratio=(
                settings.asr_preservation_max_energy_ratio
            ),
            preservation_minimum_speech_band_ratio=(
                settings.asr_preservation_min_speech_band_ratio
            ),
            preservation_maximum_speech_mix=(
                settings.asr_preservation_max_speech_mix
            ),
            preservation_maximum_noise_mix=(
                settings.asr_preservation_max_noise_mix
            ),
            preservation_crossfade_samples=round(
                settings.asr_preservation_crossfade_ms * 16
            ),
            dc_block_hz=settings.asr_dpdfnet_post_dc_hz,
            target_rms=settings.asr_dpdfnet_post_target_rms,
            minimum_gain=settings.asr_dpdfnet_post_min_gain,
            maximum_gain=settings.asr_dpdfnet_post_max_gain,
            attenuation_rate=(
                settings.asr_dpdfnet_post_attenuation_rate
            ),
            boost_rate=settings.asr_dpdfnet_post_boost_rate,
            activity_floor=settings.asr_dpdfnet_post_activity_floor,
            peak_limit=settings.asr_dpdfnet_post_peak_limit,
        )
    elif enhancer_name == "dpdfnet_baseline":
        enhancer = StreamingDpdfNetEnhancer(
            model_path=str(denoiser_model_path),
            num_threads=settings.asr_enhancer_threads,
            model_type=denoiser_model_type,
            alignment_delay_samples=round(alignment_delay_ms * 16),
            controller=DynamicEnhancementController(
                bypass_snr_db=settings.asr_enhancer_bypass_snr_db,
                full_snr_db=settings.asr_enhancer_full_snr_db,
                maximum_mix=settings.asr_enhancer_max_mix,
                attack=settings.asr_enhancer_attack,
                release=settings.asr_enhancer_release,
            ),
        )
    for start in range(0, len(audio), FRAME_SAMPLES):
        frame = audio[start : start + FRAME_SAMPLES]
        if len(frame) < FRAME_SAMPLES:
            frame = np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
        rms = float(np.sqrt(np.mean(np.square(frame))))
        speech_active = rms >= max(0.008, tracker.noise_floor * 2.5)
        measured = tracker.measure(
            frame, speech_active=speech_active
        )
        if guarded_frontend is not None:
            frame = guarded_frontend.process(frame, quality=measured)
        else:
            if use_light_preprocessing:
                frame = processor.process(frame, quality=measured)
            if enhancer is not None:
                frame = enhancer.process(frame, quality=measured)
        if frame.size:
            stream.accept_waveform(SAMPLE_RATE, frame)
        if not defer_decode_until_final:
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
    enhancement = None
    if guarded_frontend is not None:
        tail = guarded_frontend.flush()
        if tail.size:
            stream.accept_waveform(SAMPLE_RATE, tail)
            if not defer_decode_until_final:
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
        post, gate = guarded_frontend.telemetry()
        enhancement = {
            "frontend": "guarded",
            "model_type": denoiser_model_type,
            "alignment_delay_ms": alignment_delay_ms,
            "speech_frames": gate.evaluated_speech_frames,
            "accepted_speech_frames": gate.accepted_speech_frames,
            "fallback_speech_frames": gate.fallback_speech_frames,
            "noise_frames": gate.noise_frames,
            "average_mix": round(gate.average_mix, 4),
            "peak_mix": round(gate.peak_mix, 4),
            "average_correlation": round(
                gate.average_correlation, 4
            ),
            "average_energy_ratio": round(
                gate.average_energy_ratio, 4
            ),
            "average_speech_band_ratio": round(
                gate.average_speech_band_ratio, 4
            ),
            "average_gain": round(post.average_gain, 4),
            "minimum_gain": round(post.minimum_gain, 4),
            "maximum_gain": round(post.maximum_gain, 4),
            "peak_limited_frames": post.peak_limited_frames,
        }
    elif enhancer is not None:
        tail = enhancer.flush()
        if tail.size:
            stream.accept_waveform(SAMPLE_RATE, tail)
            if not defer_decode_until_final:
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
        telemetry = enhancer.telemetry()
        enhancement = {
            "average_mix": round(telemetry.average_mix, 4),
            "peak_mix": round(telemetry.peak_mix, 4),
        }
    padding = np.zeros(
        max(0, int(round(final_padding_seconds * SAMPLE_RATE))),
        dtype=np.float32,
    )
    if padding.size:
        stream.accept_waveform(SAMPLE_RATE, padding)
        if not defer_decode_until_final:
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result(stream)
    text = (
        result.text.strip()
        if hasattr(result, "text")
        else str(result).strip()
    )
    return text, enhancement


def evaluate(
    mode: str,
    *,
    frontend_name: str,
    denoiser_model_type: str,
    denoiser_model_path: Path,
    alignment_delay_ms: float,
    enhancer_name: str,
    max_active_paths: int,
    chunk_size: int,
    final_padding_seconds: float,
    blank_penalty: float,
    hotwords: tuple[str, ...] = (),
    postprocess: str = "none",
    final_turn_redecode: bool = False,
    truth_path: Path = PROJECT_ROOT / "audio" / "truth.csv",
) -> dict:
    truth_rows = load_transcript_truth(truth_path)
    # sherpa reads the BPE vocabulary and hotword phrases when the recognizer
    # is built, so the temporary files can be discarded before decoding.
    with TemporaryDirectory(prefix="zipformer-hotwords-") as temporary:
        artifacts = build_evaluation_hotwords(hotwords, Path(temporary))
        recognizer = create_recognizer(
            max_active_paths=max_active_paths,
            chunk_size=chunk_size,
            blank_penalty=blank_penalty,
            hotword_artifacts=artifacts,
        )
    postprocessor = (
        build_phonetic_postprocessor()
        if postprocess == "phonetic"
        else lambda text: text
    )
    rows = []
    started = time.perf_counter()
    total_audio_seconds = 0.0
    for truth in truth_rows:
        path = PROJECT_ROOT / "audio" / f"{truth.voice}.wav"
        audio = load_audio(path)
        if truth.start_seconds is not None:
            start = int(truth.start_seconds * SAMPLE_RATE)
            end = (
                int(truth.end_seconds * SAMPLE_RATE)
                if truth.end_seconds is not None
                else len(audio)
            )
            audio = audio[start:end]
        total_audio_seconds += len(audio) / SAMPLE_RATE
        item_started = time.perf_counter()
        raw_hypothesis, enhancement = decode(
            recognizer,
            audio,
            frontend_name=frontend_name,
            denoiser_model_type=denoiser_model_type,
            denoiser_model_path=denoiser_model_path,
            alignment_delay_ms=alignment_delay_ms,
            use_light_preprocessing=mode == "light",
            enhancer_name=enhancer_name,
            final_padding_seconds=final_padding_seconds,
        )
        redecode = None
        selected_raw = raw_hypothesis
        if final_turn_redecode:
            replayed, _ = decode(
                recognizer,
                audio,
                frontend_name=frontend_name,
                denoiser_model_type=denoiser_model_type,
                denoiser_model_path=denoiser_model_path,
                alignment_delay_ms=alignment_delay_ms,
                use_light_preprocessing=mode == "light",
                enhancer_name=enhancer_name,
                final_padding_seconds=(
                    final_padding_seconds
                    + settings.asr_final_turn_redecode_tail_padding_seconds
                ),
                defer_decode_until_final=True,
            )
            decision = choose_redecode_transcript(
                raw_hypothesis,
                replayed,
                minimum_overlap=settings.asr_final_turn_redecode_min_overlap,
                maximum_word_ratio=settings.asr_final_turn_redecode_max_word_ratio,
            )
            selected_raw = decision.text
            redecode = {
                "candidate_text": replayed,
                "selected": decision.selected_redecode,
                "decision": decision.reason,
                "overlap": round(decision.overlap, 4),
                "word_ratio": round(decision.word_ratio, 4),
            }
        hypothesis = postprocessor(selected_raw)
        elapsed = time.perf_counter() - item_started
        item = {
            "voice": truth.voice,
            "reference": truth.transcript,
            "hypothesis": hypothesis,
            "wer": round(
                word_error_rate(truth.transcript, hypothesis), 4
            ),
            "cer": round(
                character_error_rate(truth.transcript, hypothesis), 4
            ),
            "audio_seconds": round(len(audio) / SAMPLE_RATE, 3),
            "processing_seconds": round(elapsed, 3),
        }
        breakdown = word_error_breakdown(truth.transcript, hypothesis)
        item["deletions"] = breakdown["deletions"]
        item["insertions"] = breakdown["insertions"]
        item["substitutions"] = breakdown["substitutions"]
        if hypothesis != raw_hypothesis:
            item["raw_hypothesis"] = raw_hypothesis
        if redecode is not None:
            item["final_turn_redecode"] = redecode
        if enhancement is not None:
            item["enhancement"] = enhancement
        rows.append(item)
    elapsed = time.perf_counter() - started
    return {
        "mode": mode,
        "frontend": frontend_name,
        "denoiser_model_type": denoiser_model_type,
        "denoiser_model_path": str(denoiser_model_path),
        "alignment_delay_ms": alignment_delay_ms,
        "enhancer": enhancer_name,
        "max_active_paths": max_active_paths,
        "chunk_size": chunk_size,
        "final_padding_seconds": final_padding_seconds,
        "blank_penalty": blank_penalty,
        "hotwords": list(hotwords),
        "postprocess": postprocess,
        "final_turn_redecode": final_turn_redecode,
        "mean_wer": round(
            float(np.mean([item["wer"] for item in rows])), 4
        ),
        "mean_cer": round(
            float(np.mean([item["cer"] for item in rows])), 4
        ),
        "realtime_factor": round(
            elapsed / max(total_audio_seconds, 1e-6), 4
        ),
        "items": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("raw", "light", "both"),
        default="both",
    )
    parser.add_argument(
        "--frontend",
        choices=("legacy", "dpdfnet"),
        default="legacy",
        help=(
            "legacy = optional light DSP/enhancer; dpdfnet = raw plus "
            "a guarded DPDFNet/GTCRN candidate and post-conditioning"
        ),
    )
    parser.add_argument(
        "--denoiser-model-type",
        choices=("dpdfnet", "gtcrn"),
        default=settings.asr_enhancer_model_type,
        help="Online denoiser architecture used by the guarded frontend.",
    )
    parser.add_argument(
        "--denoiser-model",
        type=Path,
        default=settings.asr_enhancer_model,
        help="Path to the DPDFNet or GTCRN ONNX model.",
    )
    parser.add_argument(
        "--alignment-delay-ms",
        type=float,
        default=settings.asr_enhancer_alignment_delay_ms,
        help="Measured waveform delay compensated before preservation gating.",
    )
    parser.add_argument(
        "--enhancer",
        choices=("none", "dpdfnet_baseline"),
        default="none",
        help="Optional ASR-only neural enhancer for A/B comparison.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the JSON report",
    )
    parser.add_argument(
        "--max-active-paths",
        type=int,
        default=settings.zipformer_max_active_paths,
        help="Modified-beam width (default from ZIPFORMER_MAX_ACTIVE_PATHS).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        choices=(16, 32, 64),
        default=settings.zipformer_chunk_size,
        help="Streaming Zipformer chunk variant (default from ZIPFORMER_CHUNK_SIZE).",
    )
    parser.add_argument(
        "--final-padding-seconds",
        type=float,
        default=settings.asr_final_padding_seconds,
        help="Trailing silence fed before finalizing each stream.",
    )
    parser.add_argument(
        "--blank-penalty",
        type=float,
        default=settings.zipformer_blank_penalty,
        help="Penalty subtracted from RNNT blank logits.",
    )
    parser.add_argument(
        "--hotwords",
        default="",
        help="Comma-separated canonical phrases for a temporary decoder A/B test.",
    )
    parser.add_argument(
        "--postprocess",
        choices=("none", "phonetic"),
        default="none",
        help="Apply the production deterministic final-turn phonetic gate.",
    )
    parser.add_argument(
        "--final-turn-redecode",
        action="store_true",
        help=(
            "A/B test a deferred full-turn replay and the production "
            "conservative selection gate."
        ),
    )
    parser.add_argument(
        "--truth",
        type=Path,
        default=PROJECT_ROOT / "audio" / "truth.csv",
        help="CSV transcript truth file (default: audio/truth.csv).",
    )
    args = parser.parse_args()
    if args.frontend == "dpdfnet" and args.enhancer != "none":
        parser.error(
            "--frontend dpdfnet already owns the neural stage; "
            "use --enhancer none"
        )
    hotwords = tuple(
        dict.fromkeys(
            item.strip() for item in args.hotwords.split(",") if item.strip()
        )
    )
    modes = (
        ("dpdfnet",)
        if args.frontend == "dpdfnet"
        else (
            ("raw", "light")
            if args.mode == "both"
            else (args.mode,)
        )
    )
    report = {
        "results": [
            evaluate(
                mode,
                frontend_name=args.frontend,
                denoiser_model_type=args.denoiser_model_type,
                denoiser_model_path=args.denoiser_model,
                alignment_delay_ms=max(0.0, args.alignment_delay_ms),
                enhancer_name=args.enhancer,
                max_active_paths=max(1, args.max_active_paths),
                chunk_size=args.chunk_size,
                final_padding_seconds=max(0.0, args.final_padding_seconds),
                blank_penalty=args.blank_penalty,
                hotwords=hotwords,
                postprocess=args.postprocess,
                final_turn_redecode=args.final_turn_redecode,
                truth_path=args.truth,
            )
            for mode in modes
        ]
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
