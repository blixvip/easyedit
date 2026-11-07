"""Make an isolated, 2.4-second project for a real save/build/export smoke check.

Usage: py -3.12 -m tests.demo_project <new-job-folder>
Uses FFmpeg test patterns and caches analysis; never downloads footage or transcribes it.
The caller owns removing the new folder after the check.
"""
import argparse
from pathlib import Path

from easyedit import shots
from easyedit.util import run, write_json


def prepare(job: Path) -> Path:
    if job.exists():
        raise ValueError("choose a new folder for the smoke project")
    job = job.resolve()
    media = job / "downloads"
    media.mkdir(parents=True)
    source, song = media / "pattern.mp4", media / "tone.m4a"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3", "-threads", "1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", source])
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
         "-t", "3", "-c:a", "aac", song])
    write_json(job / "sources.json", {"specs": {"speech": None, "montage": None, "music": None},
                                      "speech": [str(source)], "montage": [str(source)], "music": [str(song)]})
    write_json(job / "plan.json", {"title": "EasyEdit workflow check", "year": 2026, "grade": "neutral",
                                   "palette": {"positive": "#66ddaa", "negative": "#ff6677", "gold": "#ffcc55", "cool": "#55aaff"},
                                   "speech_query": "local test", "montage_queries": [], "music_query": "local test"})
    words = [{"text": "WORKFLOW", "start": 0.2, "end": 0.7, "role": "gold"},
             {"text": "CHECK", "start": 0.7, "end": 1.0, "role": None}]
    write_json(job / "quote.json", {"clip": 0, "files": [str(source)], "start": 0.2, "end": 1.0, "lines": [words]})
    write_json(job / "work/words-pattern.json", words)
    write_json(job / "work/shots-pattern.json", {"v": shots.CACHE_VERSION, "source": str(source), "crop": None,
              "shots": [{"start": 0, "end": 3, "face": None, "motion": 1, "sharp": 1,
                         "contrast": 1, "sat": 50, "thumb": [0] * (32 * 18)}]})
    beat_times = [i / 2 for i in range(7)]
    write_json(job / "work/beats-tone.json", {"beats": beat_times, "bpm": 120, "loud": [1] * 7,
                                             "strength": [1] * 7, "duration": 3})
    write_json(job / "settings.json", {"fps": 30, "montage_length": 8, "hero_length": 2, "sfx": 0,
                                       "depth": False, "velocity": False})
    write_json(job / "timeline.json", {"v": 1, "fps": 30, "off": [], "video": [[
        {"id": "first", "src": "montage:pattern.mp4", "kind": "shot", "at": 0, "dur": 1.2, "in": 0, "shot": "pattern@0.00"},
        {"id": "last", "src": "montage:pattern.mp4", "kind": "hero", "at": 1.2, "dur": 1.2, "in": 1.2, "shot": "pattern@0.00"}], []],
        "audio": [[{"id": "dialogue", "src": "speech:pattern.mp4", "kind": "speech", "at": 0, "dur": 1.2, "in": 0}],
                  [{"id": "song", "src": "music:tone.m4a", "kind": "music", "at": 0, "dur": 2.4, "in": 0, "duck": True}]]})
    return job


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    print(prepare(parser.parse_args().job))
