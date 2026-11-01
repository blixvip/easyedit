import unittest
from unittest.mock import patch
from easyedit.doctor import tools


class TestDoctorTools(unittest.TestCase):
    def test_ffmpeg_both_present(self):
        with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            item = next(t for t in tools() if t["name"] == "FFmpeg")
            self.assertTrue(item["ok"])
            self.assertEqual(item["detail"], "on PATH")

    def test_ffprobe_missing(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/ffmpeg" if x == "ffmpeg" else None):
            item = next(t for t in tools() if t["name"] == "FFmpeg")
            self.assertFalse(item["ok"])
            self.assertEqual(item["detail"], "ffprobe missing")

    def test_ffmpeg_missing(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/ffprobe" if x == "ffprobe" else None):
            item = next(t for t in tools() if t["name"] == "FFmpeg")
            self.assertFalse(item["ok"])
            self.assertEqual(item["detail"], "ffmpeg missing")

    def test_both_missing(self):
        with patch("shutil.which", return_value=None):
            item = next(t for t in tools() if t["name"] == "FFmpeg")
            self.assertFalse(item["ok"])
            self.assertEqual(item["detail"], "missing")


if __name__ == "__main__":
    unittest.main()
