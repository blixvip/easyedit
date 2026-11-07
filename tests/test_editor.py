"""Editor saves validate the whole request before replacing any saved choices."""
import tempfile
import unittest
from pathlib import Path

from easyedit import editor
from easyedit.util import read_json, write_json
from tests.test_sequence import _job


def _ready_job(root):
    job = _job(root)
    write_json(job / "plan.json", {"title": "Original", "palette": {}})
    write_json(job / "quote.json", {"clip": 0, "start": 1, "end": 2,
                                    "lines": [[{"text": "Hello", "start": 1, "end": 2, "role": None}]],
                                    "files": [str(job / "downloads/speech/sp.mp4")]})
    write_json(job / "work/shots-mo.json", {"source": str(job / "downloads/montage/mo.mp4"),
                                           "crop": None, "shots": []})
    write_json(job / "work/beats-song.json", {"beats": [], "bpm": 120, "loud": [], "strength": [], "duration": 30})
    return job


class Saves(unittest.TestCase):
    def test_invalid_quote_does_not_save_other_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _ready_job(Path(tmp))
            original = (job / "plan.json").read_bytes()
            with self.assertRaises(ValueError):
                editor.save(job, {"plan": {"title": "Changed"}, "quote": {"lines": []}})
            self.assertEqual((job / "plan.json").read_bytes(), original)

    def test_caption_times_must_be_finite(self):
        for value in (float("nan"), float("inf"), -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    editor._clean_lines([[{"text": "Word", "start": value, "end": 2}]])

    def test_valid_plan_quote_and_sequence_save_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _ready_job(Path(tmp))
            result = editor.save(job, {"plan": {"title": "Changed"},
                                      "quote": {"lines": [[{"text": "New", "start": 1, "end": 2}]]},
                                      "seq": {"video": [], "audio": []}})
            self.assertEqual(result["plan"]["title"], "Changed")
            self.assertEqual(result["quote"]["lines"][0][0]["text"], "New")
            self.assertTrue(result["seqSaved"])
            self.assertIn("files", read_json(job / "quote.json"))

    def test_custom_sequence_rejects_legacy_layout_changes_without_partial_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _ready_job(Path(tmp))
            editor.save(job, {"seq": {"video": [], "audio": []}})
            with self.assertRaisesRegex(ValueError, "beat layout"):
                editor.save(job, {"plan": {"title": "Should not save"}, "settings": {"montage_length": 10}})
            self.assertEqual(read_json(job / "plan.json")["title"], "Original")

    def test_explicit_beat_layout_switch_backs_up_saved_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _ready_job(Path(tmp))
            editor.save(job, {"seq": {"video": [], "audio": []}})
            saved = read_json(job / "timeline.json")
            result = editor.save(job, {"seq": None, "settings": {"montage_length": 10}})
            self.assertFalse((job / "timeline.json").exists())
            self.assertEqual(read_json(job / "timeline.before-beat.json"), saved)
            self.assertFalse(result["seqSaved"])

    def test_custom_sequence_projects_its_actual_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _ready_job(Path(tmp))
            result = editor.save(job, {"seq": {"fps": 30, "video": [[
                {"id": "shot", "src": "montage:mo.mp4", "kind": "shot", "at": 0, "dur": 2.4, "in": 0}]],
                "audio": []}})
            self.assertEqual(result["timeline"]["frames"], 144)
            self.assertEqual(result["timeline"]["speechFrames"], 0)
            self.assertTrue(result["timeline"]["sequence"])

    def test_visual_shot_changes_reach_sequence_and_preserve_clip_transforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _ready_job(Path(tmp))
            analysis = read_json(job / "work/shots-mo.json")
            analysis["shots"] = [{"start": 0, "end": 4, "face": None, "motion": 1,
                                  "sharp": 1, "contrast": 1, "sat": 1, "thumb": [0] * (32 * 18)}]
            write_json(job / "work/shots-mo.json", analysis)
            editor.save(job, {"seq": {"video": [[
                {"id": "shot", "src": "montage:mo.mp4", "kind": "shot", "at": 0, "dur": 2, "in": 0,
                 "shot": "mo@0.00", "opts": {"tf": {"x": 120}}}]], "audio": []}})
            result = editor.save(job, {"curation": {"shots": {"mo@0.00": {"fx": ["glow"]}}}})
            self.assertEqual(result["seq"]["video"][0][0]["opts"], {"tf": {"x": 120.0}, "fx": ["glow"]})


class SourceQuality(unittest.TestCase):
    def test_original_is_default_even_when_analysis_proxy_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, original, proxy = self._sources(Path(tmp))
            self.assertEqual(editor.source(job, "montage", 0), original)
            self.assertEqual(editor.source(job, "montage", 0, proxy=True), proxy)

    def test_audio_uses_original_even_when_proxy_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, original, _ = self._sources(Path(tmp))
            self.assertEqual(editor.source(job, "montage", 0, audio=True, proxy=True), original)

    def test_proxy_request_falls_back_to_available_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, original, proxy = self._sources(Path(tmp))
            proxy.unlink()
            self.assertEqual(editor.source(job, "montage", 0, proxy=True), original)

    def test_missing_original_can_recover_video_from_silent_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, original, proxy = self._sources(Path(tmp))
            original.unlink()
            self.assertEqual(editor.source(job, "montage", 0), proxy)
            self.assertIsNone(editor.source(job, "montage", 0, audio=True))
            self.assertIsNone(editor.source(job, "montage", 0, audio=True, proxy=True))
            proxy.unlink()
            self.assertIsNone(editor.source(job, "montage", 0))

    def test_invalid_source_returns_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, _, _ = self._sources(Path(tmp))
            for kind, index in [("other", 0), ("montage", -1), ("montage", 1)]:
                with self.subTest(kind=kind, index=index):
                    self.assertIsNone(editor.source(job, kind, index))

    @staticmethod
    def _sources(root):
        original = root / "downloads" / "montage" / "original.mp4"
        proxy = original.parent / "proxy" / original.name
        proxy.parent.mkdir(parents=True)
        original.write_bytes(b"original footage")
        proxy.write_bytes(b"540p analysis copy")
        write_json(root / "sources.json", {"speech": [], "montage": [str(original)], "music": []})
        return root, original, proxy


if __name__ == "__main__":
    unittest.main()
