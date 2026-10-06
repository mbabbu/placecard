"""Tool behaviour: profiles, constraint validation, seating, grounded cards, present_plan. SAMPLE Qloo, no network."""
import unittest
from unittest import mock

from placecard import tools
from placecard.qloo import QlooAuthError, QlooError
from placecard.sample_qloo import SampleQloo
from placecard.tools import ToolBox

PARTY = [
    {"name": "Kai Park", "favorites": ["Parasite", "BTS"], "group": "Park family"},
    {"name": "Lee Park", "favorites": ["Squid Game", "NewJeans"], "group": "Park family"},
    {"name": "Min Choi", "favorites": ["Parasite", "Past Lives"], "group": "Friends"},
    {"name": "Jo Han", "favorites": ["Train to Busan", "BTS"], "group": "Friends"},
    {"name": "Tess Ng", "favorites": ["Silicon Valley", "Acquired"], "group": "Work"},
    {"name": "Uma Rao", "favorites": ["Zero to One", "Mr. Robot"], "group": "Work"},
    {"name": "Vic Lim", "favorites": ["Lex Fridman Podcast", "Silicon Valley"], "group": "Work"},
    {"name": "Wen Xu", "favorites": ["Acquired", "The Social Network"], "group": "Work"},
]


def box(**kw):
    kw.setdefault("table_size", 4)
    return ToolBox(kw.pop("qloo", None) or SampleQloo(), kw.pop("guests", PARTY), **kw)


def seated(**kw):
    b = box(**kw)
    ok(b.run("build_taste_profiles", {}))
    ok(b.run("seat_guests", {}))
    return b


def ok(res):
    out, err = res
    assert not err, out
    return out


def bad(res, text=""):
    out, err = res
    assert err, out
    assert text in out["error"], out["error"]
    return out["error"]


def table_of(b, name):
    return b.plan["guests"][name]["table"]


