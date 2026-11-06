import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from easyedit import beats, sheet, shots, vision


class FFmpegFailureTest(unittest.TestCase):
    def sample(self, script):
        popen = subprocess.Popen
        def start(cmd, **kwargs):
            return popen([sys.executable, "-c", script], **kwargs)
        return patch("easyedit.vision.subprocess.Popen", side_effect=start)

    @patch("easyedit.util.probe", return_value={"width": 2, "height": 2, "duration": 10})
    def test_stream_success(self, probe):
        with self.sample("import sys; sys.stdout.buffer.write(bytes(range(12)))"):
            result = list(vision.frames(Path("video"), 2, 2, start=3, height=2))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], 3)
        np.testing.assert_array_equal(result[0][1].ravel(), np.arange(12))

    @patch("easyedit.util.probe", return_value={"width": 2, "height": 2, "duration": 10})
    def test_stream_reports_failure_after_partial_output_without_stderr_deadlock(self, probe):
        script = "import sys; sys.stdout.buffer.write(bytes(12)); sys.stdout.flush(); sys.stderr.write('x'*200000+'TAIL'); sys.exit(7)"
        with self.sample(script):
            it = vision.frames(Path("video"), 1, 2, height=2)
            self.assertEqual(next(it)[1].shape, (2, 2, 3))
            with self.assertRaisesRegex(RuntimeError, "exit 7.*TAIL"):
                next(it)

    @patch("easyedit.util.probe", return_value={"width": 2, "height": 2, "duration": 10})
    def test_empty_success_and_failure_reach_letterbox(self, probe):
        with self.sample("pass"):
            self.assertIsNone(vision.letterbox(Path("video"), 10))
        with self.sample("import sys; sys.stderr.write('bad video'); sys.exit(3)"):
            with self.assertRaisesRegex(RuntimeError, "bad video"):
                vision.letterbox(Path("video"), 10)

    @patch("easyedit.util.probe", return_value={"width": 2, "height": 2, "duration": 10})
    def test_early_close_reaps_child(self, probe):
        children = []
        popen = subprocess.Popen
        def start(cmd, **kwargs):
            child = popen([sys.executable, "-c", "import sys,time; sys.stdout.buffer.write(bytes(12)); sys.stdout.flush(); time.sleep(30)"], **kwargs)
            children.append(child)
            return child
        with patch("easyedit.vision.subprocess.Popen", side_effect=start):
            it = vision.frames(Path("video"), 1, 2, height=2)
            next(it)
            it.close()
        self.assertIsNotNone(children[0].poll())

    def test_audio_and_sheet_check_exit_before_output(self):
        failure = subprocess.CompletedProcess([], 9, b"", b"decoder failed")
        for module, call in [(beats, lambda: beats.load_audio(Path("audio"))),
                             (sheet, lambda: sheet._grab(Path("video"), 0))]:
            with patch.object(module, "run", return_value=failure):
                with self.assertRaisesRegex(RuntimeError, "exit 9.*decoder failed"):
                    call()
        with patch.object(beats, "run", return_value=subprocess.CompletedProcess([], 0, b"\x00\x00", b"")):
            np.testing.assert_array_equal(beats.load_audio(Path("audio")), [0])
        with patch.object(sheet, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            self.assertIsNone(sheet._grab(Path("video"), 0))
        image = bytes(sheet.TW * sheet.TH * 3)
        with patch.object(sheet, "run", return_value=subprocess.CompletedProcess([], 0, image, b"")):
            self.assertEqual(sheet._grab(Path("video"), 0).shape, (sheet.TH, sheet.TW, 3))

    @patch("easyedit.shots.probe", return_value={"width": 2, "height": 2, "duration": 10})
    @patch("easyedit.util.probe", return_value={"width": 2, "height": 2, "duration": 10})
    def test_shots_propagates_decoder_failure(self, probe, shots_probe):
        with self.sample("import sys; sys.stderr.write('broken'); sys.exit(1)"):
            with self.assertRaisesRegex(RuntimeError, "broken"):
                shots.analyze(Path("video"), Path("nonexistent-cache.json"), None)
