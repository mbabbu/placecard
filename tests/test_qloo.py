"""Qloo client: request shape, auth header, normalisation, and 401/403/404/429/5xx handling."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse

from placecard import qloo

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf8") as f:
        return json.load(f)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def http_error(code, body=b"{}", headers=None):
    return urllib.error.HTTPError("https://hackathon.api.qloo.com/x", code, "err", headers or {}, io.BytesIO(body))


class Recorder:
    """Fake urlopen: records requests and returns queued responses or raises queued errors."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, req, timeout=0):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResp(json.dumps(r).encode())


def params(req):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))


TASTE = {"success": True, "results": {"tags": [
    {"tag_id": "urn:tag:keyword:media:korean_cinema", "name": "Korean Cinema", "subtype": "urn:tag:keyword:media",
     "types": ["urn:entity:movie"], "query": {"affinity": 0.91}},
    {"tag_id": "urn:tag:genre:media:thriller", "name": "Thriller", "subtype": "urn:tag:genre:media",
     "query": {"affinity": 0.62}},
    {"tag_id": "urn:tag:genre:media:drama", "name": "Drama", "query": {"affinity": 0}}]}}


class RequestShape(unittest.TestCase):
    def client(self, *responses):
        rec = Recorder(*responses)
        return qloo.LiveQloo("SECRET", "https://hackathon.api.qloo.com", opener=rec, sleep=lambda s: None,
                             rps=0), rec

    def test_taste_analysis_get_with_header_and_params(self):
        c, rec = self.client(TASTE)
        vec = c.taste_tags(["B-2", "A-1"], take=30)
        req = rec.requests[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.startswith("https://hackathon.api.qloo.com/v2/insights?"))
        self.assertEqual(req.get_header("X-api-key"), "SECRET")  # urllib capitalises header names
        self.assertNotIn("SECRET", req.full_url)  # key never in the query string
        self.assertEqual(req.get_header("User-agent"), "Placecard/1.0")
        p = params(req)
        self.assertEqual(p["filter.type"], "urn:tag")
        self.assertEqual(p["signal.interests.entities"], "A-1,B-2")   # sorted: cache is order-insensitive
        self.assertEqual(p["take"], "30")
        self.assertNotIn("filter.tag.types", p)                        # empty params are dropped
        self.assertEqual(vec, {"urn:tag:keyword:media:korean_cinema": {"name": "Korean Cinema", "affinity": 0.91,
                                                                       "kind": "topic"},
                               "urn:tag:genre:media:thriller": {"name": "Thriller", "affinity": 0.62, "kind": "topic"}})

    def test_taste_tags_take_is_clamped_and_tag_types_forwarded(self):
        c, rec = self.client(TASTE)
        c.taste_tags(["A"], take=500, tag_types=["urn:tag:genre:media"])
        p = params(rec.requests[0])
        self.assertEqual((p["take"], p["filter.tag.types"]), ("50", "urn:tag:genre:media"))

    def test_taste_tags_without_entities_makes_no_request(self):
        c, rec = self.client()
        self.assertEqual(c.taste_tags([]), {})
        self.assertEqual(rec.requests, [])

    def test_rank_fallback_when_tags_have_no_affinity(self):
        c, _ = self.client({"results": {"tags": [{"tag_id": "t1", "name": "One"}, {"tag_id": "t2", "name": "Two"},
                                                  {"tag_id": "t3", "name": "Three"}]}})
        vec = c.taste_tags(["A"])
        self.assertEqual(list(vec), ["t1", "t2", "t3"])
        self.assertEqual(vec["t1"]["affinity"], 1.0)
        self.assertLess(vec["t3"]["affinity"], vec["t2"]["affinity"])
        self.assertGreater(vec["t3"]["affinity"], 0)

    def test_search_shape_types_and_404_means_empty(self):
        c, rec = self.client({"results": [{"entity_id": "E1", "name": "Parasite", "types": ["urn:entity:movie"],
                                           "disambiguation": "2019", "tags": [{"name": "Thriller"}]}]},
                             http_error(404, b'{"code":404,"reason":"No results"}'))
        hits = c.search("Parasite", "video_game")
        p = params(rec.requests[0])
        self.assertEqual(rec.requests[0].full_url.split("?")[0], "https://hackathon.api.qloo.com/search")
        self.assertEqual((p["query"], p["types"]), ("Parasite", "urn:entity:videogame"))
        self.assertEqual(hits[0]["type"], "movie")
        self.assertEqual(hits[0]["tags"], ["Thriller"])
        self.assertEqual(c.search("zzzz"), [])

    def test_cache_avoids_repeat_calls(self):
        c, rec = self.client(TASTE)
        c.taste_tags(["A", "B"])
        c.taste_tags(["B", "A"])
        self.assertEqual(len(rec.requests), 1)
        self.assertEqual(c.calls, 1)

    def test_get_client_is_shared_so_the_cache_persists(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"QLOO_API_KEY": "k1"}):
            a, b = qloo.get_client(), qloo.get_client()
            self.assertIs(a, b)
        with mock.patch.dict(os.environ, {"QLOO_API_KEY": ""}):
            self.assertTrue(qloo.get_client().sample)


