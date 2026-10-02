"""Anime: telling an anime title from a film, naming its footage, and scoring uploads. No network."""
import unittest
from unittest import mock

from easyedit import anime, fetch


def _show(title, romaji, popularity, synonyms=()):
    return {"id": 1, "title": title, "romaji": romaji, "native": "", "synonyms": list(synonyms), "format": "TV",
            "year": 2019, "episodes": 26, "popularity": popularity, "cover": None, "color": None, "banner": None,
            "studio": "", "characters": []}


SLAYER = _show("Demon Slayer: Kimetsu no Yaiba", "Kimetsu no Yaiba", 999995, ["KnY"])


class MatchTest(unittest.TestCase):
    def _match(self, typed, found):
        with mock.patch.object(anime, "lookup", return_value=found):
            return anime.match(typed)

    def test_a_short_name_means_a_well_known_anime(self):
        self.assertIs(self._match("demon slayer", [SLAYER]), SLAYER)

    def test_a_film_is_not_an_obscure_anime_with_a_similar_name(self):
        self.assertIsNone(self._match("Interstellar", [_show("Interstella 5555", "Interstella 5555", 20648,
                                                             ["Interstellar 5555"])]))
        self.assertIsNone(self._match("Fight Club", []))

    def test_an_exact_title_needs_only_to_be_known(self):
        name = _show("Your Name.", "Kimi no Na wa.", 718430)
        self.assertIs(self._match("Your Name", [name]), name)
        self.assertIsNone(self._match("Onigiri", [_show("Onigiri", "Onigiri", 9038)]))


class NamingTest(unittest.TestCase):
    def test_footage_is_asked_for_the_way_editors_label_it(self):
        self.assertEqual(anime.short(SLAYER), "Demon Slayer")
        self.assertEqual(anime.footage_queries(SLAYER, "Zenitsu")[0], "Zenitsu Demon Slayer twixtor 4K")

    def test_sakugabooru_is_asked_by_the_romaji_name_first(self):
        self.assertEqual(anime.sakuga_tags(SLAYER)[0], "kimetsu_no_yaiba")
        season = _show("JUJUTSU KAISEN Season 2", "Jujutsu Kaisen 2nd Season", 500000)
        self.assertIn("jujutsu_kaisen", anime.sakuga_tags(season))


class ScoreTest(unittest.TestCase):
    def score(self, title):
        return fetch._title_score(title, "montage", anime.names(SLAYER), anime=True)

    def test_raw_footage_beats_finished_edits(self):
        for good in ("Demon Slayer Twixtor 4K CC - Tanjiro vs Rui", "Kimetsu no Yaiba scenepack 4K no subs",
                     "Demon Slayer raw clips for edits 1080p"):
            self.assertGreater(self.score(good), 0, good)
        for bad in ("Demon Slayer [AMV] - Royalty", "Tanjiro edit", "Demon Slayer reaction ep 19"):
            self.assertLess(self.score(bad), 0, bad)

    def test_films_are_scored_as_before(self):
        self.assertLess(fetch._title_score("Inception twixtor edit", "montage", "Inception"), 0)


if __name__ == "__main__":
    unittest.main()
