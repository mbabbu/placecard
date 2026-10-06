"""Keyless stand-ins for an LLM, used for the demo without keys and for tests.

ScriptedLLM  - replays a fixed list of turns (unit tests of the agent loop).
DemoAgentLLM - a deterministic, rule-based policy that drives the SAME tools through the SAME
               loop as a real model, reading tool results from the history. It is clearly
               labelled "MOCK agent" in the UI; it shows the agent mechanics, not LLM judgement.
               (Its "natural language" understanding is a handful of regular expressions.)
"""
import json
import re
import time

from ..requirements import parse_constraint, split_requirements  # noqa: F401  (parse_constraint is re-exported)
from .base import ToolCall, Turn


class ScriptedLLM:
    provider = "scripted"
    label = "Scripted test LLM"

    def __init__(self, turns):
        self.turns, self.calls = list(turns), 0

    def chat(self, system, messages, tool_specs):
        self.calls += 1
        if not self.turns:
            return Turn(text="(script finished)")
        t = self.turns.pop(0)
        return t(messages) if callable(t) else t


def parse_request(messages):
    first = messages[0]["content"]
    m = re.search(r"GROUP_JSON:\s*(\{.*\})\s*$", first, re.S)
    return json.loads(m.group(1)) if m else {"guests": []}


def history(messages):
    """[(name, args, result_dict, is_error)] for every executed tool call, in order."""
    calls, out = {}, []
    for m in messages:
        if m["role"] == "assistant":
            for c in m.get("tool_calls", []):
                calls[c.id] = c
        elif m["role"] == "tool":
            for r in m["results"]:
                c = calls.get(r["id"])
                try:
                    res = json.loads(r["content"])
                except ValueError:
                    res = {}
                out.append((r["name"], c.args if c else {}, res, r.get("is_error", False)))
    return out


