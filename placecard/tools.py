"""The agent's tools: Qloo taste profiles, constraints, the seating optimiser, table cards.

The LLM decides which tools to call and in what order. The toolbox keeps the per-run state: every
guest's resolved favourites and Qloo tag vector, the structured constraints, the seating plan and the
conversation-starter cards. The final plan is assembled from that state, so every number in the UI
came from Qloo (or from labelled SAMPLE data) and the optimiser, never from the model, and a card is
rejected unless every tag, favourite and guest it mentions is grounded in the table's own Qloo data.
"""
import difflib
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import config, qloo as qloo_mod, seating
from .qloo import QlooAuthError, QlooError
from .requirements import parse_constraint, split_requirements

PROFILE_BATCH = 60          # guests profiled per build_taste_profiles call
MAX_LISTED = 20             # cap on names echoed back to the model


def _arr(desc, max_items=None):
    s = {"type": "array", "items": {"type": "string"}, "description": desc}
    if max_items:
        s["maxItems"] = max_items
    return s


TOOL_SPECS = [
    {
        "name": "build_taste_profiles",
        "description": "Build each guest's taste profile from Qloo: resolve their 2-4 favourites (Qloo /search) and "
                       "fetch the tag-affinity vector that characterises them (Qloo taste analysis, /v2/insights with "
                       "filter.type=urn:tag). Guest names are never sent to Qloo. Processes up to 60 guests per call; "
                       "call again while 'remaining' is non-zero. Reports unmatched favourites and any ambiguity "
                       "assumptions it made.",
        "parameters": {
            "type": "object",
            "properties": {"names": _arr("Optional: only these guests (exact names). Default: every guest not yet profiled.")},
        },
    },
    {
        "name": "apply_constraint",
        "description": "Turn ONE organiser requirement into a validated hard constraint. Translate the natural-language "
                       "sentence yourself into structured fields and pass the original sentence as `text`. Kinds: "
                       "'together' (guests, or a whole `group` such as 'Tan family'), 'apart' (guests who must be at "
                       "different tables), 'spread' (a `group` such as 'Speakers', or named guests, divided over the "
                       "tables as evenly as possible: at most ceil(n / tables) of them per table; use it for 'spread "
                       "the speakers across tables' or 'mix the sponsors'), 'fixed' (guests + `table` number), 'head' "
                       "(guests who sit at the head table, table 1), 'none' (ONLY when the organiser's notes contain no "
                       "seating requirement at all; put the reason in `text`). Guest names must match the guest list; "
                       "the server validates them and rejects contradictions (e.g. together and apart). When the "
                       "organiser gave requirements, seat_guests is refused until this tool has been used.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The organiser's original wording."},
                "kind": {"type": "string", "enum": ["together", "apart", "spread", "fixed", "head", "none"]},
                "guests": _arr("Guest names (a unique first name or the full name)."),
                "group": {"type": "string", "description": "For 'together' or 'spread': a group name from the guest list."},
                "table": {"type": "integer", "description": "For 'fixed': the table number, 1-based."},
            },
            "required": ["text", "kind"],
        },
    },
    {
        "name": "seat_guests",
        "description": "Run the seating optimiser on the taste profiles and the constraints. It maximises the fit of the "
                       "least-matched guest plus the average table cohesion, avoids isolating anyone, and is deterministic "
                       "for a seed. Returns tables, per-table cohesion and shared tags, the lowest-fit guests, isolation "
                       "checks and a constraint report. Apply the organiser's requirements with apply_constraint "
                       "FIRST; this tool refuses to run before that. Call again after changing constraints.",
        "parameters": {
            "type": "object",
            "properties": {
                "table_size": {"type": "integer", "description": "Seats per table (2-14). Default: the organiser's setting."},
                "seed": {"type": "integer", "description": "Optional seed for a different but equally valid arrangement."},
            },
        },
    },
    {
        "name": "explain_table",
        "description": "Everything grounded about one seated table: each guest's fit and why, the tags the table shares "
                       "(from Qloo), and the favourites behind them. Use it before writing a table card, or to explain "
                       "a low-fit guest to the organiser.",
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "integer", "description": "Table number, 1-based."}},
            "required": ["table"],
        },
    },
    {
        "name": "table_card",
        "description": "Save the conversation-starter card for one table. It may only use the table's shared Qloo tags "
                       "and the favourites of the guests who share them (see explain_table); the server REJECTS the card "
                       "if it names a tag, favourite or guest that is not grounded in this table's data.",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "integer"},
                "title": {"type": "string", "description": "Short card title, max 60 characters."},
                "opener": {"type": "string", "description": "1-2 friendly sentences naming what the table has in common."},
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tag": {"type": "string", "description": "A shared tag of this table, exactly as listed."},
                            "because": {"type": "string", "description": "Which guests' favourites back it, e.g. 'Mei and Dan both listed Parasite'."},
                            "entities": _arr("Favourites (as listed) of guests who share this tag."),
                            "guests": _arr("Guests at this table who share this tag."),
                        },
                        "required": ["tag", "because"],
                    },
                },
                "ask": {"type": "string", "description": "One question the table can answer to start talking."},
            },
            "required": ["table", "opener", "topics", "ask"],
        },
    },
    {
        "name": "ask_organizer",
        "description": "Stop and ask the organiser ONE short clarifying question when an ambiguity would materially change "
                       "the plan and no safe assumption exists (e.g. many guests have no matched favourites, or a "
                       "constraint refers to a person you cannot identify). Ends this run.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "options": _arr("2-4 short answer options.", 4)},
            "required": ["question"],
        },
    },
    {
        "name": "present_plan",
        "description": "Finish with the seating plan. Requires seat_guests to have run on the current constraints. Tables "
                       "without a card get an automatic grounded one. Ends this run.",
        "parameters": {
            "type": "object",
            "properties": {
                "headline": {"type": "string", "description": "One-line summary of the seating."},
                "notes": _arr("Short observations for the organiser (e.g. which guests have the weakest fit and why)."),
                "assumptions": _arr("Assumptions you made (ambiguous favourites, constraint interpretations)."),
            },
            "required": ["headline"],
        },
    },
]
TERMINAL = {"ask_organizer", "present_plan"}


