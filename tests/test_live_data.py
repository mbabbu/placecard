"""Live-Qloo behaviour exercised with responses recorded from the real API (tests/fixtures), no network:
resolution (hints, subtitles, accents, venues), vectors, the quota guard, shown tags and the live presets."""
import json
import os
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from placecard import presets, qloo, seating, server, tools
from placecard.tools import ToolBox

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf8") as f:
        return json.load(f)


class Resp:
    def __init__(self, obj):
        self.raw = json.dumps(obj).encode()
        self.headers = {"X-Month-RateLimit-Remaining": "9000", "X-Month-RateLimit-Limit": "10000"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return self.raw


class Router:
    """Fake urlopen that answers like the hackathon API from the recorded fixtures."""

    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=0):
        u = urllib.parse.urlsplit(req.full_url)
        p = dict(urllib.parse.parse_qsl(u.query))
        self.requests.append((u.path, p))
        if u.path == "/search":
            if p["query"].lower() == "parasite" and "videogame" not in p["types"].replace("urn:entity:videogame,", ""):
                return Resp(fixture("search_parasite.json"))
            return Resp({"results": []})
        if u.path == "/v2/insights":
            return Resp(fixture("insights_taste_mixed.json"))
        raise AssertionError(u.path)


def live_box(guests, **kw):
    router = Router()
    client = qloo.LiveQloo("K", opener=router, sleep=lambda s: None, rps=0)
    return ToolBox(client, guests, table_size=4, workers=1, **kw), client, router


class Profiles(unittest.TestCase):
    def test_profile_from_recorded_responses(self):
        b, client, router = live_box([{"name": "A", "favorites": ["Parasite"]}])
        out, err = b.run("build_taste_profiles", {})
        self.assertFalse(err, out)
        prof = b.profiles["A"]
        ent = prof["entities"][0]
        self.assertEqual((ent["name"], ent["type"]), ("Parasite", "movie"))
        self.assertEqual(ent["tag_ids"][0], "urn:tag:theme:qloo:privilege")
        vec = prof["vector"]
        self.assertIn("urn:tag:theme:qloo:privilege", vec)                      # the favourite's own tag, blended in
        self.assertEqual(vec["urn:tag:theme:qloo:privilege"]["fav"], 1)
        self.assertTrue(any("fav" not in v for v in vec.values()), "taste-analysis tags carry no favourite")
        self.assertFalse([t for t in vec if qloo.tag_kind(t) == "noise"])
        self.assertEqual([r[0] for r in router.requests], ["/search", "/v2/insights"])
        self.assertEqual(client.calls, 2)

    def test_own_tag_blend_can_be_switched_off(self):
        with mock.patch.object(qloo.config, "OWN_TAG_WEIGHT", 0.0):
            b, *_ = live_box([{"name": "A", "favorites": ["Parasite"]}])
            b.run("build_taste_profiles", {})
        self.assertTrue(all("fav" not in v for v in b.profiles["A"]["vector"].values()))

    def test_hint_restricts_the_search_type_and_wrong_hint_falls_back(self):
        b, _client, router = live_box([{"name": "A", "favorites": ["Parasite (movie)", "Zzz Nothing (game)"]}])
        b.run("build_taste_profiles", {})
        searches = [p for path, p in router.requests if path == "/search"]
        self.assertEqual(searches[0]["types"], "urn:entity:movie")
        self.assertEqual(searches[0]["query"], "Parasite")                       # the hint is not sent as text
        self.assertEqual(searches[1]["types"], "urn:entity:videogame")           # tried with the hint ...
        self.assertEqual(searches[2]["types"].count("urn:entity:"), 9)           # ... then without it
        self.assertEqual(b.profiles["A"]["unmatched"], ["Zzz Nothing (game)"])

    def test_monthly_quota_reserve_stops_requests(self):
        b, client, _ = live_box([{"name": "A", "favorites": ["Parasite"]}])
        client.month_remaining = 5
        out, err = b.run("build_taste_profiles", {})
        self.assertFalse(err)
        self.assertNotIn("A", b.profiles)                                          # nothing was fetched for A
        self.assertIn("budget", out["note"])
        self.assertEqual(client.calls, 0)

    def test_call_budget_is_still_enforced(self):
        b, client, _ = live_box([{"name": "A", "favorites": ["Parasite"]}], max_qloo_calls=1)
        b.run("build_taste_profiles", {})
        self.assertEqual(client.calls, 1)                                          # the search, then the budget stopped it