class Profiles(unittest.TestCase):
    def test_builds_vectors_and_reports_ambiguity_assumption(self):
        b = box(guests=[{"name": "A", "favorites": ["Parasite", "BTS"]}, {"name": "B", "favorites": ["Spirited Away"]}])
        out = ok(b.run("build_taste_profiles", {}))
        self.assertEqual((out["profiled_total"], out["remaining"]), (2, 0))
        self.assertTrue(any("Parasite" in a and "2019" in a for a in out["assumptions"]))
        self.assertIn("Korean Culture", [t["name"] for t in b.profiles["A"]["vector"].values()])
        self.assertEqual([e["name"] for e in b.profiles["A"]["entities"]], ["Parasite", "BTS"])
        self.assertEqual(b.profiles["A"]["entities"][0]["disambiguation"], "2019, Bong Joon-ho")

    def test_unmatched_and_no_data_guests(self):
        b = box(guests=[{"name": "A", "favorites": ["BTS", "Zxqv Obscure"]}, {"name": "B", "favorites": ["Qwerty Nope"]}])
        out = ok(b.run("build_taste_profiles", {}))
        self.assertEqual({(u["guest"], u["favorite"]) for u in out["unmatched_favorites"]},
                         {("A", "Zxqv Obscure"), ("B", "Qwerty Nope")})
        self.assertEqual(out["guests_without_taste_data"], ["B"])
        self.assertEqual(b.profiles["B"]["status"], "no_data")

    def test_batches_and_remaining(self):
        with mock.patch.object(tools, "PROFILE_BATCH", 3):
            b = box()
            first = ok(b.run("build_taste_profiles", {}))
            self.assertEqual((first["profiled_now"], first["remaining"]), (3, 5))
            self.assertIn("again", first["note"])
            bad(b.run("seat_guests", {}), "no profile yet")
            ok(b.run("build_taste_profiles", {}))
            last = ok(b.run("build_taste_profiles", {}))
            self.assertEqual((last["profiled_total"], last["remaining"]), (8, 0))

    def test_names_subset_and_unknown_name(self):
        b = box()
        out = ok(b.run("build_taste_profiles", {"names": "Kai, Tess Ng"}))
        self.assertEqual(out["profiled_now"], 2)
        bad(b.run("build_taste_profiles", {"names": ["Nobody"]}), "No guest")

    def test_repeated_favourites_are_searched_once_and_names_never_reach_qloo(self):
        seen = []

        class Rec(SampleQloo):
            def search(self, query, entity_type=None, take=5):
                seen.append(query)
                return super().search(query, entity_type, take)

            def taste_tags(self, ids, *a, **k):
                seen.append(tuple(ids))
                return super().taste_tags(ids, *a, **k)

        b = box(qloo=Rec())
        ok(b.run("build_taste_profiles", {}))
        searches = [s for s in seen if isinstance(s, str)]
        self.assertEqual(len(searches), len(set(searches)))                   # "Parasite" etc. only once
        blob = " ".join(map(str, seen)).lower()
        for g in PARTY:
            self.assertNotIn(g["name"].lower(), blob)
            self.assertNotIn(g["name"].split()[0].lower(), blob)

    def test_budget_exhaustion_degrades_gracefully(self):
        b = box(max_qloo_calls=6)
        out = ok(b.run("build_taste_profiles", {}))
        self.assertIn("budget", out["note"])
        self.assertLess(out["profiled_total"], 8)
        self.assertEqual(len(ok(b.run("seat_guests", {}))["tables"]), 2)     # still seats; the rest are neutral

    def test_per_guest_qloo_error_does_not_abort_the_batch(self):
        class Flaky(SampleQloo):
            def taste_tags(self, ids, *a, **k):
                if any(i == self.search("Zero to One")[0]["id"] for i in ids):
                    raise QlooError("Qloo server error (503).", 503, retryable=True)
                return super().taste_tags(ids, *a, **k)

        b = box(qloo=Flaky())
        out = ok(b.run("build_taste_profiles", {}))
        self.assertEqual(out["failed"], ["Uma Rao"])
        self.assertEqual(out["profiled_total"], 8)
        self.assertEqual(b.profiles["Uma Rao"]["status"], "failed")

    def test_bad_key_is_fatal(self):
        class Denied(SampleQloo):
            def search(self, *a, **k):
                raise QlooAuthError("Qloo rejected the API key (401).", 401)

        with self.assertRaises(tools.FatalToolError):
            box(qloo=Denied()).run("build_taste_profiles", {})

    def test_close_but_not_exact_match_is_flagged(self):
        class Fuzzy(SampleQloo):
            def search(self, query, entity_type=None, take=5):
                from placecard import sample_qloo as sq
                return [sq._entity(c) for c in sq.CATALOG if c["name"] == "Spirited Away"]

        b = box(qloo=Fuzzy(), guests=[{"name": "A", "favorites": ["Spirited Awy", "Totally Different Thing"]}])
        out = ok(b.run("build_taste_profiles", {}))
        self.assertTrue(any("closest Qloo match" in a for a in out["assumptions"]))
        self.assertEqual(out["unmatched_favorites"][0]["favorite"], "Totally Different Thing")

    def test_threaded_live_path_matches_sequential(self):
        class Live(SampleQloo):
            sample = False

        a, c = box(qloo=Live(), workers=4), box()
        ok(a.run("build_taste_profiles", {}))
        ok(c.run("build_taste_profiles", {}))
        self.assertEqual({n: p["vector"] for n, p in a.profiles.items()}, {n: p["vector"] for n, p in c.profiles.items()})


