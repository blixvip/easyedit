import json
import unittest

from easyedit.llm import extract_json


class ExtractJsonTest(unittest.TestCase):
    def test_fenced_json_block(self):
        text = "Here is the result:\n```json\n{\"key\": \"value\"}\n```\nThanks."
        self.assertEqual(extract_json(text), {"key": "value"})

    def test_bare_object(self):
        text = '{"foo": 1, "bar": [2, 3]}'
        self.assertEqual(extract_json(text), {"foo": 1, "bar": [2, 3]})

    def test_leading_prose_preamble(self):
        text = "The model replied with this object: {\"answer\": true}"
        self.assertEqual(extract_json(text), {"answer": True})

    def test_top_level_array(self):
        text = "Results:\n[1, 2, {\"nested\": null}]"
        self.assertEqual(extract_json(text), [1, 2, {"nested": None}])

    def test_brace_in_prose_before_real_json(self):
        # First { is not valid JSON; current implementation starts decoding there.
        text = "See the example {not json} and then the real data: {\"ok\": 42}"
        with self.assertRaises(json.JSONDecodeError):
            extract_json(text)

    def test_trailing_text_after_object(self):
        text = '{"status": "done"} and that is all.'
        self.assertEqual(extract_json(text), {"status": "done"})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError) as cm:
            extract_json("There is no JSON here at all.")
        self.assertEqual(str(cm.exception), "no JSON in reply")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError) as cm:
            extract_json("")
        self.assertEqual(str(cm.exception), "no JSON in reply")


if __name__ == "__main__":
    unittest.main()
