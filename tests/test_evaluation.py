from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.evaluation import (
    character_error_rate,
    load_transcript_truth,
    word_error_rate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TranscriptTruthTests(unittest.TestCase):
    def test_truth_rows_have_matching_audio_files(self) -> None:
        rows = load_transcript_truth(PROJECT_ROOT / "audio" / "truth.csv")
        self.assertGreaterEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(
                (PROJECT_ROOT / "audio" / f"{row.voice}.wav").is_file(),
                row.voice,
            )

    def test_wer_and_cer_are_normalized(self) -> None:
        reference = "Xin chào, cuộc họp!"
        self.assertEqual(word_error_rate(reference, reference.lower()), 0.0)
        self.assertAlmostEqual(
            word_error_rate(reference, "xin chào"), 2 / 4
        )
        self.assertEqual(character_error_rate(reference, reference), 0.0)
        self.assertGreater(character_error_rate(reference, "xin chào"), 0)

    def test_optional_time_ranges_are_loaded(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "truth.csv"
            path.write_text(
                "voice,transcript,start_seconds,end_seconds\n"
                "sample,hello,1.5,8\n",
                encoding="utf-8",
            )
            row = load_transcript_truth(path)[0]
        self.assertEqual(row.start_seconds, 1.5)
        self.assertEqual(row.end_seconds, 8.0)


if __name__ == "__main__":
    unittest.main()