class Constraints(unittest.TestCase):
    def setUp(self):
        self.b = box()

    def test_group_names_are_matched_fuzzily(self):
        out = ok(self.b.run("apply_constraint", {"text": "Keep the Parks together", "kind": "together", "group": "Parks"}))
        self.assertEqual(out["added"]["guests"], ["Kai Park", "Lee Park"])
        self.assertEqual(out["added"]["group"], "Park family")
        self.assertEqual(self.b.constraints["together"], [["Kai Park", "Lee Park"]])

    def test_first_names_resolve_when_unique(self):
        out = ok(self.b.run("apply_constraint", {"text": "Tess and Uma apart", "kind": "apart", "guests": ["Tess", "uma"]}))
        self.assertEqual(out["added"]["guests"], ["Tess Ng", "Uma Rao"])

    def test_ambiguous_and_unknown_names(self):
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "apart", "guests": ["Park", "Tess"]}), "ambiguous")
        err = bad(self.b.run("apply_constraint", {"text": "x", "kind": "head", "guests": ["Tes Ng"]}), "No guest")
        self.assertIn("Tess Ng", err)                                             # did-you-mean
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "together", "group": "Aliens"}), "No group")

    def test_invalid_kinds_and_arities(self):
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "mingle", "guests": ["Tess"]}), "kind must")
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "together", "guests": ["Tess"]}), "at least two")
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "fixed", "guests": ["Tess"]}), "table number")
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "head"}), "at least one")
        bad(self.b.run("apply_constraint", {"text": "x", "kind": "apart", "group": "Work"}), "only supported for 'together'")

    def test_contradictions_are_rejected_and_not_stored(self):
        ok(self.b.run("apply_constraint", {"text": "t", "kind": "together", "guests": ["Tess", "Uma"]}))
        bad(self.b.run("apply_constraint", {"text": "a", "kind": "apart", "guests": ["Tess", "Uma"]}), "together and apart")
        self.assertEqual(self.b.constraints["apart"], [])
        self.assertEqual(len(self.b.constraint_log), 1)
        bad(self.b.run("apply_constraint", {"text": "f", "kind": "fixed", "guests": ["Tess"], "table": 9}), "does not exist")

    def test_capacity_is_checked(self):
        bad(self.b.run("apply_constraint", {"text": "everyone", "kind": "together",
                                            "guests": [g["name"] for g in PARTY[:5]]}), "at most")

    def test_head_fixed_and_log(self):
        ok(self.b.run("apply_constraint", {"text": "Kai at the head table", "kind": "head", "guests": ["Kai"]}))
        ok(self.b.run("apply_constraint", {"text": "Tess on table 2", "kind": "fixed", "guests": ["Tess"], "table": "2"}))
        self.assertEqual(self.b.constraints["fixed"], {"Kai Park": 1, "Tess Ng": 2})
        self.assertEqual([c["text"] for c in self.b.constraint_log], ["Kai at the head table", "Tess on table 2"])


