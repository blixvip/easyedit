import unittest
from unittest import mock

from easyedit import plan
from easyedit.plan import DEFAULT_PALETTE, make_plan


class MakePlanBadReplyTest(unittest.TestCase):
    def _plan_for(self, reply):
        with mock.patch.object(plan, "ask_json", return_value=reply):
            return make_plan("Test Movie", "none")

    def test_bad_palette_shapes_fall_back_to_defaults(self):
        # A string palette and a list palette used to raise TypeError.
        for bad in ("gold and black", ["#ffffff"]):
            result = self._plan_for({"speech_query": "x", "palette": bad})
            self.assertEqual(result["palette"], DEFAULT_PALETTE, f"palette={bad!r}")

        # Known keys with bad hex values are replaced, good ones are kept.
        result = self._plan_for({
            "speech_query": "x",
            "palette": {"positive": "red-ish", "negative": "#ff0000", "gold": "#GGGGGG", "extra": "#123456"},
        })
        expected = dict(DEFAULT_PALETTE, negative="#ff0000")
        self.assertEqual(result["palette"], expected)

    def test_non_string_montage_queries_are_dropped(self):
        result = self._plan_for({"speech_query": "x", "montage_queries": [1, None, "good query"]})
        self.assertEqual(result["montage_queries"], ["good query"])

        result = self._plan_for({"speech_query": "x", "montage_queries": [1, None]})
        self.assertEqual(result["montage_queries"], ["Test Movie best scenes", "Test Movie trailer 4k"])


if __name__ == "__main__":
    unittest.main()
