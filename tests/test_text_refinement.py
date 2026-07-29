from __future__ import annotations

import unittest

from backend.text_refinement import (
    PanphonFeatureScorer,
    ParallelPhoneticScorer,
    PhoneticLexicon,
    normalize_meeting_terms,
)


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


class PhoneticRecoveryTests(unittest.TestCase):
    def test_recovers_known_asr_aliases(self) -> None:
        lexicon = PhoneticLexicon.from_file(None)
        result = lexicon.recover("h pase chạy trên zip former")
        self.assertEqual(result.text, "HBase chạy trên Zipformer")
        self.assertEqual(len(result.replacements), 2)

    def test_recovery_is_conservative_for_unrelated_text(self) -> None:
        lexicon = PhoneticLexicon.from_file(None)
        result = lexicon.recover("hôm nay phòng họp bắt đầu đúng giờ")
        self.assertEqual(result.text, "hôm nay phòng họp bắt đầu đúng giờ")
        self.assertEqual(result.replacements, ())

    def test_dictionary_supports_pipe_and_tab_formats(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "terms.txt"
            path.write_text(
                "Meeting Minutes | meeting minits\n"
                "Sailor2\t sailor two\n",
                encoding="utf-8",
            )
            lexicon = PhoneticLexicon.from_file(path)
            result = lexicon.recover(
                "meeting minits được tạo bởi sailor two"
            )
            self.assertEqual(
                result.text,
                "Meeting Minutes được tạo bởi Sailor2",
            )

    def test_contextual_alias_does_not_rewrite_standalone_sos(self) -> None:
        from pathlib import Path

        dictionary = Path(__file__).parents[1] / "config" / "phonetic_dictionary.txt"
        lexicon = PhoneticLexicon.from_file(dictionary)
        contextual = lexicon.recover("lớp SOS gồm HBase")
        standalone = lexicon.recover("SOS đang được kích hoạt")
        self.assertIn("lớp Hadoop Storage", contextual.text)
        self.assertEqual(standalone.text, "SOS đang được kích hoạt")

    def test_g2p_score_can_promote_a_near_phonetic_candidate(self) -> None:
        class StubG2P:
            def phonemize(self, text: str) -> str:
                return {"alpaa": "a", "alfaa": "a", "Alphaa": "b"}.get(
                    text, ""
                )

        grapheme = PhoneticLexicon(
            (("Alphaa", ("alfaa",)),), threshold=0.86
        )
        g2p = PhoneticLexicon(
            (("Alphaa", ("alfaa",)),),
            threshold=0.86,
            phonemizer=StubG2P(),
            g2p_weight=0.65,
            g2p_prefilter=0.50,
        )
        self.assertEqual(grapheme.recover("alpaa").text, "alpaa")
        recovered = g2p.recover("alpaa")
        self.assertEqual(recovered.text, "Alphaa")
        self.assertEqual(recovered.replacements[0]["backend"], "g2p_onnx")

    def test_forced_g2p_returns_proposal_without_auto_apply(self) -> None:
        class StubG2P:
            def phonemize(self, text: str) -> str:
                return "same-sound" if text.casefold() in {"alfaa", "alphaa"} else ""

        lexicon = PhoneticLexicon(
            (("Alphaa", ("alfaa",)),),
            phonemizer=StubG2P(),
            g2p_force=True,
            auto_apply=False,
        )
        result = lexicon.recover("alfaa")
        self.assertEqual(result.text, "alfaa")
        self.assertEqual(result.replacements[0]["to"], "Alphaa")
        self.assertEqual(result.replacements[0]["backend"], "g2p_onnx")
        self.assertEqual(result.replacements[0]["start"], 0)


class ParallelPhoneticTests(unittest.TestCase):
    def test_combines_two_independent_pronunciation_views(self) -> None:
        class StubPhonemizer:
            def __init__(self, values: dict[str, str]) -> None:
                self.values = values

            def phonemize(self, text: str) -> str:
                return self.values[text]

        class StubFeatureScorer:
            def similarity(self, left: str, right: str) -> float:
                return {("e-observed", "e-candidate"): 0.90,
                        ("g-observed", "g-candidate"): 0.70}[(left, right)]

        scorer = ParallelPhoneticScorer(
            StubPhonemizer({"raw": "e-observed", "alias": "e-candidate"}),
            StubPhonemizer({"raw": "g-observed", "alias": "g-candidate"}),
            StubFeatureScorer(),  # type: ignore[arg-type]
            epitran_weight=0.25,
        )
        result = scorer.score("RAW", "ALIAS")
        self.assertAlmostEqual(result.score, 0.75)
        self.assertEqual(result.epitran_score, 0.90)
        self.assertEqual(result.g2p_score, 0.70)
        self.assertAlmostEqual(result.disagreement or 0.0, 0.20)


if __name__ == "__main__":
    unittest.main()
