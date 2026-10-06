"""Agent loop tests with a scripted LLM and the SAMPLE Qloo client (no keys, no network)."""
import json
import unittest

from placecard import agent, presets
from placecard.llm.base import LLMError, ToolCall, Turn
from placecard.llm.mock_llm import DemoAgentLLM, ScriptedLLM, parse_constraint
from placecard.qloo import QlooAuthError
from placecard.requirements import split_requirements
from placecard.sample_qloo import SampleQloo

PARTY = {"guests": [
    {"name": "Kai Park", "favorites": ["Parasite", "BTS"], "group": "Park family"},
    {"name": "Lee Park", "favorites": ["Squid Game", "NewJeans"], "group": "Park family"},
    {"name": "Min Choi", "favorites": ["Parasite", "Past Lives"]},
    {"name": "Tess Ng", "favorites": ["Silicon Valley", "Acquired"]},
    {"name": "Uma Rao", "favorites": ["Zero to One", "Mr. Robot"]},
    {"name": "Vic Lim", "favorites": ["Lex Fridman Podcast", "Silicon Valley"]}], "table_size": 3,
    "constraints_text": "Keep the Parks together."}


def call(name, args, cid=None):
    return ToolCall(cid or f"c-{name}-{json.dumps(args, sort_keys=True)[:24]}", name, args)


def last_result(messages, name):
    for m in reversed(messages):
        if m["role"] == "tool":
            for r in m["results"]:
                if r["name"] == name:
                    return json.loads(r["content"])
    raise AssertionError(f"no result for {name}")


def req(**over):
    return agent.normalise_request(dict(PARTY, **over))