class Choosing(unittest.TestCase):
    def hit(self, name, typ, pop, year=None, dis=""):
        return {"name": name, "type": typ, "popularity": pop, "year": year, "disambiguation": dis, "id": name + typ}

    def test_exact_name_beats_a_more_popular_venue(self):
        notes = []
        hits = [self.hit("Dune Cafe", "place", 1.0), self.hit("Dune", "artist", 0.9), self.hit("Dune", "place", 1.0)]
        self.assertEqual(ToolBox._choose("Dune", hits, notes)["type"], "artist")   # a venue is discounted

    def test_subtitle_match_and_no_note_for_it(self):
        notes = []
        hits = [self.hit("Lagaan", "place", 0.9), self.hit("Lagaan: Once Upon a Time in India", "movie", 0.98)]
        # exact name 'Lagaan' exists only as a venue in another country; the film is found by its base title
        pick = ToolBox._choose("Lagaan", hits[1:], notes)
        self.assertEqual(pick["type"], "movie")
        self.assertEqual(notes, [])

    def test_accents_are_ignored(self):
        self.assertEqual(tools._norm("Beyoncé"), tools._norm("BEYONCE"))
        hits = [self.hit("Beyoncé", "artist", 1.0)]
        notes = []
        self.assertEqual(ToolBox._choose("Beyonce", hits, notes)["name"], "Beyoncé")
        self.assertEqual(notes, [])                                                # exact after accent folding

    def test_ambiguity_note_only_for_works_with_the_same_title(self):
        notes = []
        film = self.hit("Oldboy", "movie", 1.0, 2003)
        remake = self.hit("Oldboy", "movie", 0.97, 2013)
        band = self.hit("Oldboy", "artist", 0.99)
        ToolBox._choose("Oldboy", [film, remake, band], notes)
        self.assertEqual(len(notes), 1)
        self.assertIn("2003", notes[0])
        self.assertIn("2013", notes[0])
        self.assertNotIn("artist", notes[0])
        notes = []
        ToolBox._choose("Oldboy", [film, self.hit("Oldboy", "movie", 0.2, 1999)], notes)   # an obscure namesake
        self.assertEqual(notes, [])

    def test_html_entities_and_stray_spaces_are_cleaned(self):
        e = qloo._norm_entity({"entity_id": "E", "name": "Atomic Habits: Build &amp; Break", "types": ["urn:entity:book"],
                               "tags": [{"name": " indie ", "tag_id": "urn:tag:genre:music:indie"}]})
        self.assertEqual(e["name"], "Atomic Habits: Build & Break")
        self.assertEqual(e["tags"], ["indie"])


