"""Regression tests for setup diagnostics."""
import unittest
from unittest import mock

from easyedit import doctor


class DoctorToolsTest(unittest.TestCase):
    def test_ffprobe_missing_is_named(self):
        def which(name):
            return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

        with mock.patch.object(doctor.shutil, "which", side_effect=which):
            ffmpeg = next(tool for tool in doctor.tools() if tool["name"] == "FFmpeg")

        self.assertFalse(ffmpeg["ok"])
        self.assertEqual(ffmpeg["detail"], "ffprobe missing")

    def test_ffmpeg_missing_is_named(self):
        def which(name):
            return "/usr/bin/ffprobe" if name == "ffprobe" else None

        with mock.patch.object(doctor.shutil, "which", side_effect=which):
            ffmpeg = next(tool for tool in doctor.tools() if tool["name"] == "FFmpeg")

        self.assertFalse(ffmpeg["ok"])
        self.assertEqual(ffmpeg["detail"], "ffmpeg missing")


if __name__ == "__main__":
    unittest.main()