class ScriptedLoop(unittest.TestCase):
    def test_full_tool_sequence_and_grounded_plan(self):
        def cards(messages):
            exp = [json.loads(r["content"]) for m in messages if m["role"] == "tool" for r in m["results"]
                   if r["name"] == "explain_table"]
            out = []
            for e in exp:
                s = e["shared_tags"][0]
                out.append(call("table_card", {"table": e["table"], "opener": f"You lean towards {s['tag']}.",
                                               "topics": [{"tag": s["tag"], "because": "You listed related favorites."}],
                                               "ask": "What first?"}))
            return Turn(text="Cards.", tool_calls=out)

        llm = ScriptedLLM([
            Turn(text="Profiles.", tool_calls=[call("build_taste_profiles", {})]),
            Turn(tool_calls=[call("apply_constraint", {"text": "Keep the Parks together.", "kind": "together",
                                                      "group": "Park family"})]),
            Turn(tool_calls=[call("seat_guests", {})]),
            Turn(tool_calls=[call("explain_table", {"table": 1}, "e1"), call("explain_table", {"table": 2}, "e2")]),
            cards,
            Turn(text="Done.", tool_calls=[call("present_plan", {"headline": "Seated.", "notes": ["n"],
                                                                "assumptions": ["a"]})]),
        ])
        events = []
        out = agent.run(llm, SampleQloo(), req(), emit=events.append)
        self.assertEqual(out["status"], "plan")
        plan = out["plan"]
        self.assertEqual(len(plan["tables"]), 2)
        self.assertEqual([c["source"] for c in plan["cards"]], ["agent", "agent"])
        self.assertTrue(plan["all_constraints_ok"])
        by = {g["name"]: g["table"] for g in plan["guests"]}
        self.assertEqual(by["Kai Park"], by["Lee Park"])
        self.assertTrue(out["mode"]["sample_data"])
        names = [e["name"] for e in events if e["type"] == "tool_call"]
        self.assertEqual(names[:4], ["build_taste_profiles", "apply_constraint", "seat_guests", "explain_table"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(out["stats"]["turns"], 6)
        self.assertGreater(out["stats"]["qloo_calls"], 6)

    def test_ungrounded_card_is_rejected_and_model_can_recover(self):
        def retry(messages):
            err = json.loads(messages[-1]["results"][0]["content"])["error"]
            self.assertIn("Ungrounded", err)
            exp = last_result(messages, "explain_table")
            s = exp["shared_tags"][0]
            return Turn(tool_calls=[call("table_card", {"table": exp["table"], "opener": "You lean towards " + s["tag"],
                                                       "topics": [{"tag": s["tag"], "because": "x"}], "ask": "Why?"})])

        llm = ScriptedLLM([
            Turn(tool_calls=[call("build_taste_profiles", {})]),
            Turn(tool_calls=[call("seat_guests", {})]),
            Turn(tool_calls=[call("explain_table", {"table": 1})]),
            Turn(tool_calls=[call("table_card", {"table": 1, "opener": "You all love Jazz.", "ask": "?",
                                                "topics": [{"tag": "Jazz", "because": "invented"}]})]),
            retry,
        ])
        out = agent.run(llm, SampleQloo(), req(constraints_text=""), max_turns=5)
        res = [s for s in out["steps"] if s["type"] == "tool_result" and s["name"] == "table_card"]
        self.assertEqual([r["error"] for r in res], [True, False])
        self.assertEqual(out["status"], "plan")        # turn limit reached: fallback plan keeps the accepted card
        card = [c for c in out["plan"]["cards"] if c["table"] == 1][0]
        self.assertEqual(card["source"], "agent")

    def test_present_before_seating_is_rejected(self):
        llm = ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})]),
                           Turn(tool_calls=[call("present_plan", {"headline": "x"})]),
                           Turn(tool_calls=[call("ask_organizer", {"question": "Which?", "options": ["a", "b"]})])])
        out = agent.run(llm, SampleQloo(), req())
        self.assertEqual(out["status"], "question")
        self.assertEqual(out["question"]["options"], ["a", "b"])
        errs = [s for s in out["steps"] if s["type"] == "tool_result" and s["error"]]
        self.assertIn("seat_guests", errs[0]["data"]["error"])

    def test_invalid_constraint_is_reported_to_the_model(self):
        llm = ScriptedLLM([Turn(tool_calls=[call("apply_constraint", {"text": "x", "kind": "apart", "guests": ["Zed", "Kai"]})])])
        out = agent.run(llm, SampleQloo(), req(), max_turns=1)
        res = [s for s in out["steps"] if s["type"] == "tool_result"][0]
        self.assertTrue(res["error"])
        self.assertIn("No guest named 'Zed'", res["data"]["error"])
        self.assertTrue(res["summary"].startswith("Rejected"))

    def test_text_only_reply_gets_one_nudge_then_stops(self):
        llm = ScriptedLLM([Turn(text="Hmm."), Turn(text="Still thinking.")])
        out = agent.run(llm, SampleQloo(), req())
        self.assertEqual(llm.calls, 2)
        self.assertEqual(out["status"], "error")  # nothing profiled, nothing to auto-plan

    def test_turn_limit_falls_back_to_a_plan_from_tool_results(self):
        llm = ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})]),
                           Turn(tool_calls=[call("seat_guests", {})]),
                           Turn(tool_calls=[call("explain_table", {"table": 1})])])
        out = agent.run(llm, SampleQloo(), req(), max_turns=3)
        self.assertEqual(out["status"], "plan")
        self.assertIn("stopped early", out["plan"]["assumptions"][-1])
        self.assertEqual({c["source"] for c in out["plan"]["cards"]}, {"auto"})

    def test_turn_limit_with_only_profiles_still_seats(self):
        out = agent.run(ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})])]), SampleQloo(), req(), max_turns=1)
        self.assertEqual(out["status"], "plan")
        self.assertEqual(len(out["plan"]["tables"]), 2)

    def test_model_that_skips_apply_constraint_is_sent_back_and_then_complies(self):
        """The live-run failure: seat_guests without apply_constraint. The server refuses and the model recovers."""
        def retry(messages):
            err = json.loads(messages[-1]["results"][0]["content"])["error"]
            self.assertIn("Keep the Parks together", err)
            return Turn(tool_calls=[call("apply_constraint", {"text": "Keep the Parks together.", "kind": "together",
                                                              "group": "Park family"})])

        llm = ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})]),
                           Turn(tool_calls=[call("seat_guests", {})]),
                           retry,
                           Turn(tool_calls=[call("seat_guests", {})]),
                           Turn(tool_calls=[call("present_plan", {"headline": "Seated."})])])
        out = agent.run(llm, SampleQloo(), req())
        seats = [s for s in out["steps"] if s["type"] == "tool_result" and s["name"] == "seat_guests"]
        self.assertEqual([s["error"] for s in seats], [True, False])
        self.assertEqual(out["status"], "plan")
        self.assertTrue(out["plan"]["all_constraints_ok"])
        self.assertEqual(out["plan"]["unapplied_requirements"], [])

    def test_system_prompt_and_message_spell_out_the_order(self):
        p = agent.SYSTEM_PROMPT
        steps = ["1. Call build_taste_profiles", "2. CONSTRAINTS BEFORE SEATING", "3. Call seat_guests",
                 "4. For each table call explain_table", "5. Finish with present_plan"]
        self.assertEqual([p.index(x) for x in steps], sorted(p.index(x) for x in steps))
        self.assertIn("profiles -> constraints -> seat -> explain/cards -> present_plan", p)
        self.assertIn("spread", p)
        self.assertIn("apply EACH one", agent.build_user_message(req()))

    def test_llm_outage_before_constraints_still_applies_and_reports_the_notes(self):
        class DiesAfterProfiles(ScriptedLLM):
            def chat(self, *a):
                if self.calls >= 1:
                    raise LLMError("Gemini error (503): high demand", retryable=True)
                return super().chat(*a)

        out = agent.run(DiesAfterProfiles([Turn(tool_calls=[call("build_taste_profiles", {})])]), SampleQloo(),
                        req(constraints_text="Keep the Parks together.\nMake the evening magical."))
        plan = out["plan"]
        self.assertEqual(out["status"], "plan")
        by = {g["name"]: g["table"] for g in plan["guests"]}
        self.assertEqual(by["Kai Park"], by["Lee Park"])
        self.assertFalse(plan["all_constraints_ok"])
        self.assertTrue(any("Make the evening magical" in n and "NOT applied" in n for n in plan["notes"]))
        self.assertTrue(any("Applied automatically" in n for n in plan["notes"]))
        self.assertEqual(out["error"], None)

    def test_llm_error_is_reported(self):
        class Boom:
            label, provider = "boom", "test"

            def chat(self, *a):
                raise LLMError("Gemini rate limit (429)", retryable=True)

        out = agent.run(Boom(), SampleQloo(), req())
        # Unreachable before any work: the server still profiles the guests, applies the notes and seats them.
        self.assertEqual((out["status"], out["error"]), ("plan", None))
        self.assertIn("unavailable", out["plan"]["headline"])
        self.assertTrue(out["plan"]["all_constraints_ok"])
        self.assertEqual([c["kind"] for c in out["plan"]["constraint_log"]], ["together"])

    def test_llm_unreachable_and_qloo_key_bad_is_an_error(self):
        class Boom:
            label, provider = "boom", "test"

            def chat(self, *a):
                raise LLMError("Gemini rate limit (429)", retryable=True)

        class BadKey(SampleQloo):
            def search(self, *a, **k):
                raise QlooAuthError("Qloo rejected the API key (401).")

        out = agent.run(Boom(), BadKey(), req())
        self.assertEqual(out["status"], "error")

    def test_transient_llm_failure_mid_run_still_returns_plan(self):
        class FailsSecondTurn(ScriptedLLM):
            def chat(self, *a):
                if self.calls >= 1:
                    raise LLMError("Gemini error (503): high demand", retryable=True)
                return super().chat(*a)

        out = agent.run(FailsSecondTurn([Turn(tool_calls=[call("build_taste_profiles", {})])]), SampleQloo(), req())
        self.assertEqual(out["status"], "plan")
        self.assertIsNone(out["error"])
        self.assertTrue(any("unavailable" in (s.get("text") or "") for s in out["steps"]))

    def test_fatal_llm_error_is_not_masked(self):
        class BadKey:
            label, provider = "bad", "test"

            def chat(self, *a):
                raise LLMError("Gemini rejected the API key (401).", fatal=True)

        out = agent.run(BadKey(), SampleQloo(), req())
        self.assertEqual(out["status"], "error")
        self.assertIn("401", out["error"])

    def test_bad_qloo_key_stops_the_run(self):
        class BadKey(SampleQloo):
            def search(self, *a, **k):
                raise QlooAuthError("Qloo rejected the API key (401).", 401)

        llm = ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})])] * 3)
        out = agent.run(llm, BadKey(), req())
        self.assertEqual(out["status"], "error")
        self.assertIn("401", out["error"])
        self.assertEqual(llm.calls, 1)

    def test_qloo_budget_is_enforced_per_run(self):
        out = agent.run(ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})])]), SampleQloo(), req(),
                        max_turns=1, max_qloo_calls=4)
        res = [s for s in out["steps"] if s["type"] == "tool_result"][0]
        self.assertIn("budget", res["data"]["note"])
        self.assertLessEqual(out["stats"]["qloo_calls"], 5)

    def test_calls_after_the_final_tool_are_refused(self):
        llm = ScriptedLLM([Turn(tool_calls=[call("build_taste_profiles", {})]),
                           Turn(tool_calls=[call("seat_guests", {})]),
                           Turn(tool_calls=[call("present_plan", {"headline": "x"}, "p"),
                                            call("explain_table", {"table": 1}, "late")])])
        out = agent.run(llm, SampleQloo(), req())
        late = [s for s in out["steps"] if s["type"] == "tool_result" and s["id"] == "late"][0]
        self.assertTrue(late["error"])
        self.assertEqual(out["status"], "plan")