class ShownTags(unittest.TestCase):
    def vec(self, **tags):
        return {t: dict(v) for t, v in tags.items()}

    def test_only_topic_tags_backed_by_a_favourite_are_shown_but_all_count_for_compatibility(self):
        mk = lambda *t: {k: {"name": k.split(":")[-1], "affinity": 1.0, "kind": "topic", "fav": 1} for k in t}
        a = mk("urn:tag:genre:x:a", "urn:tag:genre:x:b")
        b = mk("urn:tag:genre:x:a", "urn:tag:genre:x:b")
        # a taste-analysis tag nobody's favourite carries, and a mood tag: counted in similarity, never shown
        for v in (a, b):
            v["urn:tag:genre:x:inferred"] = {"name": "inferred", "affinity": 1.0, "kind": "topic"}
            v["urn:tag:style:q:mood"] = {"name": "mood", "affinity": 1.0, "kind": "mood", "fav": 1}
        plan = seating.seat(["A", "B"], {"A": a, "B": b}, 2)
        shown = {s["tag_id"] for s in plan["tables"][0]["shared_tags"]}
        self.assertEqual(shown, {"urn:tag:genre:x:a", "urn:tag:genre:x:b"})
        self.assertGreater(plan["guests"]["A"]["fit"], 0.55)                       # identical vectors: top of the scale

    def test_sample_vectors_without_markers_show_everything(self):
        v = {"t1": {"name": "T1", "affinity": 1}, "t2": {"name": "T2", "affinity": 1}}
        plan = seating.seat(["A", "B"], {"A": v, "B": v}, 2)
        self.assertEqual({s["tag_id"] for s in plan["tables"][0]["shared_tags"]}, {"t1", "t2"})

    def test_spelling_variants_of_one_tag_are_shown_once(self):
        v = {"urn:tag:genre:music:k_pop": {"name": "k pop", "affinity": 1.0, "kind": "topic", "fav": 1},
             "urn:tag:genre:music:kpop": {"name": "Kpop", "affinity": 0.9, "kind": "topic", "fav": 1},
             "urn:tag:genre:music:ballad": {"name": "Ballad", "affinity": 0.8, "kind": "topic", "fav": 1}}
        plan = seating.seat(["A", "B"], {"A": v, "B": v}, 2)
        names = [v[s["tag_id"]]["name"] for s in plan["tables"][0]["shared_tags"]]
        self.assertEqual(names, ["k pop", "Ballad"])

    def test_a_card_may_not_borrow_another_tables_tag_but_everyday_words_are_fine(self):
        guests = [{"name": n, "favorites": []} for n in ("A", "B", "C", "D")]
        b = ToolBox(qloo=None, guests=guests, table_size=2)
        mkv = lambda tid, name: {tid: {"name": name, "affinity": 1.0, "kind": "topic", "fav": 1}}
        b.profiles = {n: {"entities": [{"id": n, "name": f"Fav {n}", "tags": [t], "tag_ids": [tid]}], "vector": mkv(tid, t),
                          "unmatched": [], "assumptions": [], "status": "ok"}
                      for n, tid, t in (("A", "urn:tag:genre:g:jazz", "Jazz"), ("B", "urn:tag:genre:g:jazz", "Jazz"),
                                        ("C", "urn:tag:genre:g:anime", "Anime"), ("D", "urn:tag:genre:g:anime", "Anime"))}
        for n, p in b.profiles.items():
            b.tag_names.update({k: v["name"] for k, v in p["vector"].items()})
        b.stale = True
        out, err = b.run("seat_guests", {})
        self.assertFalse(err, out)
        t_jazz = next(t for t in b.plan["tables"] if "A" in t["guests"])
        topics = [{"tag": "Jazz", "because": "A and B share it", "entities": ["Fav A"], "guests": ["A", "B"]}]
        call = lambda opener: b.run("table_card", {"table": t_jazz["index"], "opener": opener, "topics": topics,
                                                   "ask": "What is your favourite?"})
        self.assertFalse(call("You all enjoy Jazz and a good laugh over dinner.")[1])        # everyday word 'dinner'
        out, err = call("You all enjoy Jazz, and Anime too.")                                # another table's tag
        self.assertTrue(err)
        self.assertIn("Anime", out["error"])

    def _three_tables(self):
        guests = [{"name": n, "favorites": []} for n in "ABCDEF"]
        b = ToolBox(qloo=None, guests=guests, table_size=2)
        pairs = {"A": "jazz", "B": "jazz", "C": "anime", "D": "anime", "E": "chess", "F": "chess"}
        b.profiles = {}
        for n, kind in pairs.items():
            vec = {f"urn:tag:genre:g:{kind}": {"name": kind.title(), "affinity": 1.0, "kind": "topic", "fav": 1},
                   "urn:tag:theme:q:love": {"name": "Love", "affinity": 0.2, "kind": "topic", "fav": 1}}
            b.profiles[n] = {"entities": [{"id": n, "name": f"Fav {n}", "tags": [kind.title(), "Love"],
                                           "tag_ids": [f"urn:tag:genre:g:{kind}", "urn:tag:theme:q:love"]}],
                             "vector": vec, "unmatched": [], "assumptions": [], "status": "ok"}
            b.tag_names.update({k: v["name"] for k, v in vec.items()})
        b.stale = True
        ok, err = b.run("seat_guests", {})
        self.assertFalse(err, ok)
        return b

    def test_everyday_tags_shown_on_many_tables_are_not_policed_and_own_tags_may_be_named(self):
        b = self._three_tables()
        t = next(x for x in b.plan["tables"] if "A" in x["guests"])
        topics = [{"tag": "Jazz", "because": "A and B share it", "entities": ["Fav A"], "guests": ["A", "B"]}]
        card = lambda opener: b.run("table_card", {"table": t["index"], "opener": opener, "topics": topics,
                                                   "ask": "What is your favourite?"})
        self.assertEqual({s["tag_id"].split(":")[-1] for s in t["shared_tags"]} - {"love"}, {"jazz"})
        out, err = card("You all love a good Jazz night, and Love is a theme at every table.")
        self.assertFalse(err, out)                      # 'Love' is shown on all three tables: too common to be a claim
        out, err = card("You all enjoy Jazz, and Anime or Chess too.")
        self.assertTrue(err)                            # 'Anime' is shown on exactly one other table
        self.assertIn("Anime", out["error"])