class Errors(unittest.TestCase):
    def client(self, *responses):
        self.slept = []
        rec = Recorder(*responses)
        return qloo.LiveQloo("K", opener=rec, sleep=self.slept.append, rps=0), rec

    def test_401_raises_auth_error_without_retry(self):
        c, rec = self.client(http_error(401, b'{"message":"No API key found in request"}'))
        with self.assertRaises(qloo.QlooAuthError) as cm:
            c.taste_tags(["A"])
        self.assertIn("hackathon.api.qloo.com", str(cm.exception))
        self.assertEqual(len(rec.requests), 1)

    def test_429_retries_twice_then_raises_rate_limited(self):
        c, rec = self.client(http_error(429, headers={"Retry-After": "7"}), http_error(429), http_error(429))
        with self.assertRaises(qloo.QlooRateLimited) as cm:
            c.search("x")
        self.assertTrue(cm.exception.retryable)
        self.assertEqual(len(rec.requests), 3)
        self.assertEqual(self.slept, [2.5, 2.0])  # Retry-After honoured but capped; then back off

    def test_429_with_empty_monthly_quota_is_not_retried(self):
        c, rec = self.client(http_error(429, headers={"X-Month-RateLimit-Remaining": "0"}))
        with self.assertRaises(qloo.QlooQuotaError) as cm:
            c.search("x")
        self.assertFalse(cm.exception.retryable)
        self.assertEqual(len(rec.requests), 1)

    def test_429_then_success(self):
        c, rec = self.client(http_error(429), TASTE)
        self.assertEqual(len(c.taste_tags(["A"])), 2)

    def test_403_and_400_surface_message(self):
        c, _ = self.client(http_error(403, b'{"message":"You do not have permission to access urn:entity:x"}'))
        with self.assertRaises(qloo.QlooError) as cm:
            c.taste_tags(["A"])
        self.assertIn("permission", str(cm.exception))
        c, _ = self.client(http_error(400, b'{"message":"at least one valid signal or filter is required"}'))
        with self.assertRaises(qloo.QlooError) as cm:
            c.taste_tags(["A"])
        self.assertIn("valid signal", str(cm.exception))
        self.assertFalse(cm.exception.retryable)

    def test_500_retries_then_retryable_error(self):
        c, rec = self.client(http_error(503), http_error(503), http_error(503))
        with self.assertRaises(qloo.QlooError) as cm:
            c.taste_tags(["x"])
        self.assertTrue(cm.exception.retryable)
        self.assertEqual(len(rec.requests), 3)

    def test_network_error(self):
        c, _ = self.client(urllib.error.URLError("dns"), urllib.error.URLError("dns"))
        with self.assertRaises(qloo.QlooError):
            c.search("x")


