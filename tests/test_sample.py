"""The SAMPLE catalogue and presets are consistent, clearly labelled, and big enough for the demos."""
import unittest

from placecard import presets, sample_qloo as sq


class Catalogue(unittest.TestCase):
    def test_labelled_sample_and_ids(self):
        self.assertTrue(sq.SampleQloo.sample)
        self.assertTrue(all(c["id"].startswith("SAMPLE-") for c in sq.CATALOG))
        self.assertIn("NOT REAL QLOO", sq.__doc__)

    def test_all_tag_keys_are_defined(self):
        for c in sq.CATALOG:
            for k in c["tag_keys"]:
                self.assertIn(k, sq.TAG_NAMES, c["name"])

    def test_every_preset_favourite_resolves_except_the_mystery_ones(self):
        q = sq.SampleQloo()
        for key in ("wedding", "conference"):
            for g in presets.PRESETS[key]["guests"]:
                self.assertTrue(2 <= len(g["favorites"]) <= 4, g)
                for f in g["favorites"]:
                    self.assertTrue(q.search(f), f"{key}: {f}")
        miss = [f for g in presets.PRESETS["mystery"]["guests"] for f in g["favorites"] if not q.search(f)]
        self.assertEqual(len(miss), 3)

    def test_preset_sizes_and_unique_names(self):
        self.assertEqual(len(presets.PRESETS["wedding"]["guests"]), 40)
        self.assertEqual(len(presets.PRESETS["conference"]["guests"]), 24)
        for p in presets.PRESETS.values():
            names = [g["name"] for g in p["guests"]]
            self.assertEqual(len(names), len(set(names)))

    def test_search_prefers_exact_then_popular_and_flags_ambiguity(self):
        q = sq.SampleQloo()
        hits = q.search("Parasite")
        self.assertEqual([h["year"] for h in hits], [2019, 1982])
        self.assertEqual(q.search("zzzz"), [])
        self.assertEqual(q.search("Spirited Away", "book"), [])

    def test_taste_vector_is_deterministic_and_reflects_the_favourites(self):
        q = sq.SampleQloo()
        ids = [q.search(n)[0]["id"] for n in ("Parasite", "BTS")]
        v = q.taste_tags(ids)
        self.assertEqual(v, q.taste_tags(list(reversed(ids))))
        top = max(v.items(), key=lambda kv: kv[1]["affinity"])[1]["name"]
        self.assertEqual(top, "Korean Culture")
        self.assertTrue(all(0 < t["affinity"] <= 1 for t in v.values()))
        self.assertEqual(q.taste_tags([]), {})

    def test_presets_are_deterministic(self):
        import importlib
        again = importlib.reload(presets)
        self.assertEqual(again.PRESETS["wedding"]["guests"][5], presets.PRESETS["wedding"]["guests"][5])


if __name__ == "__main__":
    unittest.main()
