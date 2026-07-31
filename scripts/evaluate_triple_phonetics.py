"""Exercise the final-turn three-phoneme gate with real Vietnamese models.

This is not an ASR benchmark. It evaluates the conservative post-ASR gate on
known positive aliases and a deliberately distant negative (`nhân viên` vs
`VNPT`) produced by the Zipformer test recording.

Example:
    venv_linux/bin/python -B scripts/evaluate_triple_phonetics.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.adaptive_dictionary import AdaptiveDictionary
from backend.config import settings
from backend.text_refinement import (
    EpitranVietnamesePhonemizer,
    G2POnnxPhonemizer,
    PanphonFeatureScorer,
    PhoneticLexicon,
    SeaG2PVietnamesePhonemizer,
    TriplePhoneticScorer,
)


CASES = (
    "H PASE chạy trên Zipformer",
    "lớp adolf sorus đang triển khai",
    "lớp adolf sorris đang triển khai",
    "VÊ EN PÊ TÊ có các giải pháp để hỗ trợ",
    "NHÂN VIÊN có các giải pháp để hỗ trợ",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manual-dictionary",
        type=Path,
        default=PROJECT_ROOT / "config" / "meeting_lexicon.example.txt",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    started = time.perf_counter()
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
    dictionary = AdaptiveDictionary.from_paths(
        seed_path=None,
        state_path=PROJECT_ROOT / "tmp" / "unused-adaptive-state.json",
        manual_path=args.manual_dictionary,
    )
    dynamic_entries = dictionary.active_entries()
    lexicon = PhoneticLexicon.from_file(
        settings.phonetic_dictionary_path,
        extra_entries=(
            (entry.canonical, entry.aliases) for entry in dynamic_entries
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
    initialized_seconds = time.perf_counter() - started

    rows = []
    for text in CASES:
        item_started = time.perf_counter()
        result = lexicon.recover(text)
        rows.append(
            {
                "raw": text,
                "recovered": result.text,
                "replacements": list(result.replacements),
                "elapsed_ms": round((time.perf_counter() - item_started) * 1000, 1),
            }
        )
    report = {
        "manual_dictionary": str(args.manual_dictionary),
        "dynamic_terms": [entry.to_json() for entry in dynamic_entries],
        "initialization_seconds": round(initialized_seconds, 3),
        "cases": rows,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