class Requests(unittest.TestCase):
    def test_normalise_clips_and_validates(self):
        r = agent.normalise_request({"guests": [
            {"name": "  Ann   Lee ", "favorites": ["A", "a", "B", "C", "D", "E"], "group": "G" * 80},
            {"name": "Bob", "favourites": "X; Y | Z"}, {"name": ""}, "junk"],
            "table_size": 99, "seed": "7", "constraints_text": "x" * 5000,
            "clarifications": [{"answer": "ok"}, {"question": "q"}]})
        self.assertEqual([g["name"] for g in r["guests"]], ["Ann Lee", "Bob"])
        self.assertEqual(len(r["guests"][0]["favorites"]), 4)
        self.assertEqual(r["guests"][1]["favorites"], ["X", "Y", "Z"])
        self.assertEqual((r["table_size"], r["seed"]), (14, 7))
        self.assertEqual(len(r["constraints_text"]), 1500)
        self.assertEqual(len(r["guests"][0]["group"]), 40)
        self.assertEqual(len(r["clarifications"]), 1)

    def test_normalise_rejects_bad_input(self):
        for bad in ({}, {"guests": "x"}, {"guests": [{"name": "A", "favorites": ["x"]}]},
                    {"guests": [{"name": "A"}, {"name": "B"}]},
                    {"guests": [{"name": "A", "favorites": ["x"]}, {"name": "a", "favorites": ["y"]}]}, [], None):
            with self.assertRaises(ValueError, msg=str(bad)):
                agent.normalise_request(bad)

    def test_guest_cap(self):
        many = [{"name": f"G{i}", "favorites": ["x"]} for i in range(121)]
        with self.assertRaises(ValueError):
            agent.normalise_request({"guests": many})
        self.assertEqual(len(agent.normalise_request({"guests": many[:120]})["guests"]), 120)

    def test_user_message_is_compact_for_real_llms_and_complete_for_the_mock(self):
        r = req()
        real = agent.build_user_message(r)
        self.assertNotIn("GROUP_JSON", real)
        self.assertNotIn("Parasite", real)                  # favourites stay server-side
        self.assertIn("Park family: Kai Park, Lee Park", real)
        self.assertIn("Keep the Parks together.", real)
        self.assertIn("GROUP_JSON", agent.build_user_message(r, include_json=True))