class RequirementsGuard(unittest.TestCase):
    NOTES = "Work should be spread across tables. Keep Tess and Uma apart."

    def boxed(self, **kw):
        b = box(requirements=kw.pop("requirements", self.NOTES), **kw)
        ok(b.run("build_taste_profiles", {}))
        return b

    def test_seat_guests_is_refused_until_a_constraint_is_applied(self):
        b = self.boxed()
        err = bad(b.run("seat_guests", {}), "apply_constraint")
        self.assertIn("Work should be spread", err)       # the organiser's words are echoed back
        self.assertIn("kind='none'", err)
        self.assertIsNone(b.plan)
        ok(b.run("apply_constraint", {"text": "Work should be spread across tables.", "kind": "spread", "group": "Work"}))
        out = ok(b.run("seat_guests", {}))
        self.assertTrue(out["all_constraints_ok"])

    def test_without_notes_no_constraint_is_needed(self):
        b = box(requirements="")
        ok(b.run("build_taste_profiles", {}))
        ok(b.run("seat_guests", {}))

    def test_explicit_none_unlocks_seating_and_is_disclosed(self):
        b = self.boxed(requirements="Make the evening magical.")
        bad(b.run("seat_guests", {}), "apply_constraint")
        ok(b.run("apply_constraint", {"text": "Only a mood, no seating rule", "kind": "none"}))
        ok(b.run("seat_guests", {}))
        ok(b.run("present_plan", {"headline": "h"}))
        plan = b.final[1]
        self.assertTrue(any("judged the organiser's notes to contain no seating requirement" in n for n in plan["notes"]))
        self.assertEqual(plan["unapplied_requirements"], [])

    def test_a_refused_attempt_does_not_deadlock_but_is_reported_as_not_applied(self):
        b = self.boxed()
        bad(b.run("apply_constraint", {"text": "Foo and Bar apart", "kind": "apart", "guests": ["Foo", "Bar"]}), "No guest")
        ok(b.run("seat_guests", {}))
        ok(b.run("present_plan", {"headline": "h", "notes": ["All requirements were met."]}))
        plan = b.final[1]
        self.assertFalse(plan["all_constraints_ok"])
        self.assertIn("Requirement NOT applied", plan["notes"][0])
        self.assertIn("Foo and Bar apart", plan["notes"][0])
        self.assertTrue(any(c["type"] == "unapplied" and not c["ok"] for c in plan["constraints"]))

    def test_a_fixed_retry_clears_the_not_applied_flag(self):
        b = self.boxed()
        bad(b.run("apply_constraint", {"text": "Tess and Uma apart", "kind": "apart", "guests": ["Tess", "Umaa"]}), "No guest")
        ok(b.run("apply_constraint", {"text": "Tess and Uma apart", "kind": "apart", "guests": ["Tess", "Uma"]}))
        ok(b.run("seat_guests", {}))
        ok(b.run("present_plan", {"headline": "h"}))
        self.assertEqual(b.final[1]["unapplied_requirements"], [])

    def test_repeating_a_constraint_is_idempotent(self):
        b = self.boxed()
        args = {"text": "Tess and Uma apart", "kind": "apart", "guests": ["Tess", "Uma"]}
        ok(b.run("apply_constraint", args))
        out = ok(b.run("apply_constraint", dict(args, guests=["Uma", "Tess"])))
        self.assertIn("Already applied", out["note"])
        self.assertEqual(len(b.constraint_log), 1)
        self.assertEqual(len(b.constraints["apart"]), 1)

    def test_fallback_applies_what_it_understands_and_flags_the_rest(self):
        b = self.boxed(requirements=self.NOTES + " Make the evening magical.")
        plan = b.auto_plan("the AI model was unavailable")
        self.assertEqual(sorted(c["kind"] for c in b.constraint_log), ["apart", "spread"])
        self.assertTrue(any("Applied automatically" in n for n in plan["notes"]))
        self.assertEqual(len(plan["unapplied_requirements"]), 1)
        self.assertIn("Make the evening magical", plan["notes"][0])
        self.assertFalse(plan["all_constraints_ok"])          # never claims the notes were all met
        by = {g["name"]: g["table"] for g in plan["guests"]}
        self.assertNotEqual(by["Tess Ng"], by["Uma Rao"])
        work = [by[n] for n in ("Tess Ng", "Uma Rao", "Vic Lim", "Wen Xu")]
        self.assertLessEqual(max(work.count(t) for t in set(work)), 2)

    def test_fallback_with_clean_notes_reports_them_as_applied_and_met(self):
        b = self.boxed()
        plan = b.auto_plan("the AI model was unavailable")
        self.assertEqual(plan["unapplied_requirements"], [])
        self.assertTrue(plan["all_constraints_ok"])
        self.assertEqual(len(plan["constraints"]), 2)

    def test_fallback_that_applies_nothing_says_so(self):
        b = self.boxed(requirements="Make the evening magical.")
        plan = b.auto_plan("turn limit reached")
        self.assertFalse(plan["all_constraints_ok"])
        self.assertIn("NOT applied", plan["notes"][0])
        self.assertIn("magical", plan["notes"][0])

    def test_fallback_does_not_redo_requirements_the_model_applied(self):
        b = self.boxed()
        ok(b.run("apply_constraint", {"text": "Keep Tess and Uma apart.", "kind": "apart", "guests": ["Tess", "Uma"]}))
        plan = b.auto_plan("the AI model was unavailable")
        self.assertEqual(len(b.constraint_log), 2)             # only the spread one was added
        self.assertEqual(b.auto_applied, ["Work should be spread across tables."])
        self.assertEqual(plan["unapplied_requirements"], [])


class SpreadConstraint(unittest.TestCase):
    def test_spread_a_group_and_the_cap(self):
        b = box()
        out = ok(b.run("apply_constraint", {"text": "Spread Work out", "kind": "spread", "group": "Work"}))
        self.assertEqual(out["added"]["guests"], ["Tess Ng", "Uma Rao", "Vic Lim", "Wen Xu"])
        self.assertEqual(b.constraints["spread"], [["Tess Ng", "Uma Rao", "Vic Lim", "Wen Xu"]])
        ok(b.run("build_taste_profiles", {}))
        out = ok(b.run("seat_guests", {}))
        self.assertTrue(out["all_constraints_ok"])
        self.assertIn("at most 2 per table", out["constraints"][0]["text"])
        work = [table_of(b, n) for n in ("Tess Ng", "Uma Rao", "Vic Lim", "Wen Xu")]
        self.assertEqual(sorted(work), [1, 1, 2, 2])

    def test_spread_named_guests_and_arity(self):
        b = box()
        ok(b.run("apply_constraint", {"text": "x", "kind": "spread", "guests": ["Kai", "Lee"]}))
        bad(b.run("apply_constraint", {"text": "x", "kind": "spread", "guests": ["Kai"]}), "at least two")

    def test_impossible_spread_is_refused(self):
        b = box()
        ok(b.run("apply_constraint", {"text": "t", "kind": "together", "guests": ["Tess", "Uma", "Vic"]}))
        bad(b.run("apply_constraint", {"text": "s", "kind": "spread", "group": "Work"}), "at most 2")
        self.assertEqual(b.constraints["spread"], [])


