"""Qloo Taste AI client (hackathon environment). Everything Qloo-specific lives here.

Endpoints used (all GET, key in the X-Api-Key header, base https://hackathon.api.qloo.com):
  /search          entity lookup by name            -> results[] (entity_id, name, types[], popularity, tags[]...)
  /v2/insights     filter.type=urn:tag ("taste      -> results.tags[] (tag_id, name, subtype, query.affinity)
                   analysis"): the tags that characterise a set of favourites
                   (signal.interests.entities). This is each guest's taste vector.

Response shapes were recorded from the live hackathon API (see tests/fixtures/ and TUNING.md). Things the
live API taught us that the docs do not say:
  * /search `types` takes a comma list; its values differ from insights ("urn:entity:videogame", not video_game).
  * Rate limits are returned in headers: 5 requests per second and 10,000 per month on the hackathon key.
  * Insights `take` is capped at 50. Affinities are all close to 1 (0.99-1.0), so they barely discriminate;
    the tag SET carries the signal. Many tags are place metadata (credit cards, hotel stars) that say
    nothing about taste; they are dropped (NOISE_CATEGORIES, see tag_kind).
  * /search results carry the entity's own tags (up to ~300). Blended into the vector at a lower weight they
    sharpen the match noticeably (TUNING.md).
Normalisers are defensive: if a payload differs, fix it in _norm_entity/_norm_tag only.
Identical requests are cached (one client is shared by the whole server), so repeated favourites such as
"Parasite" cost one call, not one per guest. An optional JSON file (QLOO_CACHE_FILE) keeps the cache across
restarts so development and demos do not burn the monthly quota.
"""
import atexit
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

# Short names the agent uses -> Qloo insights filter.type URNs.
TYPES = {
    "place": "urn:entity:place",
    "movie": "urn:entity:movie",
    "artist": "urn:entity:artist",
    "tv_show": "urn:entity:tv_show",
    "book": "urn:entity:book",
    "podcast": "urn:entity:podcast",
    "video_game": "urn:entity:video_game",
    "brand": "urn:entity:brand",
    "destination": "urn:entity:destination",
}
# /search uses a slightly different type list (the 400 response lists the allowed values).
SEARCH_TYPES = dict(TYPES, video_game="urn:entity:videogame")
SHORT = {v: k for k, v in TYPES.items()}
SHORT["urn:entity:videogame"] = "video_game"
# What a guest's favourite can be. Searching only these keeps albums, actors, directors, localities and
# other people out of the way.
CULTURE_TYPES = ["movie", "tv_show", "artist", "book", "podcast", "video_game", "brand", "destination", "place"]

# Optional hint a guest (or the organiser) can put after a favourite: "Hades (game)", "Dune (book)".
HINT_WORDS = {
    "movie": "movie", "film": "movie", "tv": "tv_show", "tv show": "tv_show", "show": "tv_show", "series": "tv_show",
    "book": "book", "novel": "book", "artist": "artist", "band": "artist", "singer": "artist", "music": "artist",
    "musician": "artist", "game": "video_game", "video game": "video_game", "videogame": "video_game",
    "podcast": "podcast", "brand": "brand", "company": "brand", "place": "place", "restaurant": "place",
    "cafe": "place", "bar": "place", "destination": "destination", "city": "destination", "country": "destination",
    "travel": "destination",
}
_HINT_RE = re.compile(r"^(.*\S)\s*\(\s*([A-Za-z ]{2,12}?)\s*\)\s*$")


def split_hint(fav):
    """'Hades (game)' -> ('Hades', 'video_game'); anything else -> (fav, None)."""
    m = _HINT_RE.match(str(fav).strip())
    if m and m.group(2).strip().lower() in HINT_WORDS:
        return m.group(1).strip(), HINT_WORDS[m.group(2).strip().lower()]
    return str(fav).strip(), None


