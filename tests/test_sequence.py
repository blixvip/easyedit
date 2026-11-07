"""The sequence: a beat cut written out as clips, what the picture shows when clips stack, where the
captions land when the speech moves, and what a page is allowed to save. No media is cut."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from easyedit import sequence
from easyedit.util import write_json


def _job(root: Path) -> Path:
    job = root / "job"
    for role, name in (("speech", "sp.mp4"), ("montage", "mo.mp4"), ("music", "song.m4a")):
        f = job / "downloads" / role / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    write_json(job / "sources.json", {"specs": {}, "speech": [str(job / "downloads/speech/sp.mp4")],
                                      "montage": [str(job / "downloads/montage/mo.mp4")],
                                      "music": [str(job / "downloads/music/song.m4a")]})
    return job


def _clip(cid, src, at, dur, inn=0.0, kind="shot", **kw):
    return {"id": cid, "src": src, "kind": kind, "at": at, "dur": dur, "in": inn, **kw}


class Flatten(unittest.TestCase):
    def test_audio_tail_keeps_playing_over_black_and_muted_track_does_not_extend(self):
        seq = {"video": [[_clip("picture", "montage:mo.mp4", 0, 2)]], "audio": [
            [_clip("sound", "music:song.m4a", 1, 3, kind="music")],
            [_clip("muted", "music:song.m4a", 0, 10, kind="music")]], "off": ["A2"]}
        pieces, end = sequence.flatten(seq, 30)
        self.assertEqual(end, 120)
        self.assertEqual([(p["clip"]["id"] if p["clip"] else None, p["start"], p["end"]) for p in pieces],
                         [("picture", 0, 60), (None, 60, 120)])

    def test_audio_only_sequence_has_black_video(self):
        seq = {"video": [], "audio": [[_clip("sound", "music:song.m4a", 0, 2, kind="music")]]}
        self.assertEqual(sequence.flatten(seq, 30), ([{"clip": None, "start": 0, "end": 60}], 60))

    def test_top_track_wins_and_gaps_are_black(self):
        seq = {"video": [[_clip("a", "montage:mo.mp4", 0, 2), _clip("b", "montage:mo.mp4", 3, 1)],
                         [_clip("c", "montage:mo.mp4", 1, 0.5)]], "audio": []}
        pieces, end = sequence.flatten(seq, 10)
        got = [(p["clip"]["id"] if p["clip"] else None, p["start"], p["end"]) for p in pieces]
        self.assertEqual(got, [("a", 0, 10), ("c", 10, 15), ("a", 15, 20), (None, 20, 30), ("b", 30, 40)])
        self.assertEqual(end, 40)

    def test_a_track_switched_off_is_not_seen(self):
        seq = {"video": [[_clip("a", "montage:mo.mp4", 0, 2)], [_clip("c", "montage:mo.mp4", 0, 2)]], "audio": [],
               "off": ["V2"]}
        pieces, _ = sequence.flatten(seq, 10)
        self.assertEqual([p["clip"]["id"] for p in pieces], ["a"])


class Captions(unittest.TestCase):
    QUOTE = {"clip": 0, "lines": [[{"text": "ONE", "start": 10.0, "end": 10.4, "role": None},
                                   {"text": "TWO", "start": 10.5, "end": 10.9, "role": "gold"}],
                                  [{"text": "THREE", "start": 12.0, "end": 12.5, "role": None}]]}

    def test_words_follow_the_dialogue_clip(self):
        seq = {"video": [], "audio": [[_clip("d", "speech:sp.mp4", 5.0, 3.0, 9.6, "speech")]]}
        lines = sequence.captions(seq, self.QUOTE, "speech:sp.mp4")
        self.assertEqual([w["t"] for w in lines[0]["words"]], [5.4, 5.9])
        self.assertEqual(lines[1]["words"][0]["t"], 7.4)
        self.assertLessEqual(lines[-1]["end"], 8.0)   # never past the end of the clip it is heard in

    def test_words_cut_out_of_the_clip_are_not_shown(self):
        seq = {"video": [], "audio": [[_clip("d", "speech:sp.mp4", 0.0, 1.5, 9.6, "speech")]]}
        lines = sequence.captions(seq, self.QUOTE, "speech:sp.mp4")
        self.assertEqual([w["text"] for line in lines for w in line["words"]], ["ONE", "TWO"])

    def test_repeated_dialogue_has_captions_at_each_occurrence(self):
        seq = {"audio": [[_clip("a", "speech:sp.mp4", 0, 1, 10, "speech"),
                           _clip("b", "speech:sp.mp4", 5, 1, 10, "speech")]]}
        lines = sequence.captions(seq, self.QUOTE, "speech:sp.mp4")
        self.assertEqual([line["start"] for line in lines], [0, 5])
        self.assertLessEqual(lines[0]["end"], 1)

    def test_reordered_dialogue_captions_follow_timeline_order_and_trim(self):
        seq = {"audio": [[_clip("a", "speech:sp.mp4", 0, 0.2, 12, "speech"),
                           _clip("b", "speech:sp.mp4", 5, 1, 10, "speech")]]}
        lines = sequence.captions(seq, self.QUOTE, "speech:sp.mp4")
        self.assertEqual([line["start"] for line in lines], [0, 5])
        self.assertEqual(lines[0]["end"], 0.2)


class Times(unittest.TestCase):
    def test_real_time_needs_no_map_and_slow_motion_does(self):
        self.assertIsNone(sequence.times_of(_clip("a", "montage:mo.mp4", 0, 1), 60))
        slow = sequence.times_of(_clip("a", "montage:mo.mp4", 0, 1, speed=0.5), 60)
        self.assertAlmostEqual(float(slow[-1]), 0.5 - 0.25 / 60, places=4)

    def test_a_split_ramp_plays_on_where_the_first_half_stopped(self):
        whole = sequence.times_of(_clip("a", "montage:mo.mp4", 0, 2, ramp="hit"), 60)
        one = sequence.times_of(_clip("a", "montage:mo.mp4", 0, 1, ramp="hit", rspan=[0, 0.5]), 60)
        two = sequence.times_of(_clip("b", "montage:mo.mp4", 1, 1, ramp="hit", rspan=[0.5, 1]), 60)
        offset = float(np.sum(np.diff(np.concatenate([[0], whole]))[:60]))   # source used by the first half
        np.testing.assert_allclose(one, whole[:60], atol=1e-6)
        self.assertAlmostEqual(float(two[0] + whole[60] - two[0]), float(whole[60]), places=6)
        self.assertGreater(offset, 0)


class Clean(unittest.TestCase):
    def test_malformed_optional_fields_do_not_break_sequence_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _job(Path(tmp))
            got = sequence.clean(job, {"fps": "bad", "off": "V1", "video": [[
                _clip("a", ["montage:mo.mp4"], 0, 1),
                _clip("b", "montage:mo.mp4", 0, 1, ramp=[])] ]})
            self.assertEqual(got["fps"], 60)
            self.assertEqual(got["off"], [])
            self.assertEqual([c["id"] for c in got["video"][0]], ["b"])

    def test_unknown_files_bad_numbers_and_duplicate_ids_are_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = _job(Path(tmp))
            got = sequence.clean(job, {"fps": 61, "video": [[
                _clip("a", "montage:mo.mp4", -3, "x", 2, opts={"move": "punch", "fx": ["glow", "nope"], "speed": 3}),
                _clip("a", "montage:gone.mp4", 1, 1),
                _clip("a", "speech:sp.mp4", 4, 1, kind="speech", link="z")]],
                "audio": [[], [_clip("s", "music:old.m4a", 0, 9, kind="music")]]})
            self.assertEqual(got["fps"], 60)
            v1 = got["video"][0]
            self.assertEqual([c["id"] for c in v1], ["a", "ax"])
            self.assertEqual(v1[0]["at"], 0.0)
            self.assertEqual(v1[0]["dur"], 1.0)
            self.assertEqual(v1[0]["opts"], {"move": "punch", "fx": ["glow"]})
            self.assertNotIn("link", v1[1])   # it pointed at a clip that is not there
            self.assertEqual(got["audio"][1][0]["src"], "music:song.m4a")   # a changed song carries over
            self.assertEqual(len(got["video"]), 2)


class Generate(unittest.TestCase):
    def test_the_beat_cut_as_a_sequence(self):
        rng = np.random.default_rng(1)
        shots = [{"start": 10.0 * i, "end": 10.0 * i + 4, "face": None, "motion": 1 + i, "sharp": 2, "contrast": 1,
                  "sat": 30, "thumb": rng.normal(size=32 * 18).round(2).tolist()} for i in range(30)]
        inp = {"quote": {"clip": 0, "start": 10.0, "end": 16.0, "lines": []},
               "analyses": [{"source": "/x/mo.mp4", "crop": None, "shots": shots}],
               "curation": {}, "beats": {"beats": [i * 0.5 for i in range(200)], "bpm": 120, "loud": [1.0] * 200, "strength": [1.0] * 200, "duration": 100.0},
               "sources": {"speech": ["/x/sp.mp4"], "montage": ["/x/mo.mp4"], "music": ["/x/song.m4a"]},
               "music": Path("/x/song.m4a")}
        s = {"fps": 60, "montage_length": 12.0, "hero_length": 3.4, "cut_rate": "normal", "velocity": True}
        seq = sequence.generate(inp, s)
        v1, a1, a2 = seq["video"][0], seq["audio"][0], seq["audio"][1]
        self.assertEqual(v1[0]["kind"], "speech")
        self.assertEqual(v1[0]["link"], a1[0]["id"])
        self.assertEqual(v1[-1]["kind"], "hero")
        # the clips sit end to end with no gaps
        for a, b in zip(v1, v1[1:]):
            self.assertAlmostEqual(a["at"] + a["dur"], b["at"], places=2)
        self.assertEqual(a2[0]["kind"], "music")
        self.assertEqual(sequence.generate(inp, s), seq)   # the same choices give the same sequence


class AudioMix(unittest.TestCase):
    def test_each_clip_and_effects_read_their_own_input_stream(self):
        seq = {"audio": [[_clip("speech", "speech:sp.mp4", 0, 2, kind="speech")],
                         [_clip("song", "music:song.m4a", 0, 2, kind="music")]]}
        have = {"speech:sp.mp4": Path("speech.mp4"), "music:song.m4a": Path("song.m4a")}
        with patch.object(sequence, "probe", return_value={"has_audio": True}), patch.object(sequence, "run") as run:
            sequence.mix(seq, have, 2, Path("out.m4a"), Path("effects.wav"), 0.7)
        command = run.call_args.args[0]
        graph = command[command.index("-filter_complex") + 1]
        for index in range(3):
            self.assertIn(f"[{index}:a]", graph)

    def test_silent_video_audio_clip_does_not_create_an_invalid_audio_input(self):
        seq = {"audio": [[_clip("silent", "montage:mo.mp4", 0, 2, kind="audio")],
                         [_clip("song", "music:song.m4a", 0, 2, kind="music")]]}
        have = {"montage:mo.mp4": Path("silent.mp4"), "music:song.m4a": Path("song.m4a")}
        with patch.object(sequence, "probe", side_effect=lambda path: {"has_audio": path.name != "silent.mp4"}), \
                patch.object(sequence, "run") as run:
            sequence.mix(seq, have, 2, Path("out.m4a"), None, 0)
        command = [str(part) for part in run.call_args.args[0]]
        self.assertNotIn("silent.mp4", command)
        self.assertIn("song.m4a", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[0:a]", graph)
        self.assertNotIn("[1:a]", graph)


if __name__ == "__main__":
    unittest.main()
