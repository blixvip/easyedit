"""The clip library's bookkeeping: what removing a clip does to the edit's own files. No network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_upgraded_download_retains_its_youtube_origin(self):
        for name in ["dI0cfKtpfGI.mp4", "dI0cfKtpfGI.hq-2160p-a1b2c3d4.mp4"]:
            self.assertEqual(clips._youtube_id(Path(name)), "dI0cfKtpfGI")
        self.assertIsNone(clips._youtube_id(Path("local-video.hq-2160p.mp4")))

    def test_restore_upgraded_source_preserves_timeline_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            saved = job / "downloads" / "montage" / "dI0cfKtpfGI.hq-2160p-a1b2c3d4.mp4"
            saved.parent.mkdir(parents=True)
            downloaded = saved.with_name("dI0cfKtpfGI.mp4")
            downloaded.write_bytes(b"restored original quality")
            sources = {"speech": [], "montage": [str(saved)], "music": []}
            write_json(job / "sources.json", sources)
            timeline = {"video": [[{"id": "clip", "src": f"montage:{saved.name}"}]]}
            write_json(job / "timeline.json", timeline)
            with patch("easyedit.fetch.download", return_value=downloaded) as download:
                self.assertEqual(clips.restore(job), 1)
            self.assertEqual(download.call_args.args[0], "https://www.youtube.com/watch?v=dI0cfKtpfGI")
            self.assertTrue(download.call_args.kwargs["upgrade"])
            self.assertEqual(read_json(job / "sources.json"), sources)
            self.assertEqual(read_json(job / "timeline.json"), timeline)
            self.assertEqual(saved.read_bytes(), downloaded.read_bytes())

    def test_sites_that_block_apps_say_so(self):
        with self.assertRaises(ValueError) as caught:
            clips._from_link("https://getyarn.io/yarn-clip/0000")
        self.assertIn("Yarn", str(caught.exception))

    def test_a_sakugabooru_post_and_a_plain_link(self):
        self.assertEqual(clips._from_link("https://www.sakugabooru.com/post/show/146024")["id"], "146024")
        self.assertEqual(clips._from_link("https://youtu.be/dI0cfKtpfGI")["site"], "web")


class ImportClipTest(unittest.TestCase):
    def test_same_name_local_sources_keep_distinct_files_and_cache_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, files = _job(root)
            incoming = root / "other" / "mo-a.mp4"
            incoming.parent.mkdir()
            incoming.write_bytes(b"new local footage")
            imported, _ = clips._bring(job, {"site": "link", "url": str(incoming)}, "montage")
            self.assertNotEqual(imported.stem, "mo-a")
            self.assertIn((job / "downloads").resolve(), imported.resolve().parents)
            self.assertEqual(imported.read_bytes(), b"new local footage")
            self.assertEqual(incoming.read_bytes(), b"new local footage")
            self.assertEqual(Path(files["mo-a.mp4"]).read_bytes(), b"x")
            again, _ = clips._bring(job, {"site": "link", "url": str(incoming)}, "montage")
            self.assertEqual(again, imported)

    def test_different_extensions_cannot_share_an_analysis_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, files = _job(root)
            incoming = root / "mo-a.mov"
            incoming.write_bytes(b"different video")
            imported, _ = clips._bring(job, {"site": "file", "url": str(incoming)}, "montage")
            self.assertNotEqual(imported.stem, "mo-a")
            self.assertEqual(imported.suffix, ".mov")
            self.assertTrue(incoming.is_file())

    def test_an_existing_source_can_be_added_to_another_role_without_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, files = _job(Path(tmp))
            original = Path(files["mo-a.mp4"])
            imported, _ = clips._bring(job, {"site": "file", "url": str(original)}, "speech")
            self.assertEqual(imported, original)


if __name__ == "__main__":
    unittest.main()
