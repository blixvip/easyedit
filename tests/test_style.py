"""The style analyzer finds hard cuts where they were made and reads back a house-style edit."""
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from easyedit import style
from easyedit.util import run


def _clip(path: Path, lengths: list[float], fps: int = 30) -> None:
    """Solid-colour shots of the given lengths, hard cut, with a quiet tone so there is an audio track."""
    colours = ["red", "blue", "green", "yellow", "magenta", "purple", "orange", "cyan"]
    parts, filters = [], []
    for i, d in enumerate(lengths):
        parts += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", f"color=c={colours[i % len(colours)]}:s=320x240:r={fps}"]
        filters.append(f"[{i}:v]")
    n = len(lengths)
    total = sum(lengths)
    parts += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "sine=frequency=220:sample_rate=22050"]
    graph = "".join(filters) + f"concat=n={n}:v=1:a=0[v]"
    run(["ffmpeg", "-v", "error", "-y", *parts, "-filter_complex", graph, "-map", "[v]", "-map", f"{n}:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class CutTimes(unittest.TestCase):
    def test_cuts_land_where_the_shots_change(self):
        lengths = [1.0, 0.5, 0.25, 2.0, 0.75, 1.5]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "cuts.mp4"
            _clip(src, lengths)
            reel = style.analyze_reel(src, Path(tmp) / "work", faces=_NoFaces(), speech=False)
        expected = np.cumsum(lengths)[:-1]
        self.assertEqual(len(reel["cuts"]), len(expected), reel["cuts"])
        for got, want in zip(reel["cuts"], expected):
            self.assertAlmostEqual(got, want, delta=1.5 / style.FAST_FPS)
        self.assertAlmostEqual(reel["shot_lens"][3], 2.0, delta=0.1)
        self.assertEqual(reel["mono"]["fraction"], 0.0)   # pure colours are not black and white
        self.assertEqual(reel["captions"]["mode"], "none")

    def test_a_grey_tail_reads_as_mono(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "mono.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "2", "-i", "color=c=red:s=320x240:r=30",
                 "-f", "lavfi", "-t", "2", "-i", "color=c=gray:s=320x240:r=30",
                 "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", src])
            reel = style.analyze_reel(src, Path(tmp) / "work", faces=_NoFaces(), speech=False)
        self.assertGreater(reel["mono"]["outro"], 0.9)
        self.assertLess(reel["mono"]["intro"], 0.1)
        self.assertEqual(reel["audio"], {"music": False})


class _NoFaces:
    def detect(self, img):
        return []


if __name__ == "__main__":
    unittest.main()
