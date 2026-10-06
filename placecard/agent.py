"""The Placecard agent: an LLM tool-calling loop over Qloo taste profiles and a seating optimiser.

The model decides which tools to call (build_taste_profiles, apply_constraint, seat_guests, explain_table,
table_card) and when to stop (present_plan, or ask_organizer for a clarifying question). The loop is
provider-neutral: Claude, Gemini and the keyless MOCK agent all run through run() below. Every step is
emitted as an event so the UI can show the agent working in real time.
"""
import json
import time

from . import config, seating
from .llm import LLMError
from .tools import TOOL_SPECS, FatalToolError, ToolBox, to_json

SYSTEM_PROMPT = """You are Placecard, an agent that builds a seating chart for dinners, weddings and conference tables so that \
every table shares real cultural common ground and nobody is stuck with strangers. Qloo's taste graph (via your tools) is your \
only source of truth about guests' tastes; your own knowledge of films, music or restaurants is never evidence, and you never do \
the seating arithmetic yourself: the optimiser does.

How to work, in this order (profiles -> constraints -> seat -> explain/cards -> present_plan):
1. Call build_taste_profiles (repeat while 'remaining' > 0). Read its report: unmatched favourites and ambiguity assumptions. \
If many guests (roughly a third or more) have no taste data and a wrong guess would change the plan, call ask_organizer once; \
otherwise state the assumption and continue. Never ask something the organiser already answered.
2. CONSTRAINTS BEFORE SEATING. If the message contains organiser requirements or notes, turn EACH requirement into its own \
apply_constraint call before you ever call seat_guests: together (guests or a group), apart, spread (a group divided over the \
tables, e.g. "speakers should be spread across tables"), fixed (table number), head (head table). Translate the sentence into \
guest names or a group yourself and pass the original wording as `text`. One sentence can contain several requirements; make one \
call per requirement. Issue independent calls in one turn. If a call is rejected, read the reason: fix a misspelt name, or tell \
the organiser which requirement is impossible and continue without it. Only if the notes contain no seating requirement at all, \
call apply_constraint with kind 'none' and the reason. The server refuses seat_guests until you have done this, and any \
requirement you did not apply is flagged as NOT applied in the plan the organiser sees.
3. Call seat_guests. Check the report: every constraint satisfied, isolated guests, the lowest-fit guests.
4. For each table call explain_table, then write its card with table_card: an opener, 1-3 topics taken from the table's shared \
Qloo tags, and one question. A card may only mention tags the table shares and favourites listed at that table; the server rejects \
anything else, including casual mentions of other genres or other guests. Describe affinities as aggregate taste patterns \
("you both lean towards..."), never as facts about a person.
5. Finish with present_plan: a one-line headline quoting the optimiser's numbers (least-matched guest's fit versus random \
seating), notes about the weakest-fit guests and why, and every assumption you made. Say a requirement is met only if the \
seat_guests report shows it as met.

Rules: never invent guests, tags, favourites or numbers. Before each batch of tool calls write one short sentence saying what \
you are doing and why (the organiser sees it live). Keep it brisk: aim to finish in about 8 turns."""


def build_user_message(req, include_json=False):
    guests = req["guests"]
    lines = [f"Seat {len(guests)} guests at tables of up to {req['table_size']} seats."]
    by_group = {}
    for g in guests:
        by_group.setdefault(g["group"] or "(no group)", []).append(g["name"])
    lines.append("Guests by group:")
    for grp, names in by_group.items():
        lines.append(f"- {grp}: {', '.join(names)}")
    lines.append("Their favourites are already loaded server-side; build_taste_profiles reads them.")
    if req.get("constraints_text"):
        lines.append("Organiser's requirements and notes (apply EACH one with apply_constraint before "
                     "seat_guests):\n" + req["constraints_text"])
    else:
        lines.append("No special requirements.")
    for c in req.get("clarifications") or []:
        lines.append(f"You asked: {c.get('question')} - The organiser answered: {c.get('answer')}")
    if include_json:  # only the keyless mock agent needs the machine-readable copy
        lines.append("")
        lines.append("GROUP_JSON: " + json.dumps({k: req[k] for k in ("guests", "table_size", "constraints_text",
                                                                      "clarifications")}, ensure_ascii=False))
    return "\n".join(lines)


