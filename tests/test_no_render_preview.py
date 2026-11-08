"""Preview composition generation for --no-render builds."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyedit import render


class NoRenderPreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = self.root / "template"
        self.template.mkdir()
        (self.template / "film.js").write_text("window.FILM = true;", encoding="utf-8")
        (self.template / "index.html").write_text(
            '<video data-duration="__DURATION__" data-fps="__FPS__" '
            'data-start="__MEDIA_START__" data-segment="__SEGMENT_START__"></video>',
            encoding="utf-8",
        )
        self.root_patch = mock.patch.object(render, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_preview_composition_is_written_when_font_download_fails(self):
        job = self.root / "jobs" / "movie"
        fonts = job / "render" / "fonts"

        def unavailable_fonts(destination):
            destination.mkdir(parents=True, exist_ok=True)
            raise OSError("offline")

        with mock.patch.object(render, "ensure_fonts", side_effect=unavailable_fonts):
            render_dir = render.prepare_preview(job, {"frames": 1800, "fps": 30})

        self.assertEqual(render_dir, job / "render")
        self.assertTrue((render_dir / "film.js").is_file())
        self.assertTrue(fonts.is_dir())
        composition = (render_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-duration="60.0000"', composition)
        self.assertIn('data-fps="30"', composition)
        self.assertIn('data-start="0.0000"', composition)
        self.assertIn('data-segment="0.0000"', composition)

    def test_font_errors_still_fail_for_actual_render_preparation(self):
        render_dir = self.root / "render"
        with mock.patch.object(render, "ensure_fonts", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                render.prepare(render_dir)
        self.assertFalse((render_dir / "film.js").exists())


if __name__ == "__main__":
    unittest.main()
