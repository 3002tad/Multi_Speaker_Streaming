from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.adaptive_dictionary import AdaptiveDictionary, GlossaryEntry


class AdaptiveDictionaryTests(unittest.TestCase):
    def test_merges_seed_and_active_dynamic_terms(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed.txt"
            seed.write_text("HDFS | h d f s\n", encoding="utf-8")
            state = root / "dictionary.json"
            dictionary = AdaptiveDictionary.from_paths(
                seed_path=seed, state_path=state
            )
            dictionary.save_dynamic_entries(
                (
                    GlossaryEntry(
                        canonical="VNPT",
                        aliases=("v n p t", "vê en pê tê"),
                        source="retrieval",
                        confidence=0.95,
                        last_seen="2026-01-01T00:00:00+00:00",
                        expires_at="2030-01-01T00:00:00+00:00",
                    ),
                )
            )
            reloaded = AdaptiveDictionary.from_paths(
                seed_path=seed, state_path=state
            )

            self.assertEqual(
                reloaded.hotword_phrases(minimum_confidence=0.9),
                ("VNPT",),
            )
            self.assertEqual(
                reloaded.dynamic_phonetic_entries(minimum_confidence=0.75),
                (("VNPT", ("v n p t", "vê en pê tê")),),
            )

    def test_manual_terms_feed_hotword_and_phonetic_views(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manual = root / "meeting_lexicon.txt"
            manual.write_text(
                "HDFS | h d f s | hát đê ép ét\n", encoding="utf-8"
            )
            dictionary = AdaptiveDictionary.from_paths(
                seed_path=None,
                state_path=root / "state.json",
                manual_path=manual,
            )

            self.assertEqual(
                dictionary.hotword_phrases(minimum_confidence=0.9),
                ("HDFS",),
            )
            self.assertEqual(
                dictionary.dynamic_phonetic_entries(
                    minimum_confidence=0.75
                ),
                (("HDFS", ("h d f s", "hát đê ép ét")),),
            )

    def test_single_token_participant_is_not_a_decoder_hotword(self) -> None:
        entries = AdaptiveDictionary.entries_from_participants(
            ("Đạt", "Nguyễn Văn Long")
        )
        dictionary = AdaptiveDictionary(
            state_path=Path("unused.json"),
            dynamic_entries=entries,
        )

        self.assertEqual(
            dictionary.hotword_phrases(minimum_confidence=0.9),
            ("Nguyễn Văn Long",),
        )
        self.assertEqual(
            tuple(item[0] for item in dictionary.dynamic_phonetic_entries(
                minimum_confidence=0.75
            )),
            ("Đạt", "Nguyễn Văn Long"),
        )

    def test_expired_or_low_confidence_entry_is_not_a_hotword(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        dictionary = AdaptiveDictionary(
            state_path=Path("unused.json"),
            dynamic_entries=(
                GlossaryEntry(
                    canonical="Expired",
                    aliases=(),
                    source="retrieval",
                    confidence=1.0,
                    last_seen=now.isoformat(),
                    expires_at=(now - timedelta(seconds=1)).isoformat(),
                ),
                GlossaryEntry(
                    canonical="Uncertain",
                    aliases=(),
                    source="asr_observation",
                    confidence=0.6,
                    last_seen=now.isoformat(),
                ),
            ),
        )

        self.assertEqual(
            dictionary.hotword_phrases(minimum_confidence=0.9, now=now), ()
        )
        self.assertEqual(
            dictionary.dynamic_phonetic_entries(
                minimum_confidence=0.75, now=now
            ),
            (),
        )

    def test_hotwords_keep_only_canonical_surface(self) -> None:
        dictionary = AdaptiveDictionary(
            state_path=Path("unused.json"),
            dynamic_entries=(
                GlossaryEntry(
                    canonical="Hadoop Storage",
                    aliases=("adolf sorus",),
                    source="topic_discovery",
                    confidence=1.0,
                    last_seen="2026-01-01T00:00:00+00:00",
                ),
            ),
        )

        self.assertEqual(
            dictionary.hotword_phrases(minimum_confidence=0.9),
            ("Hadoop Storage",),
        )

if __name__ == "__main__":
    unittest.main()