def normalise_request(data):
    """Validate and clip untrusted input from the web form."""
    if not isinstance(data, dict):
        raise ValueError("Bad request.")
    guests, seen = [], set()
    raw = data.get("guests") or []
    if not isinstance(raw, list):
        raise ValueError("Guests must be a list.")
    if len(raw) > config.MAX_GUESTS:
        raise ValueError(f"At most {config.MAX_GUESTS} guests per run.")
    for g in raw:
        if not isinstance(g, dict):
            continue
        name = " ".join(str(g.get("name") or "").split())[:40]
        favs = g.get("favorites") or g.get("favourites") or []
        if isinstance(favs, str):
            favs = [f for f in favs.replace("|", ";").split(";")]
        favs = list(dict.fromkeys(" ".join(str(f).split())[:80] for f in favs if str(f).strip()))[:4]
        if not name:
            continue
        if name.lower() in seen:
            raise ValueError(f"Duplicate guest name '{name}'. Names must be unique (add a surname or initial).")
        seen.add(name.lower())
        guests.append({"name": name, "favorites": favs, "group": " ".join(str(g.get("group") or "").split())[:40]})
    if len(guests) < 2:
        raise ValueError("Add at least two guests.")
    if not any(g["favorites"] for g in guests):
        raise ValueError("Add at least one favourite.")
    try:
        size = min(max(int(data.get("table_size", seating.DEFAULT_TABLE_SIZE)), 2), 14)
    except (TypeError, ValueError):
        size = seating.DEFAULT_TABLE_SIZE
    try:
        seed = int(data.get("seed", 0))
    except (TypeError, ValueError):
        seed = 0
    clar = [{"question": str(c.get("question", ""))[:300], "answer": str(c.get("answer", ""))[:200]}
            for c in (data.get("clarifications") or [])[:3] if isinstance(c, dict) and c.get("answer")]
    return {"guests": guests, "table_size": size, "seed": seed, "clarifications": clar,
            "constraints_text": str(data.get("constraints_text") or "")[:1500].strip()}


def summarize(name, args, result, is_error):
    """One human-readable line per tool call for the live timeline."""
    if is_error:
        return "Rejected: " + str(result.get("error", ""))[:200]
    if name == "build_taste_profiles":
        extra = f", {len(result['unmatched_favorites'])} unmatched favourite(s)" if result.get("unmatched_favorites") else ""
        left = f", {result['remaining']} to go" if result.get("remaining") else ""
        return f"{result['profiled_total']}/{result['guests_total']} guests profiled{extra}{left}"
    if name == "apply_constraint":
        a = result.get("added", {})
        if a.get("kind") == "none":
            return "No seating requirement in the organiser's notes"
        who = a.get("group") or ", ".join(a.get("guests", [])[:3])
        return f"{a.get('kind')}: {who}" + (f" at table {a['table']}" if a.get("table") else "") + " (valid)"
    if name == "seat_guests":
        iso = len(result.get("isolated", []))
        return (f"{len(result['tables'])} tables, least-matched guest fits {result['min_fit']:.2f} "
                f"(random seating: {result['versus_random_seating']['min_fit']:.2f}), "
                f"{iso} isolated, constraints {'all met' if result['all_constraints_ok'] else 'NOT all met'}")
    if name == "explain_table":
        return f"{result['label']}: cohesion {result['cohesion']:.2f}, shares " + \
            (", ".join(s["tag"] for s in result["shared_tags"][:3]) or "no tags")
    if name == "table_card":
        return f"Card saved for table {result['table']} ({len(result['cards_done'])} done)"
    if name == "ask_organizer":
        return "Asked the organiser a question"
    if name == "present_plan":
        return "Plan delivered"
    return ""


