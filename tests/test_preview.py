"""--preview range validation, from the scenarios in issue #6."""
import unittest

from easyedit.__main__ import parse, preview_range


class ParsePreviewTest(unittest.TestCase):
    def test_preview_is_a_float_pair(self):
        a = parse(["Some Movie", "--preview", "12.5", "30"])
        self.assertEqual(a.preview, [12.5, 30.0])
        self.assertIsNone(parse(["Some Movie"]).preview)


class PreviewRangeTest(unittest.TestCase):
    def test_reversed_range_is_rejected(self):
        with self.assertRaises(SystemExit):
            preview_range((25.0, 15.0))

    def test_negative_start_is_rejected(self):
        with self.assertRaises(SystemExit):
            preview_range((-1.0, 10.0))

    def test_empty_range_is_rejected(self):
        with self.assertRaises(SystemExit):
            preview_range((5.0, 5.0))

    def test_start_past_the_edit_is_rejected(self):
        with self.assertRaises(SystemExit):
            preview_range((60.5, 62.0), frames=3600, fps=60)

    def test_end_is_clamped_to_the_edit_length(self):
        self.assertEqual(preview_range((10.0, 90.0), frames=3600, fps=60), (10.0, 60.0))

    def test_valid_range_passes_through(self):
        self.assertEqual(preview_range((10.0, 25.0), frames=3600, fps=60), (10.0, 25.0))
        self.assertEqual(preview_range((0.0, 3.5)), (0.0, 3.5))


if __name__ == "__main__":
    unittest.main()
