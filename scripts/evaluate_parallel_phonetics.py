"""Benchmark Epitran + PanPhon + ByT5 G2P evidence without starting the demo.

The candidate column is a dictionary alias, not its canonical replacement.
This mirrors the recovery stage: phonetic matching chooses an alias first,
then the alias maps to the known meeting term.

Example (WSL):
    venv_linux/bin/python -B scripts/evaluate_parallel_phonetics.py \
      --json-out tmp/parallel_phonetics.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))

from backend.config import settings
from backend.text_refinement import (
    EpitranVietnamesePhonemizer,
    G2POnnxPhonemizer,
    PanphonFeatureScorer,
    ParallelPhoneticScorer,
)


# Positive rows reflect aliases already present in config/phonetic_dictionary.txt.
# Negative rows are deliberately plausible Vietnamese speech, but must not be
# elevated to an unrelated technical dictionary entry.
CASES = (
    # Exact aliases are controls: both engines should converge at 1.0.
    ("LÀM QUÉT", "làm quét", "làm web", True),
    # These are deliberately non-identical spellings of the same aliases.
    ("LÀM QUYẾT", "làm quét", "làm web", True),
    ("H PA SÊ", "h pase", "HBase", True),
    ("HÊ ĐÊ ÉP ÉT", "h d f s", "HDFS", True),
    ("LỚP A PO LÍT", "lớp apolis", "lớp Hadoop Storage", True),
    ("HỌP LÚC CHÍN GIỜ", "h pase", "HBase", False),
    ("HỆ THỐNG TỰ TIN", "h pase", "HBase", False),
    ("LỚP SOS", "h d s", "HDFS", False),
    ("HBASE", "hadoop storage", "Hadoop Storage", False),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-path",
        type=Path,
        default=settings.phonetic_g2p_model_path,
        help="directory of klebster/g2p_multilingual_byT5_tiny_onnx",
    )
    parser.add_argument(
        "--threads", type=int, default=settings.phonetic_g2p_threads
    )
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    g2p = G2POnnxPhonemizer(
        args.model_path,
        language_code=settings.phonetic_g2p_language,
        threads=args.threads,
    )
    scorer = ParallelPhoneticScorer(
        EpitranVietnamesePhonemizer(),
        g2p,
        PanphonFeatureScorer(),
    )

    rows: list[dict[str, object]] = []
    for observed, alias, canonical, expected_match in CASES:
        result = scorer.score(observed, alias)
        row = {
            "observed": observed,
            "alias": alias,
            "canonical": canonical,
            "expected_match": expected_match,
            **asdict(result),
        }
        rows.append(row)
        print(
            f"{observed!r} -> {canonical} (expected={expected_match}): "
            f"combined={result.score:.3f} "
            f"epitran={result.epitran_score!s} g2p={result.g2p_score!s} "
            f"delta={result.disagreement!s}"
        )

    report = {
        "model_path": str(args.model_path),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "rows": rows,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Saved {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
