"""Regression tests for setup diagnostics."""
import contextlib
import io
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

    def test_json_mode_exit_code_follows_ready(self):
        for ready, code in ((False, 1), (True, 0)):
            report = {"ready": ready, "accounts": [], "tools": []}
            with self.subTest(ready=ready), \
                    mock.patch.object(doctor, "report", return_value=report), \
                    mock.patch.object(doctor.sys, "argv", ["doctor", "--json"]), \
                    contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
                with self.assertRaises(SystemExit) as cm:
                    doctor.main()
                self.assertEqual(cm.exception.code, code)


if __name__ == "__main__":
    unittest.main()