# Qloo tags have URN ids: urn:tag:<category>:<namespace>:<name> ("urn:tag:genre:media:drama"). The live API returns
# tags of every category for any signal (a film can come back with "VISA" and "Five Star"), so each category is
# classed by what it says about taste:
#   topic  what the favourite is about (genre, theme, keyword, cuisine ...): used at full weight and shown on cards
#   mood   how it feels (style, character, audience, emotional tone ...): used at half weight, never shown on cards
#   noise  venue, price, audience-demographic and brand-business metadata: dropped
TOPIC_CATEGORIES = {
    "keyword", "genre", "subgenre", "theme", "interests", "interest_adjacency", "influenced_by", "influence",
    "instrument", "category", "gameplay_type", "cuisine", "menu_highlight", "time_period", "art_style", "motif",
    "lifestyle", "wikipedia_category", "hobby", "backdrop", "occupation", "core_value", "industry", "dietary_option",
}
NOISE_CATEGORIES = {
    "credit_card", "hotel_rating", "inclusivity", "cost_description", "specialty_dish", "amenity", "amenities",
    "customer_segment", "product_service", "influencer_appeal", "cultural_relevance", "adjective", "payments",
    "parking", "accessibility", "planning", "price_level", "service_options", "service_type", "dining_options",
    "age_ranges", "age_range", "target_age_group", "target_gender", "target_income_level", "not_for_display",
    "noise_level", "children", "pets", "getting_here", "nearby_attraction", "neighborhood_characteristic",
    "time_of_day_fit", "visit_intent", "customer_identity", "key_market", "brand_type", "brand_subtype",
    "retail_channel", "distribution_channel", "book_format", "media_type", "business_model", "collection_cadence",
    "packaging_format", "partnership", "subsidiary", "signature_piece", "signature_product", "product_line",
    "product_category", "consumption_context", "event", "view", "beverage_offering", "offerings", "setting",
    "market_archetype", "competitor_brand", "similar_brand", "platform", "material", "fashion_segment",
    "sustainability_initiative", "design_inspiration", "ingredient_focus", "flavor_profile", "dietary_attribute",
    "recycling", "player_mode", "skill_level", "narrative", "personal_style", "aesthetic_property", "decor",
    "ambience", "culinary_style", "activity_type", "artist",
}
MOOD_WEIGHT = 0.5


def tag_category(tag_id):
    parts = str(tag_id or "").split(":")
    return parts[2] if len(parts) > 2 and parts[0] == "urn" else ""


def tag_kind(tag_id):
    """'topic', 'mood' or 'noise' for a tag id. Unknown categories count as mood; ids that are not Qloo URNs
    (hand-written SAMPLE tags, test data) carry no category and count as topics."""
    c = tag_category(tag_id)
    return "noise" if c in NOISE_CATEGORIES else "topic" if c in TOPIC_CATEGORIES or not c else "mood"


OWN_TAG_CAP = 150     # own tags kept per favourite (the API returns up to ~320, in no useful order)


class QlooError(Exception):
    """A Qloo request failed. `retryable` tells the agent whether trying again may help."""

    def __init__(self, message, status=None, retryable=False):
        super().__init__(message)
        self.status, self.retryable = status, retryable


class QlooAuthError(QlooError):
    pass


class QlooRateLimited(QlooError):
    pass


class QlooQuotaError(QlooError):
    """The monthly request quota is used up (not retryable)."""


def _short_type(e):
    for key in ("subtype", "type"):
        v = e.get(key)
        if isinstance(v, str) and v in SHORT:
            return SHORT[v]
    for v in e.get("types") or []:
        if v in SHORT:
            return SHORT[v]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _explain(q):
    """Flatten query.explainability into {input_entity_id: score}. Shape is not fully
    documented, so accept a dict of id->score, or lists of {entity_id|id, score|...}."""
    out = {}

    def walk(node):
        if isinstance(node, dict):
            eid = node.get("entity_id") or node.get("id")
            score = next((_num(node[k]) for k in ("score", "weight", "value", "contribution")
                          if k in node and _num(node[k]) is not None), None)
            if isinstance(eid, str) and score is not None:
                out[eid] = max(out.get(eid, 0.0), score)
                return
            for k, v in node.items():
                looks_like_id = len(k) >= 16 and ("-" in k or k.startswith("urn:"))
                if looks_like_id and _num(v) is not None:
                    out[k] = max(out.get(k, 0.0), _num(v))
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk((q or {}).get("explainability"))
    return {k: round(v, 3) for k, v in out.items()}


def _tag_key(t):
    """Stable id of a tag. Almost every tag_id is a URN; the few UUID ones carry the URN in `value`."""
    tid = t.get("tag_id") or t.get("id")
    if isinstance(tid, str) and not tid.startswith("urn:"):
        for k in ("value", "tag_value"):
            if isinstance(t.get(k), str) and t[k].startswith("urn:"):
                return t[k]
    return tid or t.get("tag_value")


