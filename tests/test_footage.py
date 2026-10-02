"""The footage finder's own decisions: what a Wikipedia page is, and how clips are dealt. No network."""
import random
import unittest

from easyedit import footage


def _upload(vid: str, seconds: float) -> dict:
    return {"site": "youtube", "id": vid, "kind": "video", "title": vid, "duration": seconds}


class WikiKindTest(unittest.TestCase):
    def test_pages_are_told_apart(self):
        for title, desc, kind in (
                ("Blade Runner 2049", "2017 film by Denis Villeneuve", "movie"),
                ("Fargo (TV series)", "American crime anthology series", "tv"),
                ("Breaking Bad", "American crime drama TV series", "tv"),
                ("Walter White (Breaking Bad)", "Breaking Bad character", "character"),
                ("Blade Runner 2049 (soundtrack)", "2017 soundtrack album by Hans Zimmer", None),
                ("Fargo season 2", "2015 season of American crime anthology TV series", None),
                ("Jujutsu Kaisen", "Japanese manga series", None),
                ("List of Blade Runner characters", "", None)):
            self.assertEqual(footage._wiki_kind(title, desc), kind, title)

    def test_where_a_character_is_from(self):
        self.assertEqual(footage._wiki_of("Walter White (Breaking Bad)", "Breaking Bad character"), "Breaking Bad")
        self.assertEqual(footage._wiki_of("Satoru Gojo", "Fictional character from Jujutsu Kaisen"), "Jujutsu Kaisen")
        self.assertEqual(footage._wiki_of("John Wick (character)", "Protagonist of the John Wick franchise"), "John Wick")
        self.assertEqual(footage._wiki_of("Joker (character)", "Supervillain appearing in DC Comics"), "")


class MomentsTest(unittest.TestCase):
    def test_every_upload_is_dealt_before_any_twice(self):
        pool = [_upload(f"v{i:010d}", 300) for i in range(10)]
        dealt = footage.moments(pool, 25, 12, random.Random(1))
        self.assertEqual(len(dealt), 25)
        self.assertEqual(len({c["id"] for c in dealt[:10]}), 10)
        self.assertEqual(len({c["key"] for c in dealt}), 25)

    def test_moments_stay_inside_the_upload_and_off_its_ends(self):
        for c in footage.moments([_upload("a" * 11, 300)], 4, 12, random.Random(3)):
            self.assertGreaterEqual(c["start"], 15)
            self.assertLessEqual(c["start"] + c["length"], 300 - 18)
        short = footage.moments([_upload("b" * 11, 5)], 4, 12, random.Random(3))
        self.assertEqual([(c["start"], c["length"]) for c in short], [(0.0, 5.0)])

    def test_a_seed_deals_the_same_hand(self):
        pool = [_upload(f"v{i:010d}", 200 + i) for i in range(6)]
        keys = lambda seed: [c["key"] for c in footage.moments(pool, 12, 6, random.Random(seed))]  # noqa: E731
        self.assertEqual(keys(7), keys(7))
        self.assertNotEqual(keys(7), keys(8))

    def test_cuts_and_stills_are_mixed_in_but_do_not_take_over(self):
        pool = [_upload(f"v{i:010d}", 120) for i in range(4)]
        pool += [{"site": "filmgrab", "id": f"s{i}", "kind": "still", "title": "x"} for i in range(50)]
        dealt = footage.moments(pool, 20, 12, random.Random(2))
        self.assertEqual(sum(c["site"] == "filmgrab" for c in dealt), 4)


class CleanTest(unittest.TestCase):
    def test_a_subject_from_the_page_is_checked(self):
        with self.assertRaises(ValueError):
            footage._clean({"kind": "website", "title": "x"})
        s = footage._clean({"kind": "movie", "title": " Heat ", "image": "javascript:1", "anime": "7", "year": 1995})
        self.assertEqual((s["title"], s["image"], s["anime"], s["year"]), ("Heat", None, None, 1995))


if __name__ == "__main__":
    unittest.main()