class DemoAgentLLM:
    provider = "mock"
    label = "MOCK agent (scripted, no LLM key)"

    def __init__(self, delay=0.0):
        self.calls, self.delay = 0, delay  # delay only paces the demo UI
        self.notes = []

    def _id(self, name):
        self.calls += 1
        return f"mock-{self.calls}-{name}"

    def chat(self, system, messages, tool_specs):
        if self.delay:
            time.sleep(self.delay)
        req = parse_request(messages)
        done = history(messages)
        names = [d[0] for d in done]
        n_guests = len(req.get("guests", []))

        # 1. taste profiles (batched)
        builds = [d for d in done if d[0] == "build_taste_profiles" and not d[3]]
        if not builds or builds[-1][2].get("remaining"):
            first = not builds
            text = (f"First I'll build a Qloo taste profile for each of the {n_guests} guests: resolve their favourites "
                    "and fetch their tag affinities." if first else "More guests to profile.")
            return Turn(text=text, tool_calls=[ToolCall(self._id("prof"), "build_taste_profiles", {})])
        last = builds[-1][2]
        no_data = {n for b in builds for n in b[2].get("guests_without_taste_data", [])}
        weak = no_data
        for b in builds:
            self.notes += [a for a in b[2].get("assumptions", []) if a not in self.notes]

        # 2. a real clarifying question when a large share of the guests cannot be profiled
        if len(weak) * 3 >= max(n_guests, 1) and not req.get("clarifications") and "ask_organizer" not in names:
            who = sorted(weak)
            return Turn(text="Several guests have no favourites Qloo recognises, so I can't place them by taste.",
                        tool_calls=[ToolCall(self._id("ask"), "ask_organizer", {
                            "question": f"{len(who)} of {n_guests} guests ({', '.join(who[:4])}"
                                        f"{'...' if len(who) > 4 else ''}) have no favourites Qloo recognises. "
                                        "Seat them anyway with a neutral score, or will you add better favourites first?",
                            "options": ["Seat them anyway (neutral score)", "I'll add better favourites and re-run"]})])
        for c in req.get("clarifications") or []:
            self.notes.append(f"Organiser said: {c.get('answer')}")
        if weak:
            self.notes.append(f"{len(weak)} guest(s) without taste data were seated with a neutral score: {', '.join(sorted(weak))}.")

        # 3. constraints
        if "apply_constraint" not in names:
            calls, unparsed = [], []
            for line in split_requirements(req.get("constraints_text") or ""):
                args = parse_constraint(line)
                if args:
                    calls.append(ToolCall(self._id("con"), "apply_constraint", args))
                else:
                    unparsed.append(line.strip())
            self.notes += [f"I could not interpret this requirement, so it was not applied: \"{u}\"" for u in unparsed]
            if calls:
                return Turn(text=f"Now the organiser's {len(calls)} requirement(s) as validated hard constraints.",
                            tool_calls=calls)
            if unparsed:   # the server insists on an explicit answer when notes were given
                return Turn(text="None of the organiser's notes is a seating rule I can apply.",
                            tool_calls=[ToolCall(self._id("con"), "apply_constraint", {
                                "kind": "none", "text": "Could not interpret as a seating requirement: "
                                                        + "; ".join(unparsed)[:150]})])
        for d in done:
            if d[0] == "apply_constraint" and d[3]:
                note = f"Constraint not applied ({d[1].get('text')}): {d[2].get('error')}"
                if note not in self.notes:
                    self.notes.append(note)

        # 4. seat
        if "seat_guests" not in names:
            return Turn(text="Seating everyone: maximise the fit of the least-matched guest and the average table "
                             "cohesion, avoid isolating anyone, honour the constraints.",
                        tool_calls=[ToolCall(self._id("seat"), "seat_guests", {})])
        seated = [d for d in done if d[0] == "seat_guests" and not d[3]]
        if not seated:
            return Turn(text="The optimiser could not seat the party with those constraints.",
                        tool_calls=[ToolCall(self._id("ask"), "ask_organizer", {
                            "question": "These constraints cannot all be met. Which should I drop?",
                            "options": ["Drop the last constraint"]})])
        seat = seated[-1][2]

        # 5. explain + cards
        if "explain_table" not in names:
            return Turn(text="Checking each table: who fits and what Qloo says they share.",
                        tool_calls=[ToolCall(self._id("exp"), "explain_table", {"table": t["table"]})
                                    for t in seat["tables"]])
        if "table_card" not in names:
            calls = []
            for d in done:
                if d[0] != "explain_table" or d[3]:
                    continue
                e = d[2]
                tags = e["shared_tags"][:2]
                topics = []
                for sh in tags:
                    fav = sh["favorites"][0] if sh["favorites"] else None
                    who = sh["members"][:3]
                    because = (f"{' and '.join(fav['listed_by'][:2])} listed {fav['entity']}." if fav and len(fav["listed_by"]) > 1
                               else f"{', '.join(who)} lean towards {sh['tag']}"
                               + (f" (for example via {fav['entity']})." if fav else "."))
                    topics.append({"tag": sh["tag"], "because": because, "guests": who,
                                   "entities": [fav["entity"]] if fav else []})
                if topics:
                    opener = f"Everyone here leans towards {' and '.join(t['tag'] for t in topics)}."
                    ask = f"Who first got into {topics[0]['tag']}, and what did they start with?"
                else:
                    opener = "This is a mixed table with no single shared taste; compare what each of you listed."
                    ask = "Which of your favourites would surprise the rest of the table?"
                calls.append(ToolCall(self._id("card"), "table_card", {
                    "table": e["table"], "title": e["label"], "opener": opener, "topics": topics, "ask": ask}))
            return Turn(text="Writing a conversation-starter card per table, using only what each table shares in Qloo.",
                        tool_calls=calls)

        # 6. present
        low = seat.get("lowest_fit_guests", [])[:3]
        notes = []
        if low:
            notes.append("Weakest fits: " + "; ".join(f"{x['guest']} {x['fit']:.2f} (table {x['table']})" for x in low) + ".")
        if seat.get("isolated"):
            notes.append("Isolated guests: " + ", ".join(seat["isolated"]) + ".")
        else:
            notes.append("Nobody is isolated: every guest has at least one close match at their table"
                         + (f" (except {len(seat['unavoidable_isolated'])} for whom no close match exists in this party)."
                            if seat.get("unavoidable_isolated") else "."))
        if not seat.get("all_constraints_ok"):
            notes.append("Some constraints could not be met; see the constraint list.")
        head = (f"{len(req.get('guests', []))} guests at {len(seat['tables'])} tables: the least-matched guest fits "
                f"{seat['min_fit']:.2f} against {seat['versus_random_seating']['min_fit']:.2f} for random seating.")
        return Turn(text="Here is the seating plan.", tool_calls=[ToolCall(self._id("plan"), "present_plan", {
            "headline": head, "notes": notes, "assumptions": self.notes})])
