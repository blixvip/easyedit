"""Filling the montage: what happens when there are fewer good shots than beat cuts. No media."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from easyedit import assemble, shots
from easyedit.util import read_json, write_json


def _shot(src: int, start: float, length: float = 4.0, seed: int = 0) -> dict:
    thumb = np.random.default_rng(seed).normal(size=32 * 18).round(2).tolist()   # every shot looks different
    return {"src": src, "start": start, "end": start + length, "face": None, "thumb": thumb, "score": 1.0}


class Laps(unittest.TestCase):
    def test_first_time_round_is_story_order(self):
        self.assertEqual(shots.laps(5, 5), [(0, k) for k in range(5)])
        self.assertEqual(shots.laps(5, 3), [(0, 0), (0, 1), (0, 2)])

    def test_repeats_get_a_new_order(self):
        for n in range(2, 30):
            order = shots.laps(n, n * 3 + 1)
            self.assertEqual(len(order), n * 3 + 1)
            idx = [k for _, k in order]
            # every lap still uses each shot once
            for lap in range(3):
                self.assertEqual(sorted(idx[lap * n:(lap + 1) * n]), list(range(n)))
            # a shot never follows itself
            self.assertFalse(any(a == b for a, b in zip(idx, idx[1:])), n)
            if n >= 4:   # and the second lap does not replay the first one's pairs
                first = set(zip(idx[:n], idx[1:n]))
                second = set(zip(idx[n:2 * n], idx[n + 1:2 * n]))
                self.assertFalse(first & second, n)

    def test_edge_cases(self):
        self.assertEqual(shots.laps(0, 4), [])
        self.assertEqual([k for _, k in shots.laps(1, 3)], [0, 0, 0])


class Select(unittest.TestCase):
    def test_repeats_are_marked_and_enough_shots_are_not(self):
        pool = [_shot(0, 10.0 * i, seed=i) for i in range(6)]
        slots, hero = shots.select(pool, [1.0] * 4, 3.4)
        self.assertEqual(len(slots), 4)
        self.assertFalse(any(s.get("lap") for s in slots))
        slots, hero = shots.select(pool, [1.0] * 12, 3.4)
        laps = [s.get("lap", 0) for s in slots]
        self.assertEqual(laps.count(0), 5)        # one of the six is the final shot
        self.assertTrue(all(s is not hero for s in slots))
        ids = [s["start"] for s in slots]
        self.assertFalse(any(a == b for a, b in zip(ids, ids[1:])))
        # a repeat is a copy: the pool's own shot is not marked
        self.assertFalse(any(s.get("lap") for s in pool))

    def test_build_key_changes_only_for_edits_with_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ({"lines": []}, {"title": "x"}, Path(tmp) / "song.m4a", 60, 21.0, 3.4,
                    [{"source": "a.mp4", "crop": None, "shots": []}], {})
            self.assertEqual(assemble.build_key(*args), assemble.build_key(*args, laps=False))
            self.assertNotEqual(assemble.build_key(*args), assemble.build_key(*args, laps=True))


class WriteJson(unittest.TestCase):
    def test_whole_file_and_no_leftovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "deep" / "plan.json"
            write_json(f, {"a": 1})
            write_json(f, {"a": 2, "b": "ü"})
            self.assertEqual(read_json(f), {"a": 2, "b": "ü"})
            self.assertEqual([p.name for p in f.parent.iterdir()], ["plan.json"])


if __name__ == "__main__":
    unittest.main()