class MockConstraintParser(unittest.TestCase):
    def test_sentences(self):
        self.assertEqual(parse_constraint("Keep the Tan family together."),
                         {"text": "Keep the Tan family together.", "kind": "together", "group": "Tan family"})
        self.assertEqual(parse_constraint("Dave, Ali and Sam together")["guests"], ["Dave", "Ali", "Sam"])
        self.assertEqual(parse_constraint("Dave and Ali apart (they argue)")["guests"], ["Dave", "Ali"])
        self.assertEqual(parse_constraint("Keep Dave and Ali apart")["kind"], "apart")
        self.assertEqual(parse_constraint("Put Grandma Rose at the head table.")["guests"], ["Grandma Rose"])
        c = parse_constraint("Put Priya on table 3")
        self.assertEqual((c["kind"], c["table"], c["guests"]), ("fixed", 3, ["Priya"]))
        self.assertIsNone(parse_constraint("Make it fun"))
        self.assertEqual(parse_constraint("Speakers should be spread across tables."),
                         {"text": "Speakers should be spread across tables.", "kind": "spread", "group": "Speakers"})
        self.assertEqual(parse_constraint("Spread the sponsors across the tables")["group"], "sponsors")
        self.assertEqual(parse_constraint("Mix Dave, Ali and Sam across tables")["guests"], ["Dave", "Ali", "Sam"])
        self.assertEqual(split_requirements("Speakers should be spread. Keep Liam and Dr. Amara apart.\nX at table 2"),
                         ["Speakers should be spread.", "Keep Liam and Dr. Amara apart.", "X at table 2"])