class LiveShapes(unittest.TestCase):
    """Trimmed responses recorded from the live hackathon API (tests/fixtures/*.json)."""

    def client(self, *responses, **kw):
        rec = Recorder(*responses)
        return qloo.LiveQloo("K", opener=rec, sleep=lambda s: None, rps=0, **kw), rec

    def test_search_parasite_real_shape(self):
        c, rec = self.client(fixture("search_parasite.json"))
        hits = c.search("Parasite", None, take=3)
        p = params(rec.requests[0])
        self.assertEqual(p["types"].count("urn:entity:"), 9)           # the culture types, comma separated
        self.assertIn("urn:entity:videogame", p["types"])             # /search spelling, not video_game
        self.assertNotIn("urn:entity:video_game", p["types"])
        self.assertEqual(hits[0]["id"], "A499EC25-7FF6-46DE-9DA5-3E78E99B9E26")
        self.assertEqual((hits[0]["type"], hits[0]["year"], hits[0]["disambiguation"]), ("movie", 2019, "2019"))
        self.assertGreater(hits[0]["popularity"], 0.9)
        self.assertEqual(hits[0]["tags"][0], "Privilege")
        self.assertEqual(hits[0]["tag_ids"][0], "urn:tag:theme:qloo:privilege")
        self.assertEqual((hits[1]["type"], hits[2]["type"]), ("artist", "tv_show"))   # same name, other domains
        self.assertTrue(hits[0]["image"].startswith("https://images.qloo.com/"))

    def test_uuid_tag_ids_fall_back_to_their_urn_value(self):
        e = qloo._norm_entity({"entity_id": "E", "name": "X", "types": ["urn:entity:artist"], "tags": [
            {"name": "metal", "tag_id": "D14CDC1B-60A6-4EF7-BC1B-8D1987476042", "type": "urn:tag:genre",
             "value": "urn:tag:genre:metal"}, {"name": "Rock", "tag_id": "urn:tag:genre:rock"}]})
        self.assertEqual(e["tag_ids"], ["urn:tag:genre:metal", "urn:tag:genre:rock"])

    def test_insights_taste_analysis_real_shape(self):
        c, rec = self.client(fixture("insights_taste_mixed.json"))
        vec = c.taste_tags(["A", "B", "C"])
        p = params(rec.requests[0])
        self.assertEqual((p["filter.type"], p["take"]), ("urn:tag", "50"))
        raw = fixture("insights_taste_mixed.json")["results"]["tags"]
        noise = [t for t in raw if qloo.tag_kind(t["tag_id"]) == "noise"]
        self.assertTrue(noise, "the fixture should contain venue-metadata tags")
        for t in noise:
            self.assertNotIn(t["tag_id"], vec)                        # dropped
        kept = [t for t in raw if qloo.tag_kind(t["tag_id"]) != "noise"]
        self.assertEqual(set(vec), {t["tag_id"] for t in kept})
        for t in kept:
            factor = qloo.MOOD_WEIGHT if qloo.tag_kind(t["tag_id"]) == "mood" else 1.0   # mood tags count half
            self.assertAlmostEqual(vec[t["tag_id"]]["affinity"], round(t["query"]["affinity"] * factor, 4), places=4)
            self.assertEqual(vec[t["tag_id"]]["kind"], qloo.tag_kind(t["tag_id"]))
        self.assertTrue(all(0 < v["affinity"] <= 1 for v in vec.values()))

    def test_bad_search_type_error_body_is_surfaced(self):
        body = json.dumps(fixture("search_bad_type_400.json")).encode()
        c, _ = self.client(http_error(400, body))
        with self.assertRaises(qloo.QlooError) as cm:
            c.search("Elden Ring", "video_game")
        self.assertIn("allowed values", str(cm.exception))
        self.assertEqual(cm.exception.status, 400)

    def test_month_quota_is_read_from_headers(self):
        h = fixture("rate_headers.json")
        self.assertEqual(h["X-Second-RateLimit-Limit"], "5")

        class Resp(FakeResp):
            headers = h

        class Op:
            def __call__(self, req, timeout=0):
                return Resp(json.dumps(fixture("insights_taste_mixed.json")).encode())

        c = qloo.LiveQloo("K", opener=Op(), sleep=lambda s: None, rps=0)
        self.assertEqual(c.quota(), {"remaining": None, "limit": None})
        c.taste_tags(["A"])
        self.assertEqual(c.quota(), {"remaining": int(h["X-Month-RateLimit-Remaining"]), "limit": 10000})


class Hints(unittest.TestCase):
    def test_split_hint(self):
        for text, want in [("Hades (game)", ("Hades", "video_game")), ("Dune (book)", ("Dune", "book")),
                           ("Mayday (Band)", ("Mayday", "artist")), ("The Raid (film)", ("The Raid", "movie")),
                           ("Kyoto (city)", ("Kyoto", "destination")),
                           ("Spider-Man (2002)", ("Spider-Man (2002)", None)),
                           ("Mamma Mia!", ("Mamma Mia!", None)), ("(game)", ("(game)", None))]:
            self.assertEqual(qloo.split_hint(text), want, text)

    def test_hinted_search_uses_one_type(self):
        rec = Recorder({"results": []})
        c = qloo.LiveQloo("K", opener=rec, sleep=lambda s: None, rps=0)
        c.search("Hades", "video_game")
        self.assertEqual(params(rec.requests[0])["types"], "urn:entity:videogame")