def _clean(text):
    """Qloo text fields arrive HTML-escaped in places ("Habits &amp; Break") and with stray spaces (" indie")."""
    return " ".join(html.unescape(str(text)).split())


def _norm_entity(e):
    props = e.get("properties") or {}
    q = e.get("query") or {}
    img = props.get("image")
    geo = props.get("geocode") or {}
    desc = props.get("description") or props.get("short_description") or ""
    tags, tag_ids = [], []
    for t in e.get("tags") or []:
        name = _clean((t.get("name") if isinstance(t, dict) else t) or "")
        tid = _tag_key(t) if isinstance(t, dict) else None
        if name and name not in tags and len(tags) < OWN_TAG_CAP:
            tags.append(name)
            tag_ids.append(tid)
    return {
        "id": e.get("entity_id") or e.get("id"),
        "name": _clean(e.get("name") or "") or "?",
        "type": _short_type(e),
        "affinity": _num(q.get("affinity", e.get("affinity"))),
        "popularity": _num(e.get("popularity")),
        "disambiguation": _clean(e.get("disambiguation") or ""),
        "year": props.get("release_year") or props.get("year"),
        "address": props.get("address") or geo.get("city") or "",
        "description": str(desc)[:180],
        "image": img.get("url") if isinstance(img, dict) else img,
        "tags": tags,          # the entity's own tag names (display and grounding)
        "tag_ids": tag_ids,    # parallel list of tag ids (None where the API gave none)
        "explain": _explain(q),
    }


def _norm_tag(t):
    q = t.get("query") if isinstance(t.get("query"), dict) else {}
    return {
        "id": _tag_key(t),
        "name": _clean(t.get("name") or "") or "?",
        "type": t.get("subtype") or (t.get("type") if isinstance(t.get("type"), str) else "") or "",
        "affinity": _num(q.get("affinity", t.get("affinity", t.get("score")))),
    }


def _tag_vector(tags):
    """Normalise taste-analysis tags into {tag_id: {"name", "affinity"}} with affinity in (0, 1].

    Uses the API's affinity when every tag has one; otherwise falls back to rank order (the API
    returns the most characteristic tags first), so a payload without scores still works.
    Noise tags (venue and price metadata) are dropped; mood tags are halved (see tag_kind)."""
    tags = [t for t in tags if t["id"] and tag_kind(t["id"]) != "noise"]
    if not tags:
        return {}
    have = [t["affinity"] for t in tags if t["affinity"] is not None]
    out = {}
    n = len(tags)
    for i, t in enumerate(tags):
        if len(have) == n:
            a = min(max(t["affinity"], 0.0), 1.0)
        else:
            a = 1.0 - 0.9 * i / max(n - 1, 1)
        kind = tag_kind(t["id"])
        if kind == "mood":
            a *= MOOD_WEIGHT
        if a > 0:
            out[t["id"]] = {"name": t["name"], "affinity": round(a, 4), "kind": kind}
    return out


def blend_own_tags(vector, entities, weight=None):
    """Add the favourites' own Qloo tags to a taste-analysis vector.

    A tag's own weight is the fraction of the guest's matched favourites that carry it, times `weight`
    (config.OWN_TAG_WEIGHT; 0 turns the blend off), halved for mood tags; noise tags are skipped. Tags already in
    the vector gain on top of their affinity. Every tag a favourite carries gets "fav": how many favourites do, so
    a card can say which favourites back a tag; "own" marks tags that came from the favourites only. The result
    keeps the {tag_id: {"name", "affinity", ...}} shape. Entities without tag ids (SAMPLE data) leave the vector
    unchanged."""
    w = config.OWN_TAG_WEIGHT if weight is None else weight
    ents = [e for e in entities if e.get("tag_ids")]
    if w <= 0 or not ents:
        return vector
    count, names = {}, {}
    for e in ents:
        for tid, nm in zip(e["tag_ids"], e["tags"]):
            if tid:
                count[tid] = count.get(tid, 0) + 1
                names.setdefault(tid, nm)
    out = {k: dict(v) for k, v in vector.items()}
    for tid, c in count.items():
        kind = tag_kind(tid)
        if kind == "noise":
            continue
        add = w * c / len(ents) * (MOOD_WEIGHT if kind == "mood" else 1.0)
        if tid in out:
            out[tid]["affinity"] = round(out[tid]["affinity"] + add, 4)
            out[tid]["fav"] = c
        else:
            out[tid] = {"name": names[tid], "affinity": round(add, 4), "kind": kind, "own": True, "fav": c}
    return out