class DemoAgent(unittest.TestCase):
    """The keyless MOCK agent drives the same loop end to end."""

    def run_preset(self, key, **over):
        p = presets.PRESETS[key]
        r = agent.normalise_request({"guests": p["guests"], "table_size": p["table_size"],
                                     "constraints_text": p["constraints"], **over})
        events = []
        return agent.run(DemoAgentLLM(), SampleQloo(), r, emit=events.append), events, r

    def test_wedding_end_to_end(self):
        out, events, r = self.run_preset("wedding")
        self.assertEqual(out["status"], "plan")
        plan = out["plan"]
        self.assertEqual(len(plan["guests"]), 40)
        self.assertEqual(sorted(g for t in plan["tables"] for g in t["guests"]), sorted(g["name"] for g in r["guests"]))
        self.assertTrue(plan["all_constraints_ok"])
        self.assertEqual(len(plan["constraints"]), 3)
        by = {g["name"]: g["table"] for g in plan["guests"]}
        self.assertEqual(len({by[n] for n in ("Raymond Tan", "Pearl Tan", "Daniel Tan", "Mei Tan")}), 1)
        self.assertNotEqual(by["Aisyah Karim"], by["Hafiz Karim"])
        self.assertEqual((by["Grandma Rose"], by["Grandpa Lee"]), (1, 1))
        self.assertEqual(plan["metrics"]["isolated"], [])
        self.assertGreater(plan["metrics"]["min_fit"], 1.5 * plan["metrics"]["baseline"]["min_fit"])
        self.assertEqual({c["source"] for c in plan["cards"]}, {"agent"})
        self.assertTrue(all(c["topics"] for c in plan["cards"]))
        self.assertTrue(any("Parasite" in a and "2019" in a for a in plan["assumptions"]))
        self.assertIn("least-matched", plan["headline"])
        names = [e["name"] for e in events if e["type"] == "tool_call"]
        self.assertEqual(names.count("table_card"), 5)
        self.assertLess(names.index("seat_guests"), names.index("explain_table"))
        self.assertEqual(names[-1], "present_plan")
        self.assertTrue(any(e["type"] == "progress" for e in events))

    def test_conference_end_to_end(self):
        out, _, _ = self.run_preset("conference")
        self.assertEqual(out["status"], "plan")
        by = {g["name"]: g["table"] for g in out["plan"]["guests"]}
        self.assertEqual(by["Dr. Amara Okafor"], 1)
        self.assertNotEqual(by["Jordan Blake"], by["Katrina Wolfe"])
        self.assertEqual(len(out["plan"]["tables"]), 4)

    def test_mystery_guests_ask_a_question_then_proceed_with_the_answer(self):
        out, _, _ = self.run_preset("mystery")
        self.assertEqual(out["status"], "question")
        self.assertIn("Jun", out["question"]["question"])
        answer = out["question"]["options"][0]
        out2, _, _ = self.run_preset("mystery", clarifications=[{"question": out["question"]["question"], "answer": answer}])
        self.assertEqual(out2["status"], "plan")
        plan = out2["plan"]
        self.assertEqual(sorted(plan["metrics"]["no_data"]), ["Jun", "Sara"])
        self.assertTrue(any("neutral score" in a for a in plan["assumptions"]))
        self.assertTrue(any("Organiser said" in a for a in plan["assumptions"]))

    def test_unparseable_and_impossible_requirements_are_reported_not_fatal(self):
        out, _, _ = self.run_preset("conference", constraints_text="Make the evening magical.\nFoo and Bar apart.")
        self.assertEqual(out["status"], "plan")
        notes = " ".join(out["plan"]["assumptions"])
        self.assertIn("could not interpret", notes)
        self.assertIn("Constraint not applied", notes)

    def test_conference_notes_with_a_spread_requirement(self):
        notes = "Speakers should be spread across tables. Keep Liam Chen and Dr. Amara Okafor apart."
        out, _, _ = self.run_preset("conference", constraints_text=notes)
        self.assertEqual(out["status"], "plan")
        plan = out["plan"]
        kinds = sorted(c["kind"] for c in plan["constraint_log"])
        self.assertEqual(kinds, ["apart", "spread"])
        self.assertTrue(plan["all_constraints_ok"])
        by = {g["name"]: g for g in plan["guests"]}
        speakers = [n for n, g in by.items() if g["group"] == "Speakers"]
        per = [sum(1 for n in speakers if by[n]["table"] == t) for t in range(1, len(plan["tables"]) + 1)]
        self.assertLessEqual(max(per), 2)
        self.assertNotEqual(by["Liam Chen"]["table"], by["Dr. Amara Okafor"]["table"])

    def test_notes_with_no_seating_rule_get_an_explicit_none(self):
        out, events, _ = self.run_preset("conference", constraints_text="Make the evening magical.")
        self.assertEqual(out["status"], "plan")
        calls = [e["args"].get("kind") for e in events if e["type"] == "tool_call" and e["name"] == "apply_constraint"]
        self.assertEqual(calls, ["none"])
        self.assertTrue(any("no seating requirement" in n for n in out["plan"]["notes"]))

    def test_is_deterministic(self):
        a, _, _ = self.run_preset("conference")
        b, _, _ = self.run_preset("conference")
        self.assertEqual([t["guests"] for t in a["plan"]["tables"]], [t["guests"] for t in b["plan"]["tables"]])

    def test_real_provider_message_has_no_json_copy(self):
        seen = []

        class Spy(ScriptedLLM):
            provider = "gemini"

            def chat(self, system, messages, tool_specs):
                seen.append(messages[0]["content"])
                return super().chat(system, messages, tool_specs)

        agent.run(Spy([Turn(text="x"), Turn(text="y")]), SampleQloo(), req())
        self.assertNotIn("GROUP_JSON", seen[0])


if __name__ == "__main__":
    unittest.main()