class LivePresets(unittest.TestCase):
    def test_sizes_names_and_favourite_counts(self):
        p = presets.LIVE_PRESETS
        self.assertEqual((len(p["wedding"]["guests"]), len(p["conference"]["guests"]), len(p["mystery"]["guests"])),
                         (40, 24, 6))
        for key, preset in p.items():
            names = [g["name"] for g in preset["guests"]]
            self.assertEqual(len(names), len(set(names)), key)
            for g in preset["guests"]:
                if key != "mystery":
                    self.assertTrue(3 <= len(g["favorites"]) <= 4, g)
                self.assertEqual(len(g["favorites"]), len(set(g["favorites"])), g)

    def test_every_favourite_comes_from_a_pool_and_hints_are_valid(self):
        pool = {f for v in presets.LIVE_POOLS.values() for f in v}
        for key in ("wedding", "conference"):
            for g in presets.LIVE_PRESETS[key]["guests"]:
                for f in g["favorites"]:
                    self.assertIn(f, pool)
        for f in pool:
            title, hint = qloo.split_hint(f)
            self.assertTrue(title)
            if "(" in f:
                self.assertIsNotNone(hint, f"unrecognised hint in {f!r}")

    def test_malaysian_and_asian_favourites_are_well_represented(self):
        asian = set()
        for code in "KJMDZS":
            asian.update(presets.LIVE_POOLS[code])
        favs = [f for key in ("wedding", "conference") for g in presets.LIVE_PRESETS[key]["guests"]
                for f in g["favorites"]]
        share = sum(1 for f in favs if f in asian) / len(favs)
        self.assertGreater(share, 0.25)
        malaysian = set(presets.LIVE_POOLS["M"])
        self.assertGreaterEqual(len([f for f in favs if f in malaysian]), 10)

    def test_the_requirements_name_real_guests(self):
        for key in ("wedding", "conference"):
            names = {g["name"] for g in presets.LIVE_PRESETS[key]["guests"]}
            text = presets.LIVE_PRESETS[key]["constraints"]
            self.assertTrue(any(n in text for n in names))
            self.assertEqual(text, presets.PRESETS[key]["constraints"])     # the same requirements as the SAMPLE lists

    def test_for_mode(self):
        self.assertIs(presets.for_mode(False), presets.PRESETS)
        self.assertIs(presets.for_mode(True), presets.LIVE_PRESETS)

    def test_sample_presets_are_untouched(self):
        self.assertEqual(presets.PRESETS["wedding"]["guests"][0]["favorites"],
                         presets._build("wedding", presets.WEDDING)[0]["favorites"])


class ServerModes(unittest.TestCase):
    def serve(self, env):
        patcher = mock.patch.dict(os.environ, dict(env, LLM_PROVIDER="mock"))
        patcher.start()
        self.addCleanup(patcher.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(self, base, path):
        with urllib.request.urlopen(base + path) as r:
            return json.loads(r.read())

    def test_live_mode_offers_the_live_presets_and_reports_the_quota_without_calling_qloo(self):
        base = self.serve({"QLOO_API_KEY": "not-a-real-key"})
        self.assertEqual(self.get(base, "/api/presets")["wedding"]["title"], "Wedding (40 guests)")
        self.assertEqual(self.get(base, "/api/health")["qloo"], "live")
        client = qloo.LiveQloo("not-a-real-key", opener=mock.Mock(side_effect=AssertionError("no network")), rps=0)
        client.month_remaining, client.month_limit = 8123, 10000
        with mock.patch.dict(qloo._SHARED, {"client": client}):
            h = self.get(base, "/api/health")
        self.assertEqual(h["qloo_quota"], {"remaining": 8123, "limit": 10000})
        self.assertNotIn("not-a-real-key", json.dumps(h))

    def test_sample_mode_keeps_the_sample_presets_and_label(self):
        base = self.serve({"QLOO_API_KEY": ""})
        self.assertEqual(self.get(base, "/api/presets")["wedding"]["title"], "Sample wedding (40 guests)")
        h = self.get(base, "/api/health")
        self.assertEqual((h["qloo"], h["sample_data"]), ("sample", True))
        self.assertNotIn("qloo_quota", h)


if __name__ == "__main__":
    unittest.main()
