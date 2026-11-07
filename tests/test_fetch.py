"""Source quality selection and safe upgrades, with no network or media downloads."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyedit import fetch


def _hit(vid, title):
    return {"id": vid, "url": f"https://example.test/{vid}", "title": title, "duration": 120}


class SourceQuality(unittest.TestCase):
    def test_low_resolution_title_claim_cannot_beat_verified_full_hd(self):
        hits = [_hit("low", "Inception 4K scenepack clean 1080p no watermark"), _hit("high", "Inception scene")]
        with patch.object(fetch, "search", return_value=hits), \
                patch.object(fetch, "_quality", side_effect=[(360, 500), (1080, 3000)]), \
                patch.object(fetch, "download", side_effect=lambda url, *a, **kw: Path(url.rsplit("/", 1)[-1] + ".mp4")) as download, \
                patch.object(fetch, "probe", return_value={"width": 1920, "height": 1080, "duration": 120}):
            got = fetch.best_sources(["Inception"], Path("unused"), kind="montage", movie="Inception", n=1, min_dur=20, max_dur=300)
        self.assertEqual(got, [Path("high.mp4")])
        self.assertEqual(download.call_count, 1)

    def test_failed_quality_probe_never_downloads_the_unverified_upload(self):
        hit = _hit("broken", "Inception 4K scenepack clean 1080p no watermark")
        with patch.object(fetch, "search", return_value=[hit]), patch.object(fetch, "_quality", side_effect=RuntimeError("blocked")), \
                patch.object(fetch, "download") as download:
            with self.assertRaises(RuntimeError):
                fetch.best_sources(["Inception"], Path("unused"), kind="montage", movie="Inception", n=1, min_dur=20, max_dur=300)
        download.assert_not_called()

    def test_actual_cached_file_must_meet_the_advertised_quality_floor(self):
        hits = [_hit("old", "Inception 4K scenepack"), _hit("good", "Inception scene")]
        with patch.object(fetch, "search", return_value=hits), patch.object(fetch, "_quality", side_effect=[(2160, 15000), (1080, 3000)]), \
                patch.object(fetch, "download", side_effect=[Path("old.mp4"), Path("good.mp4")]), \
                patch.object(fetch, "probe", side_effect=[{"width": 640, "height": 360}, {"width": 1920, "height": 800}]):
            got = fetch.best_sources(["Inception"], Path("unused"), kind="montage", movie="Inception", n=1, min_dur=20, max_dur=300)
        self.assertEqual(got, [Path("good.mp4")])

    def test_quality_uses_the_selected_stream_not_unused_hdr_formats(self):
        class Ydl:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def extract_info(self, *args, **kwargs):
                return {"formats": [{"vcodec": "vp9", "width": 3840, "height": 2160, "tbr": 12000}],
                        "requested_formats": [{"vcodec": "avc1", "width": 1280, "height": 720, "tbr": 1200},
                                              {"vcodec": "none", "acodec": "aac", "tbr": 128}]}
        with patch.object(fetch, "_ydl", return_value=Ydl()) as ydl:
            self.assertEqual(fetch._quality("https://example.test/video", None), (720, 1200))
        self.assertEqual(ydl.call_args.args[0]["format"], fetch.VIDEO_FMT)
        self.assertEqual(ydl.call_args.args[0]["format_sort"], fetch.VIDEO_SORT)

    def test_cinema_crop_counts_by_landscape_resolution_but_portrait_does_not(self):
        self.assertEqual(fetch.video_height({"width": 1920, "height": 800}), 1080)
        self.assertEqual(fetch.video_height({"width": 3840, "height": 1600}), 2160)
        self.assertLess(fetch.video_height({"width": 720, "height": 1280}), 1080)


class DownloadCache(unittest.TestCase):
    def _ydl(self, failing=False):
        owner = self
        class Ydl:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def extract_info(self, *args, **kwargs):
                return {"id": "video", "title": "Test", "requested_formats": [
                    {"vcodec": "vp9", "width": 3840, "height": 2160, "tbr": 15000}]}
            def download(self, urls):
                owner.downloads += 1
                target = Path(self.options["outtmpl"].replace("%(id)s", "video").replace("%(ext)s", "mp4"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"new source")
                if failing: raise RuntimeError("download failed")
        return Ydl

    def test_default_keeps_valid_cached_video_and_ignores_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "video.info.json").write_text("{}")
            old = dest / "video.mp4"; old.write_bytes(b"original")
            self.downloads = 0
            with patch.object(fetch, "_ydl", side_effect=self._ydl()), \
                    patch.object(fetch, "probe", return_value={"width": 640, "height": 360, "duration": 120}):
                self.assertEqual(fetch.download("https://example.test/video", dest), old)
            self.assertEqual(self.downloads, 0)
            self.assertEqual(old.read_bytes(), b"original")

    def test_explicit_upgrade_returns_verified_new_file_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp); old = dest / "video.mp4"; old.write_bytes(b"original")
            self.downloads = 0
            def facts(path):
                return {"width": 640 if path == old else 3840, "height": 360 if path == old else 2160, "duration": 120}
            with patch.object(fetch, "_ydl", side_effect=self._ydl()), patch.object(fetch, "probe", side_effect=facts):
                upgraded = fetch.download("https://example.test/video", dest, upgrade=True)
            self.assertNotEqual(upgraded, old)
            self.assertEqual(upgraded.read_bytes(), b"new source")
            self.assertEqual(old.read_bytes(), b"original")
            self.assertEqual(self.downloads, 1)
            self.assertEqual(len(list(dest.glob("*.mp4"))), 2)

    def test_failed_upgrade_leaves_original_and_no_partial_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp); old = dest / "video.mp4"; old.write_bytes(b"original")
            self.downloads = 0
            with patch.object(fetch, "_ydl", side_effect=self._ydl(failing=True)), \
                    patch.object(fetch, "probe", return_value={"width": 640, "height": 360, "duration": 120}):
                with self.assertRaises(RuntimeError):
                    fetch.download("https://example.test/video", dest, upgrade=True)
            self.assertEqual(old.read_bytes(), b"original")
            self.assertEqual(list(dest.iterdir()), [old])

    def test_downloaded_lower_resolution_cannot_replace_sharper_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp); old = dest / "video.mp4"; old.write_bytes(b"original")
            self.downloads = 0
            def facts(path):
                return {"width": 1920 if path == old else 640, "height": 1080 if path == old else 360, "duration": 120}
            with patch.object(fetch, "_ydl", side_effect=self._ydl()), patch.object(fetch, "probe", side_effect=facts):
                self.assertEqual(fetch.download("https://example.test/video", dest, upgrade=True), old)
            self.assertEqual(self.downloads, 1)
            self.assertEqual(old.read_bytes(), b"original")
            self.assertEqual(list(dest.iterdir()), [old])


if __name__ == "__main__":
    unittest.main()
