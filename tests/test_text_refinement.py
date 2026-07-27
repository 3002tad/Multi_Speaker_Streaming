from __future__ import annotations

import unittest

from backend.text_refinement import normalize_meeting_terms


class MeetingTermNormalizationTests(unittest.TestCase):
    def test_corrects_known_technical_asr_errors(self) -> None:
        raw = (
            "một năm chấm hai lớp adolf sorus gồm có "
            "HD và hpase"
        )
        self.assertEqual(
            normalize_meeting_terms(raw),
            "mục 5.2 lớp Hadoop Storage gồm có HDFS và HBase",
        )

    def test_corrects_streaming_variants_without_global_acronym_rewrite(
        self,
    ) -> None:
        raw = (
            "làm quét và lớp ADOLF SORRIS gồm HDX và HPASE "
            "rồi H D là hệ thống tự tin phân tán"
        )
        self.assertEqual(
            normalize_meeting_terms(raw),
            (
                "làm web và lớp Hadoop Storage gồm HDFS và HBase "
                "HDFS là hệ thống tập tin phân tán"
            ),
        )

    def test_does_not_rewrite_unrelated_hd(self) -> None:
        self.assertEqual(
            normalize_meeting_terms("chất lượng HD rất tốt"),
            "chất lượng HD rất tốt",
        )


if __name__ == "__main__":
    unittest.main()