class ToolError(Exception):
    pass


class FatalToolError(Exception):
    """Stop the whole run (e.g. bad Qloo key)."""


def _list(v):
    """Models sometimes send 'a, b' instead of ['a', 'b']; accept both."""
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _norm(s):
    """Lower-case, accent-free, punctuation-free form used for every name comparison ('Beyoncé' == 'beyonce')."""
    s = unicodedata.normalize("NFKD", str(s).lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c))).split())


def _mentions(text, phrase):
    """Whole-word, case-insensitive containment (text and phrase are normalised first)."""
    p = _norm(phrase)
    return bool(p) and re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", _norm(text)) is not None


def _mentions_exact(text, phrase):
    """Case-sensitive whole-word containment, for titles such as 'Friends' that are also everyday words."""
    return bool(phrase) and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def to_json(result):
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class ToolBox:
    def __init__(self, qloo, guests, table_size=seating.DEFAULT_TABLE_SIZE, seed=0, max_qloo_calls=800, emit=None,
                 workers=6, requirements=""):
        self.qloo, self.max_calls, self.emit, self.workers = qloo, max_qloo_calls, emit, workers
        self.start_calls = getattr(qloo, "calls", 0)
        self.guests = {g["name"]: {"favorites": list(g.get("favorites") or []), "group": g.get("group") or ""}
                       for g in guests}
        self.names = [g["name"] for g in guests]
        self.table_size, self.seed = table_size, seed
        self.profiles = {}            # name -> {"entities", "vector", "unmatched", "assumptions", "status"}
        self.tag_names = {}           # tag_id -> display name
        self.requirements = str(requirements or "").strip()   # the organiser's notes, verbatim
        self.constraints = {"together": [], "apart": [], "spread": [], "fixed": {}, "head": []}
        self.constraint_log = []
        self.rejected = []            # [{"text", "error"}] apply_constraint calls that were refused
        self.no_requirements = None   # reason given when the model answered kind='none'
        self.auto_applied = []        # requirement sentences the server applied itself (fallback)
        self.unapplied_extra = []     # requirement sentences the fallback could not apply
        self._fallback = False        # auto_plan has taken over: the guard in seat_guests no longer applies
        self.assumptions = []
        self.plan = None              # seating plan dict
        self.stale = False            # constraints/profiles changed since the last seat_guests
        self.cards = {}               # table index -> card
        self.final = None             # ("plan", dict) or ("question", dict)
        self._search_cache, self._lock = {}, threading.Lock()

    # -- helpers --------------------------------------------------------------
    def qloo_used(self):
        return getattr(self.qloo, "calls", 0) - self.start_calls

    def _budget(self):
        if self.qloo_used() >= self.max_calls:
            raise ToolError("The Qloo call budget for this run is used up. Seat with the profiles you have and say so.")
        quota = getattr(self.qloo, "quota", None)
        left = quota()["remaining"] if quota else None
        if left is not None and left < config.QLOO_MONTH_RESERVE:
            raise ToolError("The monthly Qloo quota is nearly used up, so no more requests are made. Seat with the "
                            "profiles you have and say so.")

    def _resolve_guest(self, text):
        t = str(text).strip()
        if t in self.guests:
            return t
        n = _norm(t)
        exact = [g for g in self.names if _norm(g) == n]
        if len(exact) == 1:
            return exact[0]
        loose = [g for g in self.names if n and (n in _norm(g).split() or _norm(g).startswith(n))]
        if len(loose) == 1:
            return loose[0]
        if len(loose) > 1:
            raise ToolError(f"'{t}' is ambiguous: could be {', '.join(loose[:5])}. Use the full name.")
        close = difflib.get_close_matches(t, self.names, n=3, cutoff=0.6)
        raise ToolError(f"No guest named '{t}'." + (f" Did you mean {', '.join(close)}?" if close else ""))

    def _resolve_group(self, text):
        groups = sorted({g["group"] for g in self.guests.values() if g["group"]})
        n = _norm(text)
        stem = n[:-1] if n.endswith("s") else n
        hits = [g for g in groups if _norm(g) == n]
        hits = hits or [g for g in groups if stem and (stem in _norm(g).split() or _norm(g).startswith(stem))]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ToolError(f"No group named '{text}'. Groups: {groups or 'none were given'}.")
        raise ToolError(f"'{text}' matches several groups: {hits}.")

    def tag_name(self, tag_id):
        return self.tag_names.get(tag_id, str(tag_id).split(":")[-1].replace("_", " ").title())

    def _resolve_tag(self, table, text):
        """Match a model-supplied tag to one of the table's shared tags (by name or id)."""
        n = _norm(text)
        for s in table["shared_tags"]:
            if n in (_norm(self.tag_name(s["tag_id"])), _norm(s["tag_id"])):
                return s
        return None

    # -- dispatch -------------------------------------------------------------
    def run(self, name, args):
        """Execute one tool call. Returns (result_dict, is_error)."""
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            return {"error": f"No tool named {name}."}, True
        try:
            return fn(**(args or {})), False
        except (ToolError, seating.SeatingError) as e:
            return {"error": str(e)}, True
        except QlooAuthError as e:
            raise FatalToolError(str(e)) from None
        except QlooError as e:
            hint = " This may be temporary; you can retry once." if e.retryable else ""
            return {"error": str(e) + hint}, True
        except TypeError as e:
            return {"error": f"Bad arguments for {name}: {e}"}, True

    # -- build_taste_profiles ------------------------------------------------------
    def _search(self, fav, hint=None):
        key = (_norm(fav), hint)
        with self._lock:
            if key in self._search_cache:
                return self._search_cache[key]
        self._budget()
        hits = self.qloo.search(str(fav)[:80], hint, take=5)
        with self._lock:
            self._search_cache[key] = hits
        return hits

    @staticmethod
    def _base_title(name):
        """'Sapiens: A Brief History of Humankind' -> 'sapiens' (text before a colon, dash or bracket)."""
        return _norm(re.split(r"\s[-–—]\s|:|\(", str(name))[0])

    @classmethod
    def _choose(cls, fav, hits, assumptions):
        """Pick the entity a guest most likely means. Order: the exact name, then the name before a subtitle
        ('Lagaan' -> 'Lagaan: Once Upon a Time in India'), then the closest spelling. Within a level the
        more popular entity wins; a venue (place) is discounted because many businesses share a film's or
        a band's name."""
        n = _norm(fav)

        def weight(h):
            return (h.get("popularity") or 0.0) * (0.6 if h.get("type") == "place" else 1.0)

        works = ("movie", "tv_show", "book", "video_game")
        for level, test in enumerate((lambda h: _norm(h["name"]) == n, lambda h: cls._base_title(h["name"]) == n)):
            found = [h for h in hits if test(h)]
            if found:
                pick = max(found, key=weight)       # max() keeps the first (best-ranked) on ties
                # a genuine ambiguity: another work with the same title (a film and a book, two films), not a
                # restaurant or a band that happens to share a name
                rivals = [h for h in found if h is not pick and pick.get("type") in works and h.get("type") in works
                          and (h.get("popularity") or 0) >= max(0.5, 0.9 * (pick.get("popularity") or 0))]
                if rivals:
                    def label(h):
                        what = h.get("year") or str(h.get("disambiguation") or "")[:22].strip(" ,")
                        return f"{h.get('type')}, {what}" if what else str(h.get("type"))
                    assumptions.append(f"Assumed '{fav}' means {pick['name']} ({label(pick)}), the most popular match; "
                                       f"others: {'; '.join(dict.fromkeys(label(h) for h in rivals[:3]))}. "
                                       "Add (book), (game), (band)... to choose.")
                return pick
        best, ratio = None, 0.0
        for h in hits:
            r = difflib.SequenceMatcher(None, n, _norm(h["name"])).ratio()
            if r > ratio:
                best, ratio = h, r
        if best and ratio >= 0.7:
            assumptions.append(f"Matched '{fav}' to {best['name']} (closest Qloo match).")
            return best
        return None

    def _profile_one(self, name):
        ents, unmatched, assumptions = [], [], []
        for fav in self.guests[name]["favorites"]:
            title, hint = qloo_mod.split_hint(fav)
            pick = self._choose(title, self._search(title, hint), assumptions)
            if pick is None and hint:       # the hint may be wrong; try without it
                pick = self._choose(title, self._search(title), assumptions)
            if pick is None:
                unmatched.append(fav)
            elif pick["id"] not in {e["id"] for e in ents}:
                ents.append(pick)
        vector = {}
        if ents:
            self._budget()
            vector = self.qloo.taste_tags([e["id"] for e in ents])
            vector = qloo_mod.blend_own_tags(vector, ents)
        return {"entities": [{"id": e["id"], "name": e["name"], "type": e.get("type"), "tags": e.get("tags") or [],
                              "tag_ids": e.get("tag_ids") or [], "disambiguation": e.get("disambiguation") or ""} for e in ents],
                "vector": vector, "unmatched": unmatched, "assumptions": assumptions,
                "status": "ok" if vector else "no_data"}

    def t_build_taste_profiles(self, names=None):
        if names:
            todo = []
            for n in _list(names):
                g = self._resolve_guest(n)
                if g not in todo:
                    todo.append(g)
        else:
            todo = [n for n in self.names if n not in self.profiles]
        batch = todo[:PROFILE_BATCH]
        failed, skipped, done = [], [], [0]

        def work(n):
            try:
                prof = self._profile_one(n)
            except ToolError:
                skipped.append(n)
                return
            except QlooAuthError:
                raise
            except QlooError as e:
                prof = {"entities": [], "vector": {}, "unmatched": list(self.guests[n]["favorites"]),
                        "assumptions": [], "status": "failed", "error": str(e)}
                failed.append(n)
            with self._lock:
                self.profiles[n] = prof
                for tid, t in prof["vector"].items():
                    self.tag_names.setdefault(tid, t["name"])
                done[0] += 1
                if self.emit and (done[0] % 10 == 0 or done[0] == len(batch)):
                    self.emit({"type": "progress", "name": "build_taste_profiles", "done": done[0],
                               "total": len(batch)})

        if self.workers > 1 and not getattr(self.qloo, "sample", False) and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                for f in [ex.submit(work, n) for n in batch]:
                    f.result()  # re-raises QlooAuthError
        else:
            for n in batch:
                work(n)
        if batch:
            self.stale = True
        remaining = [n for n in self.names if n not in self.profiles]
        done_now = [n for n in batch if n in self.profiles]
        for n in done_now:
            for a in self.profiles[n]["assumptions"]:
                if a not in self.assumptions:
                    self.assumptions.append(a)
        out = {
            "profiled_now": len(done_now), "profiled_total": len(self.profiles), "guests_total": len(self.names),
            "remaining": len(remaining),
            "unmatched_favorites": [{"guest": n, "favorite": f} for n in done_now
                                    for f in self.profiles[n]["unmatched"]][:MAX_LISTED],
            "guests_without_taste_data": [n for n in done_now if self.profiles[n]["status"] != "ok"][:MAX_LISTED],
            "assumptions": [a for n in done_now for a in self.profiles[n]["assumptions"]][:10],
            "qloo_requests_so_far": self.qloo_used(),
        }
        if failed:
            out["failed"] = failed[:10]
        if skipped:
            out["note"] = "The Qloo call budget ran out; the remaining guests will be seated without taste data."
        elif remaining:
            out["note"] = "Call build_taste_profiles again for the remaining guests."
        else:
            out["note"] = "All guests profiled. Next: apply the organiser's constraints, then seat_guests."
        return out

    # -- apply_constraint ------------------------------------------------------------
    def _candidate(self, kind, guests, table):
        c = {"together": [list(x) for x in self.constraints["together"]],
             "apart": [list(x) for x in self.constraints["apart"]],
             "spread": [list(x) for x in self.constraints["spread"]],
             "fixed": dict(self.constraints["fixed"]), "head": list(self.constraints["head"])}
        if kind in ("together", "apart", "spread"):
            c[kind].append(guests)
        elif kind == "fixed":
            for g in guests:
                c["fixed"][g] = table
        else:
            c["head"] += [g for g in guests if g not in c["head"]]
        return c

    def t_apply_constraint(self, text, kind, guests=None, group=None, table=None):
        try:
            return self._apply_constraint(text, kind, guests, group, table)
        except (ToolError, seating.SeatingError) as e:
            if str(kind).strip().lower() != "none":
                self.rejected.append({"text": str(text)[:200], "error": str(e)})
            raise

    def _apply_constraint(self, text, kind, guests, group, table):
        kind = str(kind).strip().lower()
        if kind == "none":
            self.no_requirements = str(text or "no reason given").strip()[:200]
            return {"ok": True, "added": {"kind": "none", "text": self.no_requirements, "guests": []},
                    "constraints_so_far": len(self.constraint_log),
                    "note": "Recorded: no seating requirement. Next: seat_guests."}
        if kind not in ("together", "apart", "spread", "fixed", "head"):
            raise ToolError("kind must be one of together, apart, spread, fixed, head (or none).")
        people = []
        for g in _list(guests):
            r = self._resolve_guest(g)
            if r not in people:
                people.append(r)
        label = None
        if group:
            if kind not in ("together", "spread"):
                raise ToolError("`group` is only supported for 'together' and 'spread'. For 'apart', name the guests.")
            label = self._resolve_group(group)
            people += [n for n in self.names if self.guests[n]["group"] == label and n not in people]
        if kind in ("together", "apart", "spread") and len(people) < 2:
            raise ToolError(f"'{kind}' needs at least two guests.")
        if kind in ("fixed", "head") and not people:
            raise ToolError(f"'{kind}' needs at least one guest.")
        if kind == "fixed":
            try:
                table = int(table)
            except (TypeError, ValueError):
                raise ToolError("'fixed' needs a table number.") from None
        tbl = table if kind == "fixed" else None
        for e in self.constraint_log:
            if e["kind"] == kind and set(e["guests"]) == set(people) and e["table"] == tbl:
                return {"ok": True, "added": e, "constraints_so_far": len(self.constraint_log),
                        "note": "Already applied; nothing changed. Next: more constraints if any, then seat_guests."}
        cand = self._candidate(kind, people, table)
        seating.check_feasible(self.names, cand, self.table_size)   # raises SeatingError, nothing stored
        self.constraints = seating.normalise_constraints(self.names, cand)
        entry = {"text": str(text)[:200], "kind": kind, "guests": people, "group": label, "table": tbl}
        self.constraint_log.append(entry)
        self.rejected = [r for r in self.rejected if _norm(r["text"]) != _norm(text)]   # a retry fixed it
        self.stale = True
        return {"ok": True, "added": entry, "constraints_so_far": len(self.constraint_log),
                "note": "Next: more constraints if any, then seat_guests."}

    # -- seat_guests -----------------------------------------------------------------
    def t_seat_guests(self, table_size=None, seed=None):
        if not self.profiles:
            raise ToolError("Call build_taste_profiles first.")
        if (self.requirements and not self.constraint_log and not self.no_requirements and not self.rejected
                and not self._fallback):
            raise ToolError(
                "The organiser gave these requirements, and none has been applied yet: \"" + self.requirements[:600] +
                "\". Call apply_constraint once for EACH requirement (together, apart, spread, fixed or head) and only "
                "then call seat_guests. If, after reading them, none of it is a seating requirement, call "
                "apply_constraint with kind='none' and the reason in `text`.")
        missing = [n for n in self.names if n not in self.profiles]
        if missing and self.qloo_used() < self.max_calls:
            raise ToolError(f"{len(missing)} guests have no profile yet (e.g. {missing[:3]}). "
                            "Call build_taste_profiles again first.")
        if table_size is not None:
            try:
                ts = int(table_size)
            except (TypeError, ValueError):
                raise ToolError("table_size must be a number.") from None
            if not 2 <= ts <= 14:
                raise ToolError("table_size must be between 2 and 14.")
            self.table_size = ts
        if seed is not None:
            try:
                self.seed = int(seed)
            except (TypeError, ValueError):
                raise ToolError("seed must be a number.") from None
        vectors = {n: p["vector"] for n, p in self.profiles.items()}
        plan = seating.seat(self.names, vectors, self.table_size, constraints=self.constraints, seed=self.seed)
        self.plan, self.stale, self.cards = plan, False, {}
        lows = sorted((g["fit"], n) for n, g in plan["guests"].items() if not g["no_data"])[:5]
        return {
            "tables": [{"table": t["index"], "label": t["label"], "guests": t["guests"], "cohesion": t["cohesion"],
                        "min_fit": t["min_fit"], "shared_tags": [self.tag_name(s["tag_id"]) for s in t["shared_tags"][:4]]}
                       for t in plan["tables"]],
            "min_fit": plan["min_fit"], "avg_cohesion": plan["avg_cohesion"], "avg_fit": plan["avg_fit"],
            "versus_random_seating": plan["baseline"],
            "lowest_fit_guests": [{"guest": n, "fit": f, "table": plan["guests"][n]["table"]} for f, n in lows],
            "isolated": plan["isolated"], "unavoidable_isolated": plan["unavoidable_isolated"],
            "guests_without_taste_data": plan["no_data"],
            "constraints": [{"text": c["text"], "ok": c["ok"]} for c in plan["constraints"]],
            "all_constraints_ok": plan["all_constraints_ok"],
            "note": "Next: explain_table for any table you want to inspect, then table_card for each table, then present_plan.",
        }

    # -- explain_table ---------------------------------------------------------------
    def _need_plan(self):
        if self.plan is None:
            raise ToolError("Call seat_guests first.")
        if self.stale:
            raise ToolError("Constraints or profiles changed since the last seating. Call seat_guests again.")

    def _table(self, table):
        self._need_plan()
        try:
            i = int(table)
        except (TypeError, ValueError):
            raise ToolError("table must be a number.") from None
        if not 1 <= i <= len(self.plan["tables"]):
            raise ToolError(f"Table {i} does not exist; there are {len(self.plan['tables'])} tables.")
        return self.plan["tables"][i - 1]

    def _favs(self, name):
        return [e["name"] for e in (self.profiles.get(name) or {}).get("entities", [])]

    def _topic_material(self, share):
        """A shared tag with the favourites (of the guests who share it) that can back a card."""
        tag = self.tag_name(share["tag_id"])
        rows = []
        for m in share["members"]:
            for e in (self.profiles.get(m) or {}).get("entities", []):
                carried = share["tag_id"] in (e.get("tag_ids") or []) or tag in e["tags"]
                rows.append((not carried, e["name"], m))
        if any(not untagged for untagged, _n, _m in rows):
            rows = [r for r in rows if not r[0]]    # only favourites that themselves carry the tag back a claim
        ents, seen = [], {}
        for _untagged, name, m in rows:
            if name not in seen:
                seen[name] = {"entity": name, "listed_by": []}
                ents.append(seen[name])
            seen[name]["listed_by"].append(m)
        return {"tag": tag, "coverage": share["coverage"], "members": share["members"], "favorites": ents[:8]}

    def t_explain_table(self, table):
        t = self._table(table)
        guests = []
        for n in t["guests"]:
            g = self.plan["guests"][n]
            row = {"name": n, "fit": g["fit"], "favorites": self._favs(n), "closest_mate": g["best_mate"],
                   "shares_with_table": [self.tag_name(s["tag_id"]) for s in g["shared_tags"]]}
            if g["no_data"]:
                row["note"] = "No taste data (no favourite matched in Qloo); seated with a neutral score."
            elif g["isolated"]:
                row["note"] = "Isolated: nobody at this table is a close match."
            guests.append(row)
        by_fav = {}
        for n in t["guests"]:
            for f in self._favs(n):
                by_fav.setdefault(f, []).append(n)
        return {"table": t["index"], "label": t["label"], "cohesion": t["cohesion"], "min_fit": t["min_fit"],
                "guests": guests, "shared_tags": [self._topic_material(s) for s in t["shared_tags"]],
                "favorites_listed_by_several": [{"entity": e, "guests": g} for e, g in by_fav.items() if len(g) > 1][:6],
                "note": "A table_card may use only the tags in shared_tags and the favorites listed there."}

    # -- table_card ------------------------------------------------------------------
    def _check_card(self, t, title, opener, topics, ask):
        members = t["guests"]
        shared_names = [self.tag_name(s["tag_id"]) for s in t["shared_tags"]]
        if shared_names and not topics:
            raise ToolError(f"Give 1-3 topics from this table's shared tags: {shared_names}.")
        if not shared_names and topics:
            raise ToolError("This table shares no tags, so the card cannot have topics; send topics=[] and only invite "
                            "the guests to compare their listed favourites.")
        clean = []
        allowed_tags = {_norm(n) for n in shared_names}    # any tag this table shares may be named in the text
        for tp in topics[:4]:
            if not isinstance(tp, dict):
                raise ToolError("Each topic must be an object with tag, because, entities, guests.")
            share = self._resolve_tag(t, tp.get("tag", ""))
            if share is None:
                raise ToolError(f"Ungrounded: '{tp.get('tag')}' is not a tag shared by table {t['index']}. "
                                f"Shared tags: {shared_names}.")
            mat = self._topic_material(share)
            fav_ok = {_norm(f["entity"]): f["entity"] for f in mat["favorites"]}
            ents = []
            for e in _list(tp.get("entities")):
                if _norm(e) not in fav_ok:
                    raise ToolError(f"Ungrounded: '{e}' is not a favorite of a guest at table {t['index']} who shares "
                                    f"'{mat['tag']}'. Allowed: {list(fav_ok.values())}.")
                ents.append(fav_ok[_norm(e)])
            who = []
            for gname in _list(tp.get("guests")):
                r = self._resolve_guest(gname)
                if r not in mat["members"]:
                    raise ToolError(f"Ungrounded: {r} does not share '{mat['tag']}' at table {t['index']}. "
                                    f"Guests who do: {mat['members']}.")
                who.append(r)
            clean.append({"tag": mat["tag"], "tag_id": share["tag_id"], "because": str(tp.get("because", ""))[:200],
                          "entities": ents, "guests": who})
            allowed_tags.add(_norm(mat["tag"]))
        text = " ".join([title, opener, ask] + [c["because"] for c in clean])
        table_favs = {_norm(f) for n in members for f in self._favs(n)}
        for n in self.profiles:
            for f in self._favs(n):
                if _norm(f) not in table_favs and _mentions_exact(text, f):
                    raise ToolError(f"Ungrounded: the card mentions '{f}', which nobody at table {t['index']} listed.")
        scan = text   # a listed favourite may contain a tag word ("Street Food" the show); it is not a tag claim
        for f in sorted({f for n in members for f in self._favs(n)}, key=len, reverse=True):
            scan = scan.replace(f, " ")
        # Live Qloo vectors hold thousands of tags, many of them everyday words ("love", "family"), so only the tags
        # the plan shows on a few tables are policed: a card may not borrow another table's distinctive tag. A tag
        # shown on three or more tables is too common to be a claim about this table.
        shown = {}
        for x in self.plan["tables"]:
            for name in {self.tag_name(s["tag_id"]) for s in x["shared_tags"]}:
                shown[name] = shown.get(name, 0) + 1
        for tname in sorted(n for n, c in shown.items() if c <= 2):
            if len(_norm(tname)) >= 4 and _norm(tname) not in allowed_tags and _mentions(scan, tname):
                raise ToolError(f"Ungrounded: the card mentions the tag '{tname}', which this table does not share. "
                                f"Rewrite it without the word '{tname}' (title, opener, ask and every 'because' are "
                                f"checked). Shared tags: {shared_names}.")
        for other in self.names:
            if other in members:
                continue
            first = (_norm(other).split() or [""])[0]
            unique_first = len(first) > 2 and sum(1 for x in self.names if (_norm(x).split() or [""])[0] == first) == 1
            if _mentions(text, other) or (unique_first and _mentions(text, first)):
                raise ToolError(f"Ungrounded: the card mentions {other}, who is not at table {t['index']}.")
        return clean

    def t_table_card(self, table, opener, topics, ask, title=None):
        t = self._table(table)
        topics = topics if isinstance(topics, list) else []
        title = str(title or f"Conversation starters for {t['label']}")[:60]
        opener, ask = str(opener).strip()[:260], str(ask).strip()[:200]
        if not opener or not ask:
            raise ToolError("A card needs an opener and a question.")
        clean = self._check_card(t, title, opener, topics, ask)
        self.cards[t["index"]] = {"table": t["index"], "title": title, "opener": opener, "topics": clean, "ask": ask,
                                  "source": "agent"}
        return {"ok": True, "table": t["index"], "cards_done": sorted(self.cards),
                "tables_without_card": [x["index"] for x in self.plan["tables"] if x["index"] not in self.cards]}

    def auto_card(self, t):
        """Deterministic grounded card, used when the model did not write one for a table."""
        topics = []
        for share in t["shared_tags"][:2]:
            mat = self._topic_material(share)
            ents = [f["entity"] for f in mat["favorites"][:2]]
            who = mat["members"][:3]
            because = (f"{', '.join(who)} all lean towards {mat['tag']}" +
                       (f" (they listed {' and '.join(ents)})." if ents else "."))
            topics.append({"tag": mat["tag"], "tag_id": share["tag_id"], "because": because[:200],
                           "entities": ents, "guests": who})
        if topics:
            opener = f"This table shares a taste for {' and '.join(tp['tag'] for tp in topics)}."
            ask = f"What is the first {topics[0]['tag']} favorite you would recommend to someone here?"
        else:
            opener = "This is a mixed table: nobody shares a tag with several others, so compare your favorites."
            ask = "What is one favorite you would press on a stranger at this table?"
        return {"table": t["index"], "title": f"Conversation starters for {t['label']}", "opener": opener,
                "topics": topics, "ask": ask, "source": "auto"}

    # -- ask_organizer / present_plan ---------------------------------------------------
    def t_ask_organizer(self, question, options=None):
        self.final = ("question", {"question": str(question)[:300], "options": [o[:80] for o in _list(options)][:4]})
        return {"ok": True, "status": "Question sent to the organiser. Stop now."}

    def _why(self, g):
        if g["no_data"]:
            return "No favourite matched in Qloo, so this guest was seated by constraints and balance only."
        bits = [f"Fit {g['fit']:.2f} with the table."]
        if g["shared_tags"]:
            bits.append("Shares " + ", ".join(self.tag_name(s["tag_id"]) for s in g["shared_tags"][:3]) + ".")
        if g["best_mate"]:
            bits.append(f"Closest to {g['best_mate']['name']} ({g['best_mate']['compat']:.2f}).")
        if g["isolated"]:
            bits.append("Weak match here: nobody at the table is a close fit.")
        elif g["unavoidable"]:
            bits.append("Few people in this party share their taste, so a low fit is unavoidable.")
        return " ".join(bits)

    def build_final(self, headline, notes=None, assumptions=None):
        plan = self.plan
        cards, auto = [], []
        for t in plan["tables"]:
            c = self.cards.get(t["index"])
            if c is None:
                c, _ = self.auto_card(t), auto.append(t["index"])
            cards.append(c)
        tables = [dict(t, shared_tags=[dict(s, tag=self.tag_name(s["tag_id"])) for s in t["shared_tags"]])
                  for t in plan["tables"]]
        guests = []
        for n in self.names:
            g = plan["guests"][n]
            p = self.profiles.get(n) or {}
            n_ok = len(p.get("entities", []))
            guests.append(dict(g, name=n, group=self.guests[n]["group"],
                               shared_tags=[dict(s, tag=self.tag_name(s["tag_id"])) for s in g["shared_tags"]],
                               favorites=[e["name"] for e in p.get("entities", [])], unmatched=p.get("unmatched", []),
                               why=self._why(g), confidence="none" if g["no_data"] else ("low" if n_ok <= 1 else "ok")))
        unapplied = self.unapplied_requirements()
        notes_all = []
        if self.no_requirements and self.requirements and not self.constraint_log:
            notes_all.append("No requirement was applied: the agent judged the organiser's notes to contain no seating "
                             f"requirement ({self.no_requirements}). Notes given: \"{self.requirements[:160]}\"")
        if self.auto_applied:
            notes_all.append("Applied automatically from the organiser's notes because the AI model could not finish: "
                             + "; ".join(f"\"{t}\"" for t in self.auto_applied)[:300])
        notes_all = unapplied + notes_all + [str(x)[:240] for x in _list(notes)][:6]
        if auto:
            notes_all.append(f"Table {', '.join(map(str, auto))} got an automatic card built from the shared Qloo tags."
                             if len(auto) == 1 else
                             f"Tables {', '.join(map(str, auto))} got automatic cards built from their shared Qloo tags.")
        assumptions_all = list(dict.fromkeys(self.assumptions + [str(a)[:200] for a in _list(assumptions)]))[:10]
        keys = ("objective", "min_fit", "avg_fit", "avg_cohesion", "baseline", "isolated", "unavoidable_isolated",
                "no_data", "close_threshold", "table_sizes", "seed")
        return {"headline": str(headline)[:200], "tables": tables, "guests": guests, "cards": cards,
                "constraints": plan["constraints"] + [{"type": "unapplied", "guests": [], "ok": False, "text": u}
                                                      for u in unapplied],
                "constraint_log": self.constraint_log, "unapplied_requirements": unapplied,
                "all_constraints_ok": plan["all_constraints_ok"] and not unapplied, "metrics": {k: plan[k] for k in keys},
                "notes": notes_all, "assumptions": assumptions_all, "table_size": self.table_size}

    def t_present_plan(self, headline, notes=None, assumptions=None):
        self._need_plan()
        self.final = ("plan", self.build_final(headline, notes, assumptions))
        return {"ok": True, "status": "Plan delivered. Stop now."}

    # -- requirements the plan does not honour ----------------------------------------------
    def unapplied_requirements(self):
        """Plain-language lines for every organiser requirement that is NOT reflected in the seating."""
        done = {_norm(c["text"]) for c in self.constraint_log}
        out = [f"Requirement NOT applied: \"{r['text']}\" ({r['error']})" for r in self.rejected
               if _norm(r["text"]) not in done]
        out += [f"Requirement NOT applied: \"{t}\" (could not be interpreted automatically)"
                for t in self.unapplied_extra]
        if self.requirements and not self.constraint_log and not self.no_requirements and not out:
            out.append(f"The organiser's requirements were NOT applied: \"{self.requirements[:200]}\"")
        return out

    def _auto_apply_requirements(self):
        """Fallback when the model never applied (all of) the organiser's requirements: apply what the keyless
        parser understands, and remember what it could not so the plan says so."""
        if not self.requirements or self.no_requirements:
            return
        tried = [_norm(c["text"]) for c in self.constraint_log] + [_norm(r["text"]) for r in self.rejected]
        for sentence in split_requirements(self.requirements):
            n = _norm(sentence)
            if any(t and (t in n or n in t) for t in tried):    # the model already handled this sentence
                continue
            args = parse_constraint(sentence)
            if args is None:
                self.unapplied_extra.append(sentence)
                continue
            try:
                self.t_apply_constraint(**args)
                self.auto_applied.append(sentence)
            except (ToolError, seating.SeatingError):
                pass   # recorded in self.rejected, reported by unapplied_requirements()

    # -- fallback --------------------------------------------------------------------
    def auto_plan(self, reason, profile_if_empty=False):
        """If the model runs out of turns, finish with what the tools already computed. With profile_if_empty (the
        model was unreachable before it did anything) the taste profiles are built here first."""
        if not self.profiles and profile_if_empty:
            try:
                for _ in range(10):
                    out, err = self.run("build_taste_profiles", {})
                    if err or not out.get("remaining"):
                        break
            except FatalToolError:
                return None
        if not self.profiles:
            return None
        self._fallback = True
        self._auto_apply_requirements()
        if self.plan is None or self.stale:
            try:
                self.t_seat_guests()
            except (ToolError, seating.SeatingError):
                return None
        self.final = ("plan", self.build_final(
            f"Seating plan ({reason}).", [],
            [f"The agent stopped early ({reason}); the plan was assembled automatically from its tool results."]))
        return self.final[1]
