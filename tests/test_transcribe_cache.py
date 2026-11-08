"""The transcript cache must not be reused when --language or EASYEDIT_WHISPER changes (issue #29)."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from easyedit import __main__, transcribe
from easyedit.util import read_json, write_json


class _FakeModel:
    def transcribe(self, path, **kwargs):
        word = SimpleNamespace(word=" bonjour", start=0.0, end=0.5, probability=0.9)
        return [SimpleNamespace(words=[word])], None


class TranscriptCacheTest(unittest.TestCase):
    def test_a_different_language_is_transcribed_again(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        # the English transcript an earlier run left behind under the old cache name
        write_json(work / "words-clip.json", [{"text": "hello", "start": 0.0, "end": 0.5, "prob": 0.9}])

        with mock.patch.object(transcribe, "_load", return_value=_FakeModel()) as load:
            words = transcribe.transcribe(Path("clip.mp4"), work / "words-clip.json", "fr")

        load.assert_called_once()
        self.assertEqual([w["text"] for w in words], ["bonjour"])
        self.assertEqual([w["text"] for w in read_json(transcribe.cache_for(work / "words-clip.json", "fr"))],
                         ["bonjour"])

    def test_a_different_whisper_model_is_transcribed_again(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        write_json(work / "words-clip.json", [{"text": "hello", "start": 0.0, "end": 0.5, "prob": 0.9}])

        with mock.patch.dict(os.environ, {"EASYEDIT_WHISPER": "tiny"}), \
                mock.patch.object(transcribe, "_load", return_value=_FakeModel()) as load:
            words = transcribe.transcribe(Path("clip.mp4"), work / "words-clip.json", None)

        load.assert_called_once_with("tiny")
        self.assertEqual([w["text"] for w in words], ["bonjour"])


class PickIsCurrentTest(unittest.TestCase):
    def test_a_quote_from_another_transcript_is_not_reused(self):
        pick = {"files": ["speech.mp4"], "transcripts": ["work/words-speech-en-distil-large-v3.json"]}
        self.assertTrue(__main__.pick_is_current(pick, [Path("speech.mp4")],
                                                 ["work/words-speech-en-distil-large-v3.json"]))
        self.assertFalse(__main__.pick_is_current(pick, [Path("speech.mp4")],
                                                  ["work/words-speech-fr-large-v3.json"]))
        self.assertFalse(__main__.pick_is_current({"files": ["speech.mp4"]}, [Path("speech.mp4")],
                                                  ["work/words-speech-en-distil-large-v3.json"]))


if __name__ == "__main__":
    unittest.main()