def run(llm, qloo, request, emit=None, max_turns=None, max_qloo_calls=None):
    emit = emit or (lambda e: None)
    max_turns = max_turns or config.MAX_AGENT_TURNS
    t0 = time.time()
    steps, stats = [], {"turns": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
    status, error, nudged, llm_failure = None, None, False, None

    def step(ev):
        ev["t"] = round(time.time() - t0, 2)
        steps.append(ev)
        emit(ev)

    box = ToolBox(qloo, request["guests"], table_size=request.get("table_size", seating.DEFAULT_TABLE_SIZE),
                  seed=request.get("seed", 0), max_qloo_calls=max_qloo_calls or config.MAX_QLOO_CALLS, emit=step,
                  requirements=request.get("constraints_text") or "")
    first = build_user_message(request, include_json=getattr(llm, "provider", "") in ("mock", "scripted"))
    messages = [{"role": "user", "content": first}]
    step({"type": "start", "llm": getattr(llm, "label", "?"), "qloo": "SAMPLE" if getattr(qloo, "sample", False) else "live",
          "guests": len(request["guests"])})
    while stats["turns"] < max_turns and box.final is None:
        stats["turns"] += 1
        try:
            turn = llm.chat(SYSTEM_PROMPT, messages, TOOL_SPECS)
        except LLMError as e:
            if getattr(e, "fatal", False):
                status, error = "error", str(e)
            else:
                # Transient provider failure (overload, rate limit, network): finish from the tool results so far.
                llm_failure = str(e)
                step({"type": "thought", "text": f"The AI model is unavailable right now ({llm_failure}). "
                                                 "Finishing the plan from the work already done."})
            break
        stats["input_tokens"] += turn.input_tokens
        stats["output_tokens"] += turn.output_tokens
        if turn.text:
            step({"type": "thought", "text": turn.text[:600]})
        messages.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls, "raw": turn.raw})
        if not turn.tool_calls:
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Continue: use the tools, then finish by calling present_plan "
                                                        "(or ask_organizer if you truly need an answer)."})
            continue
        results = []
        for call in turn.tool_calls:
            stats["tool_calls"] += 1
            step({"type": "tool_call", "id": call.id, "name": call.name, "args": call.args})
            if box.final is not None:
                res, is_err = {"error": "The run already finished."}, True
            else:
                try:
                    res, is_err = box.run(call.name, call.args)
                except FatalToolError as e:
                    status, error = "error", str(e)
                    break
            step({"type": "tool_result", "id": call.id, "name": call.name, "error": is_err,
                  "summary": summarize(call.name, call.args, res, is_err), "data": res})
            results.append({"id": call.id, "name": call.name, "content": to_json(res), "is_error": is_err})
        if status == "error":
            break
        messages.append({"role": "tool", "results": results})

    plan = question = None
    if status != "error":
        if box.final and box.final[0] == "plan":
            status, plan = "plan", box.final[1]
        elif box.final and box.final[0] == "question":
            status, question = "question", box.final[1]
        else:
            reason = ("the AI model was unavailable" if llm_failure
                      else "turn limit reached" if stats["turns"] >= max_turns else "the model stopped early")
            plan = box.auto_plan(reason, profile_if_empty=bool(llm_failure))
            status = "plan" if plan else "error"
            error = None if plan else (llm_failure or "The agent stopped before it could build any taste profiles. Try again.")
    stats.update({"qloo_calls": box.qloo_used(), "seconds": round(time.time() - t0, 1)})
    sample = bool(getattr(qloo, "sample", False))
    out = {"status": status, "plan": plan, "question": question, "error": error, "stats": stats,
           "mode": {"llm": getattr(llm, "label", "?"), "llm_provider": getattr(llm, "provider", "?"),
                    "qloo": "sample" if sample else "live", "sample_data": sample},
           "steps": steps}
    step({"type": "done", "status": status})
    return out