class Seating(unittest.TestCase):
    def test_requires_profiles(self):
        bad(box().run("seat_guests", {}), "build_taste_profiles")

    def test_seats_taste_clusters_together_and_honours_constraints(self):
        b = box()
        ok(b.run("build_taste_profiles", {}))
        ok(b.run("apply_constraint", {"text": "t", "kind": "together", "group": "Park family"}))
        ok(b.run("apply_constraint", {"text": "a", "kind": "apart", "guests": ["Kai", "Min"]}))
        out = ok(b.run("seat_guests", {}))
        self.assertTrue(out["all_constraints_ok"])
        by = {g: t["table"] for t in out["tables"] for g in t["guests"]}
        self.assertEqual(by["Kai Park"], by["Lee Park"])
        self.assertNotEqual(by["Kai Park"], by["Min Choi"])

    def test_unconstrained_party_splits_into_its_taste_clusters(self):
        out = ok(seated().run("seat_guests", {}))
        by = {g: t["table"] for t in out["tables"] for g in t["guests"]}
        self.assertEqual(len({by[n] for n in ("Kai Park", "Lee Park", "Min Choi", "Jo Han")}), 1)
        self.assertEqual(len({by[n] for n in ("Tess Ng", "Uma Rao", "Vic Lim", "Wen Xu")}), 1)
        self.assertEqual(out["isolated"], [])
        self.assertIn("Technology", " ".join(" ".join(t["shared_tags"]) for t in out["tables"]))

    def test_parameters_are_validated(self):
        b = seated()
        bad(b.run("seat_guests", {"table_size": 40}), "between 2 and 14")
        bad(b.run("seat_guests", {"table_size": "big"}), "number")
        bad(b.run("seat_guests", {"seed": "x"}), "number")
        out = ok(b.run("seat_guests", {"table_size": 8, "seed": 5}))
        self.assertEqual(len(out["tables"]), 1)

    def test_over_full_fixed_table_is_rejected_when_added(self):
        b = box(table_size=4)
        ok(b.run("build_taste_profiles", {}))
        ok(b.run("apply_constraint", {"text": "x", "kind": "fixed", "guests": ["Kai", "Lee", "Min", "Jo"], "table": 2}))
        bad(b.run("apply_constraint", {"text": "y", "kind": "fixed", "guests": ["Tess"], "table": 2}), "Too many")

    def test_changing_constraints_makes_the_plan_stale(self):
        b = seated()
        ok(b.run("explain_table", {"table": 1}))
        ok(b.run("apply_constraint", {"text": "h", "kind": "head", "guests": ["Tess"]}))
        bad(b.run("explain_table", {"table": 1}), "seat_guests again")
        bad(b.run("present_plan", {"headline": "x"}), "seat_guests again")
        ok(b.run("seat_guests", {}))
        ok(b.run("present_plan", {"headline": "x"}))