class _Throttle:
    """Spaces requests at least `interval` seconds apart across all threads (the key allows 5 per second)."""

    def __init__(self, interval, sleep, clock=time.monotonic):
        self.interval, self.sleep, self.clock = interval, sleep, clock
        self._next, self._lock = 0.0, threading.Lock()

    def wait(self):
        if self.interval <= 0:
            return
        with self._lock:
            now = self.clock()
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            self.sleep(slot - now)


class LiveQloo:
    sample = False

    def __init__(self, key, base=None, opener=urllib.request.urlopen, sleep=time.sleep,
                 cache_ttl=24 * 3600, rps=None, cache_file=None):
        self.key, self.base = key, (base or config.QLOO_BASE_URL).rstrip("/")
        self.opener, self.sleep, self.cache_ttl = opener, sleep, cache_ttl
        rps = config.QLOO_RPS if rps is None else rps
        self._throttle = _Throttle(1.0 / rps if rps and rps > 0 else 0.0, sleep)
        self.calls = 0                       # network calls made (cache hits are free)
        self.month_remaining = None          # from X-Month-RateLimit-Remaining
        self.month_limit = None
        self._cache = {}                     # (kind, key) -> (timestamp, normalised result)
        self._lock = threading.Lock()        # profiles are built on several threads
        self._cache_file = cache_file
        self._dirty, self._last_save = False, 0.0
        if cache_file:
            self._load_cache()
            atexit.register(self.save_cache)

    # -- persistent cache ------------------------------------------------------------
    def _load_cache(self):
        try:
            with open(self._cache_file, encoding="utf8") as f:
                data = json.load(f)
            now = time.time()
            for k, (ts, val) in data.get("entries", {}).items():
                kind, key = k.split("|", 1)
                if now - ts < self.cache_ttl:
                    self._cache[(kind, key)] = (ts, val)
        except (OSError, ValueError, TypeError):
            pass

    def save_cache(self):
        if not self._cache_file or not self._dirty:
            return
        with self._lock:
            entries = {f"{k[0]}|{k[1]}": [ts, v] for k, (ts, v) in self._cache.items()}
            self._dirty = False
        tmp = f"{self._cache_file}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf8") as f:
                json.dump({"version": 1, "entries": entries}, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self._cache_file)
        except OSError:
            pass

    def _cached(self, kind, key):
        with self._lock:
            hit = self._cache.get((kind, key))
        if hit and time.time() - hit[0] < self.cache_ttl:
            return hit[1]
        return None

    def _store(self, kind, key, value):
        with self._lock:
            if len(self._cache) > 1500:
                self._cache.clear()
            self._cache[(kind, key)] = (time.time(), value)
            self._dirty = True
            due = bool(self._cache_file) and time.time() - self._last_save > 5
            if due:
                self._last_save = time.time()
        if due:
            self.save_cache()

    def quota(self):
        """Last known monthly quota from the response headers (None values until a call has been made)."""
        return {"remaining": self.month_remaining, "limit": self.month_limit}

    def _note_headers(self, headers):
        try:
            rem, lim = headers.get("X-Month-RateLimit-Remaining"), headers.get("X-Month-RateLimit-Limit")
        except AttributeError:
            return
        with self._lock:
            if _num(rem) is not None:
                self.month_remaining = int(_num(rem))
            if _num(lim) is not None:
                self.month_limit = int(_num(lim))

    # -- transport -------------------------------------------------------------
    def _get(self, path, params, empty_on_404=False):
        params = {k: v for k, v in params.items() if v not in (None, "", [], ())}
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"X-Api-Key": self.key, "Accept": "application/json",
                                                   "User-Agent": "Placecard/1.0"})
        attempts = 3
        for attempt in range(attempts):
            self._throttle.wait()
            with self._lock:
                self.calls += 1
            try:
                with self.opener(req, timeout=30) as r:
                    self._note_headers(getattr(r, "headers", None))
                    return json.loads(r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                self._note_headers(e.headers)
                body = _read_body(e)
                if e.code == 404 and empty_on_404:
                    return {}
                if e.code in (401,):
                    raise QlooAuthError("Qloo rejected the API key (401). Check QLOO_API_KEY and that the "
                                        "base URL is https://hackathon.api.qloo.com.", 401) from None
                if e.code == 403:
                    raise QlooError(f"Qloo refused this request (403): {body or 'type not permitted'}", 403) from None
                if e.code == 429 or e.code >= 500:
                    if e.code == 429 and self.month_remaining == 0:
                        raise QlooQuotaError("The monthly Qloo request quota is used up.", 429) from None
                    if attempt < attempts - 1:
                        # a 429 on the per-second limit clears within a second; back off a little more each time
                        self.sleep(min(max(_retry_after(e), 0.5) * (attempt + 1), 2.5))
                        continue
                    if e.code == 429:
                        raise QlooRateLimited("Qloo rate limit reached (429). Try again in a minute.", 429,
                                              retryable=True) from None
                    raise QlooError(f"Qloo server error ({e.code}).", e.code, retryable=True) from None
                raise QlooError(f"Qloo request failed ({e.code}): {body or e.reason}", e.code) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt == 0:
                    self.sleep(0.5)
                    continue
                raise QlooError(f"Could not reach Qloo: {getattr(e, 'reason', e)}", retryable=True) from None
        raise QlooError("Qloo request failed.", retryable=True)  # pragma: no cover

    # -- API ---------------------------------------------------------------------
    def search(self, query, entity_type=None, take=5):
        """Entities matching `query`, best first. entity_type: one short type, or None for CULTURE_TYPES."""
        if entity_type:
            types = SEARCH_TYPES[entity_type]
        else:
            types = ",".join(SEARCH_TYPES[t] for t in CULTURE_TYPES)
        key = json.dumps([" ".join(str(query).lower().split()), types, take])
        hit = self._cached("search", key)
        if hit is not None:
            return hit
        data = self._get("/search", {"query": query, "take": take, "types": types}, empty_on_404=True)
        res = data.get("results", data if isinstance(data, list) else [])
        out = [_norm_entity(r) for r in res if isinstance(r, dict)]
        out = [o for o in out if o["id"]][:take]
        self._store("search", key, out)
        return out

    def taste_tags(self, entity_ids, take=50, tag_types=None):
        """Taste analysis: the tags that characterise a guest's favourites, as a vector
        {tag_id: {"name", "affinity"}}. One GET /v2/insights with filter.type=urn:tag.
        Entity ids are sorted so the cache is order-insensitive."""
        ids = sorted(dict.fromkeys(i for i in entity_ids if i))
        if not ids:
            return {}
        take = max(1, min(int(take), 50))   # the API rejects take > 50
        key = json.dumps([ids, take, sorted(tag_types or ())])
        tags = self._cached("tastetags", key)
        if tags is None:
            params = {"filter.type": "urn:tag", "signal.interests.entities": ",".join(ids),
                      "filter.tag.types": ",".join(tag_types or ()), "take": take}
            data = self._get("/v2/insights", params)
            res = (data.get("results") or {}).get("tags") or []
            tags = [_norm_tag(x) for x in res if isinstance(x, dict)]
            self._store("tastetags", key, tags)     # raw tags are cached; the vector is derived each time
        return _tag_vector(tags)


def _read_body(e):
    try:
        raw = e.read().decode()
    except Exception:
        return ""
    try:
        j = json.loads(raw)
        err = j.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        return str(j.get("message") or err or raw)[:400]
    except Exception:
        return raw[:300]


def _retry_after(e):
    for h in ("Retry-After", "X-Second-RateLimit-Reset"):
        try:
            return float(e.headers.get(h))
        except (TypeError, ValueError, AttributeError):
            continue
    return 1.0


_SHARED = {}


def get_client():
    """Live client (shared across requests so its cache persists) or the labelled SAMPLE client."""
    key = config.qloo_key()
    if key:
        if _SHARED.get("key") != key:
            _SHARED.update(key=key, client=LiveQloo(key, cache_file=config.qloo_cache_file()))
        return _SHARED["client"]
    from .sample_qloo import SampleQloo
    return SampleQloo()
