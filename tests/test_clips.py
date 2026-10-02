"""The clip library's bookkeeping: what removing a clip does to the edit's own files. No network."""
import tempfile
import unittest
from pathlib import Path

from easyedit import clips
from easyedit.util import read_json, write_json


def _job(root: Path) -> tuple[Path, dict]:
    job = root / "job"
    files = {}
    for role, names in (("speech", ["sp-a.mp4", "cc-line.mp4"]), ("montage", ["mo-a.mp4", "sk-12.mp4"]),
                        ("music", ["song.m4a"])):
        for name in names:
            f = job / "downloads" / role / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
            files[name] = str(f)
    write_json(job / "sources.json", {"specs": {"speech": None, "montage": None, "music": None},
                                      "speech": [files["sp-a.mp4"], files["cc-line.mp4"]],
                                      "montage": [files["mo-a.mp4"], files["sk-12.mp4"]],
                                      "music": [files["song.m4a"]]})
    write_json(job / "quote.json", {"clip": 1, "files": [files["sp-a.mp4"], files["cc-line.mp4"]]})
    write_json(job / "curate.json", {"pin": ["sk-12@0.10", "mo-a@4.00"], "exclude": ["sk-12@3.00"],
                                     "hero": "sk-12@0.10", "shots": {"sk-12@0.10": {"move": "punch"}}})
    write_json(job / "clips.json", {files["sk-12.mp4"]: {"site": "sakuga", "id": "12", "roles": ["montage"],
                                                         "whole": True},
                                    files["cc-line.mp4"]: {"site": "clipcafe", "id": "line", "roles": ["speech"]}})
    write_json(job / "work" / "shots-sk-12.json", {"shots": [{}]})
    return job, files


class RemoveClipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job, self.files = _job(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_removing_a_shots_clip_drops_every_choice_that_named_it_but_keeps_its_file(self):
        lib = clips.remove(self.job, self.files["sk-12.mp4"], "montage")
        self.assertEqual(read_json(self.job / "sources.json")["montage"], [self.files["mo-a.mp4"]])
        self.assertEqual(read_json(self.job / "curate.json"), {"pin": ["mo-a@4.00"]})
        self.assertTrue(Path(self.files["sk-12.mp4"]).exists())
        self.assertTrue((self.job / "work" / "shots-sk-12.json").exists())
        self.assertEqual([k["path"] for k in lib["kept"]], [self.files["sk-12.mp4"]])
        self.assertEqual(clips.extras(self.job, "montage", []), [])   # kept is not the same as in the edit

    def test_a_kept_clip_is_deleted_only_when_asked(self):
        clips.remove(self.job, self.files["sk-12.mp4"], "montage")
        lib = clips.discard(self.job, self.files["sk-12.mp4"])
        self.assertEqual(lib["kept"], [])
        self.assertFalse(Path(self.files["sk-12.mp4"]).exists())
        self.assertFalse((self.job / "work" / "shots-sk-12.json").exists())
        with self.assertRaises(ValueError):
            clips.discard(self.job, self.files["mo-a.mp4"])   # still in the edit

    def test_remove_and_delete_in_one_step(self):
        clips.remove(self.job, self.files["sk-12.mp4"], "montage", delete=True)
        self.assertFalse(Path(self.files["sk-12.mp4"]).exists())
        self.assertNotIn(self.files["sk-12.mp4"], read_json(self.job / "clips.json"))

    def test_the_clip_the_captions_come_from_stays(self):
        with self.assertRaises(ValueError):
            clips.remove(self.job, self.files["cc-line.mp4"], "speech")
        self.assertTrue(Path(self.files["cc-line.mp4"]).exists())

    def test_removing_an_earlier_speech_clip_keeps_the_quote_on_its_clip(self):
        clips.remove(self.job, self.files["sp-a.mp4"], "speech")
        quote = read_json(self.job / "quote.json")
        self.assertEqual(quote, {"clip": 0, "files": [self.files["cc-line.mp4"]]})

    def test_the_last_clip_of_a_kind_cannot_be_removed(self):
        clips.remove(self.job, self.files["sk-12.mp4"], "montage")
        with self.assertRaises(ValueError):
            clips.remove(self.job, self.files["mo-a.mp4"], "montage")

    def test_added_clips_survive_a_fresh_search_for_sources(self):
        found = [Path(self.files["mo-a.mp4"])]
        self.assertEqual(clips.extras(self.job, "montage", found), [Path(self.files["sk-12.mp4"])])
        self.assertTrue(clips.whole(self.job, Path(self.files["sk-12.mp4"])))
        self.assertFalse(clips.whole(self.job, Path(self.files["mo-a.mp4"])))


class LinkTest(unittest.TestCase):
    def test_sites_that_block_apps_say_so(self):
        with self.assertRaises(ValueError) as caught:
            clips._from_link("https://getyarn.io/yarn-clip/0000")
        self.assertIn("Yarn", str(caught.exception))

    def test_a_sakugabooru_post_and_a_plain_link(self):
        self.assertEqual(clips._from_link("https://www.sakugabooru.com/post/show/146024")["id"], "146024")
        self.assertEqual(clips._from_link("https://youtu.be/dI0cfKtpfGI")["site"], "web")


if __name__ == "__main__":
    unittest.main()