class Cards(unittest.TestCase):
    def setUp(self):
        self.b = seated()
        self.k = table_of(self.b, "Kai Park")
        self.t = table_of(self.b, "Tess Ng")
        self.kt = self.b.run("explain_table", {"table": self.k})[0]
        self.tt = self.b.run("explain_table", {"table": self.t})[0]

    def card(self, **over):
        topic = self.kt["shared_tags"][0]
        args = {"table": self.k, "opener": "You all lean towards " + topic["tag"] + ".",
                "topics": [{"tag": topic["tag"], "because": "Several of you listed related favourites.",
                            "entities": [topic["favorites"][0]["entity"]], "guests": topic["members"][:2]}],
                "ask": "What would you recommend first?"}
        args.update(over)
        return self.b.run("table_card", args)

    def test_explain_table_is_grounded(self):
        names = {g["name"] for g in self.kt["guests"]}
        self.assertEqual(names, {"Kai Park", "Lee Park", "Min Choi", "Jo Han"})
        self.assertIn("Korean Culture", [s["tag"] for s in self.kt["shared_tags"]])
        self.assertIn("Parasite", [f["entity"] for s in self.kt["shared_tags"] for f in s["favorites"]])
        self.assertTrue(all(0 <= g["fit"] <= 1 for g in self.kt["guests"]))
        bad(self.b.run("explain_table", {"table": 9}), "does not exist")
        bad(self.b.run("explain_table", {"table": "x"}), "number")

    def test_valid_card_is_saved(self):
        out = ok(self.card())
        self.assertIn(self.k, out["cards_done"])
        saved = self.b.cards[self.k]
        self.assertEqual(saved["source"], "agent")
        self.assertEqual(saved["topics"][0]["tag"], self.kt["shared_tags"][0]["tag"])

    def test_ungrounded_tag_is_rejected(self):
        err = bad(self.card(topics=[{"tag": "Jazz", "because": "x"}]), "Ungrounded")
        self.assertIn("Shared tags", err)
        mine = [x["tag"] for x in self.kt["shared_tags"]]
        other = [s["tag"] for s in self.tt["shared_tags"] if s["tag"] not in mine][0]
        bad(self.card(topics=[{"tag": other, "because": "x"}]), "not a tag shared")

    def test_entity_from_another_table_is_rejected(self):
        topic = self.kt["shared_tags"][0]
        bad(self.card(topics=[{"tag": topic["tag"], "because": "x", "entities": ["Silicon Valley"]}]), "not a favorite")
        bad(self.card(opener="You all love Silicon Valley."), "Silicon Valley")

    def test_guest_from_another_table_is_rejected(self):
        topic = self.kt["shared_tags"][0]
        bad(self.card(topics=[{"tag": topic["tag"], "because": "x", "guests": ["Tess Ng"]}]), "does not share")
        bad(self.card(ask="Would Tess agree?"), "Tess Ng")
        bad(self.card(opener="Kai and Uma Rao will bond."), "Uma Rao")

    def test_casual_mention_of_an_unshared_tag_is_rejected_but_listed_favourites_are_fine(self):
        bad(self.card(ask="Which Technology podcast do you follow?"), "Technology")
        # a favourite whose title contains a tag word is not a tag claim
        self.b.profiles["Kai Park"]["entities"].append({"id": "x", "name": "Street Food", "type": "tv_show", "tags": [],
                                                       "disambiguation": ""})
        self.b.tag_names["urn:tag:sample:street_food"] = "Street Food"
        ok(self.card(opener="Kai listed Street Food, you all lean towards " + self.kt["shared_tags"][0]["tag"] + "."))

    def test_everyday_word_that_is_also_a_title_is_allowed_in_lowercase(self):
        self.b.profiles["Tess Ng"]["entities"].append({"id": "f", "name": "Friends", "type": "tv_show", "tags": [],
                                                      "disambiguation": ""})
        ok(self.card(ask="What would make friends fastest at this table?"))     # lowercase: no claim about the show
        bad(self.card(ask="Is Friends your comfort show?"), "Friends")          # exact title: nobody here listed it

    def test_topics_required_and_text_limits(self):
        bad(self.card(topics=[]), "1-3 topics")
        bad(self.card(opener=" "), "opener")
        ok(self.card(opener="A" * 900, ask="Q" * 900))
        self.assertLessEqual(len(self.b.cards[self.k]["opener"]), 260)

    def test_table_without_shared_tags_gets_topicless_card(self):
        b = box(guests=[{"name": "A1", "favorites": ["BTS"]}, {"name": "B1", "favorites": ["Metallica"]}],
                table_size=2)
        ok(b.run("build_taste_profiles", {}))
        ok(b.run("seat_guests", {}))
        bad(b.run("table_card", {"table": 1, "opener": "Hi", "ask": "Q?", "topics": [{"tag": "Drama", "because": "x"}]}),
            "shares no tags")
        ok(b.run("table_card", {"table": 1, "opener": "Compare favourites.", "ask": "Which one surprises?", "topics": []}))

    def test_card_before_seating_is_refused(self):
        bad(box().run("table_card", {"table": 1, "opener": "x", "topics": [], "ask": "y"}), "seat_guests first")


