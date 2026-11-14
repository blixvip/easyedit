"""ensure_fonts() download hygiene (issue #30)."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyedit import render


class _InterruptedRead:
    def __init__(self):
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, length=-1):
        self.calls += 1
        if self.calls == 1:
            return b"partial font data" * 1000
        raise OSError("connection reset")


class EnsureFontsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / ".cache"
        self.patch_cache = mock.patch.object(render, "CACHE", self.cache)
        self.patch_cache.start()
        self.addCleanup(self.patch_cache.stop)

    def test_interrupted_download_leaves_no_cached_font(self):
        dest = self.root / "render" / "fonts"

        def interrupted_urlretrieve(url, filename=None):
            Path(filename).write_bytes(b"partial font data")
            raise OSError("connection reset")

        with mock.patch.object(
            render.urllib.request, "urlretrieve", side_effect=interrupted_urlretrieve
        ), mock.patch.object(
            render.urllib.request, "urlopen", side_effect=lambda url, timeout=None: _InterruptedRead()
        ):
            with self.assertRaises(OSError):
                render.ensure_fonts(dest)

        for name in render.FONTS:
            cached = self.cache / "fonts" / name
            self.assertFalse(cached.exists())
            self.assertFalse(cached.with_name(name + ".part").exists())

    def test_small_cached_font_is_redownloaded(self):
        dest = self.root / "render" / "fonts"
        cache_dir = self.cache / "fonts"
        cache_dir.mkdir(parents=True, exist_ok=True)
        good = b"font bytes" * 7000
        for name in render.FONTS:
            (cache_dir / name).write_bytes(b"truncated")

        with mock.patch.object(
            render.urllib.request, "urlopen", side_effect=lambda url, timeout=None: io.BytesIO(good)
        ):
            render.ensure_fonts(dest)

        for name in render.FONTS:
            self.assertEqual((cache_dir / name).read_bytes(), good)
            self.assertEqual((dest / name).read_bytes(), good)

    def test_download_uses_a_timeout(self):
        dest = self.root / "render" / "fonts"
        seen = {}

        def capturing_urlopen(url, timeout=None):
            seen["timeout"] = timeout
            return io.BytesIO(b"font bytes")

        with mock.patch.object(render.urllib.request, "urlopen", side_effect=capturing_urlopen):
            render.ensure_fonts(dest)

        self.assertIsInstance(seen["timeout"], (int, float))
        self.assertGreaterEqual(seen["timeout"], 1)


if __name__ == "__main__":
    unittest.main()
