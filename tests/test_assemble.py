"""Tiny FFmpeg cuts preserve the saved timeline when a source ends early."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyedit import assemble
from easyedit.util import probe, run


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class CutDuration(unittest.TestCase):
    def test_short_source_holds_its_last_frame_for_the_full_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "source.mp4", Path(tmp) / "cut.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=160x90:r=30",
                 "-t", "0.2", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", source])
            with patch.object(assemble, "W", 160), patch.object(assemble, "H", 90):
                assemble.cut(source, 0, 30, None, 30, output)
            self.assertEqual(round(probe(output)["duration"] * 30), 30)


if __name__ == "__main__":
    unittest.main()