class Present(unittest.TestCase):
    def test_present_plan_fills_missing_cards_and_reports_everything(self):
        b = seated()
        k = table_of(b, "Kai Park")
        t = b.run("explain_table", {"table": k})[0]["shared_tags"][0]
        ok(b.run("table_card", {"table": k, "opener": "x " + t["tag"], "ask": "q",
                                "topics": [{"tag": t["tag"], "because": "b"}]}))
        ok(b.run("present_plan", {"headline": "Seated.", "notes": "Weakest: Jo.", "assumptions": ["I assumed X"]}))
        plan = b.final[1]
        sources = [c["source"] for c in plan["cards"]]
        self.assertEqual((sources.count("agent"), sources.count("auto")), (1, 1))
        self.assertTrue(any("automatic card" in n for n in plan["notes"]))
        self.assertIn("I assumed X", plan["assumptions"])
        g = {x["name"]: x for x in plan["guests"]}["Kai Park"]
        self.assertEqual(g["favorites"], ["Parasite", "BTS"])
        self.assertEqual(g["confidence"], "ok")
        self.assertIn("Fit", g["why"])
        self.assertTrue(plan["tables"][0]["shared_tags"][0]["tag"])
        self.assertEqual(plan["metrics"]["table_sizes"], [4, 4])
        self.assertTrue(plan["all_constraints_ok"])

    def test_auto_cards_are_grounded_and_pass_the_server_check(self):
        b = seated()
        for t in b.plan["tables"]:
            c = b.auto_card(t)
            topics = [{"tag": x["tag"], "because": x["because"], "entities": x["entities"], "guests": x["guests"]}
                      for x in c["topics"]]
            b._check_card(t, c["title"], c["opener"], topics, c["ask"])

    def test_low_confidence_and_no_data_flags(self):
        b = box(guests=[{"name": "A", "favorites": ["Parasite", "Zxqv"]}, {"name": "B", "favorites": ["Nope"]},
                        {"name": "C", "favorites": ["BTS", "Parasite"]}, {"name": "D", "favorites": ["BTS", "Squid Game"]}],
                table_size=2)
        ok(b.run("build_taste_profiles", {}))
        ok(b.run("seat_guests", {}))
        ok(b.run("present_plan", {"headline": "h"}))
        by = {g["name"]: g for g in b.final[1]["guests"]}
        self.assertEqual((by["A"]["confidence"], by["B"]["confidence"], by["C"]["confidence"]), ("low", "none", "ok"))
        self.assertTrue(by["B"]["no_data"])
        self.assertIn("No favourite matched", by["B"]["why"])

    def test_ask_organizer_ends_the_run_with_a_question(self):
        b = box()
        ok(b.run("ask_organizer", {"question": "Which?", "options": "a, b, c, d, e"}))
        self.assertEqual(b.final, ("question", {"question": "Which?", "options": ["a", "b", "c", "d"]}))

    def test_auto_plan_fallback(self):
        b = box()
        self.assertIsNone(b.auto_plan("x"))
        ok(b.run("build_taste_profiles", {}))
        plan = b.auto_plan("turn limit reached")
        self.assertIn("stopped early", plan["assumptions"][-1])
        self.assertEqual(len(plan["tables"]), 2)

    def test_unknown_tool_and_bad_arguments(self):
        b = box()
        bad(b.run("nope", {}), "No tool named")
        bad(b.run("seat_guests", {"colour": "red"}), "Bad arguments")

    def test_tool_specs_are_complete(self):
        names = [s["name"] for s in tools.TOOL_SPECS]
        self.assertEqual(names, ["build_taste_profiles", "apply_constraint", "seat_guests", "explain_table", "table_card",
                                 "ask_organizer", "present_plan"])
        for s in tools.TOOL_SPECS:
            self.assertTrue(hasattr(ToolBox, "t_" + s["name"]))
            self.assertEqual(s["parameters"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