class Throttle(unittest.TestCase):
    def test_requests_are_spaced_under_the_rate_limit(self):
        slept = []
        t = qloo._Throttle(0.25, slept.append, clock=lambda: 100.0)   # a frozen clock: every call arrives at once
        for _ in range(4):
            t.wait()
        self.assertEqual(slept, [0.25, 0.5, 0.75])                      # the first goes at once, then one per 0.25 s

    def test_disabled_throttle_never_sleeps(self):
        slept = []
        t = qloo._Throttle(0, slept.append)
        for _ in range(5):
            t.wait()
        self.assertEqual(slept, [])

    def test_default_rate_is_below_the_keys_five_per_second(self):
        from placecard import config
        self.assertLess(config.QLOO_RPS, 5)


class BlendAndCache(unittest.TestCase):
    ENTS = [{"tags": ["Korean", "Thriller"], "tag_ids": ["t:korean", "t:thriller"]},
            {"tags": ["Korean", "K Pop"], "tag_ids": ["t:korean", "t:kpop"]}]

    def test_blend_adds_own_tags_by_share_of_favourites(self):
        vec = {"t:thriller": {"name": "Thriller", "affinity": 0.9}, "t:x": {"name": "X", "affinity": 1.0}}
        out = qloo.blend_own_tags(vec, self.ENTS, weight=1.0)
        self.assertEqual(out["t:korean"], {"name": "Korean", "affinity": 1.0, "kind": "topic", "own": True, "fav": 2})
        self.assertEqual(out["t:kpop"]["affinity"], 0.5)                                      # one of two
        self.assertEqual((out["t:thriller"]["affinity"], out["t:thriller"]["fav"]), (1.4, 1))   # 0.9 + 0.5
        self.assertEqual(out["t:x"], {"name": "X", "affinity": 1.0})
        self.assertEqual(vec["t:thriller"]["affinity"], 0.9)                                  # input not modified

    def test_mood_tags_count_half_and_noise_tags_are_skipped(self):
        ents = [{"tags": ["Dark", "Genre", "VISA"], "tag_ids": ["urn:tag:style:qloo:dark", "urn:tag:genre:drama",
                                                              "urn:tag:credit_card:place:visa"]}]
        out = qloo.blend_own_tags({}, ents, weight=1.0)
        self.assertEqual(out["urn:tag:style:qloo:dark"]["affinity"], 0.5)
        self.assertEqual(out["urn:tag:style:qloo:dark"]["kind"], "mood")
        self.assertEqual(out["urn:tag:genre:drama"]["affinity"], 1.0)
        self.assertNotIn("urn:tag:credit_card:place:visa", out)

    def test_tag_kinds(self):
        self.assertEqual(qloo.tag_kind("urn:tag:genre:media:drama"), "topic")
        self.assertEqual(qloo.tag_kind("urn:tag:keyword:qloo:wealth_disparity"), "topic")
        self.assertEqual(qloo.tag_kind("urn:tag:style:qloo:polished"), "mood")
        self.assertEqual(qloo.tag_kind("urn:tag:credit_card:place:jcb"), "noise")
        self.assertEqual(qloo.tag_kind("urn:tag:something_new:qloo:x"), "mood")
        self.assertEqual(qloo.tag_kind("urn:tag:sample:drama"), "mood")      # unknown category, not a hand-written id
        self.assertEqual(qloo.tag_kind("t1"), "topic")

    def test_blend_is_off_at_zero_weight_and_for_sample_entities(self):
        vec = {"t:x": {"name": "X", "affinity": 1.0}}
        self.assertIs(qloo.blend_own_tags(vec, self.ENTS, weight=0), vec)
        self.assertIs(qloo.blend_own_tags(vec, [{"tags": ["A"]}], weight=1.0), vec)

    def test_cache_file_round_trip_saves_calls_after_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache.json")
            c1 = qloo.LiveQloo("K", opener=Recorder(fixture("insights_taste_mixed.json"),
                                                    fixture("search_parasite.json")),
                               sleep=lambda s: None, rps=0, cache_file=path)
            vec = c1.taste_tags(["A", "B"])
            hits = c1.search("Parasite")
            c1.save_cache()
            rec2 = Recorder()
            c2 = qloo.LiveQloo("K", opener=rec2, sleep=lambda s: None, rps=0, cache_file=path)
            self.assertEqual(c2.taste_tags(["B", "A"]), vec)
            self.assertEqual(c2.search("  parasite "), hits)
            self.assertEqual(rec2.requests, [])
            self.assertEqual(c2.calls, 0)


if __name__ == "__main__":
    unittest.main()
