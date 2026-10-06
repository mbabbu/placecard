"""Run the full Placecard agent on a preset against LIVE Qloo, with the MOCK agent or a real LLM.

    python scripts/live_run.py wedding --llm mock
    python scripts/live_run.py conference --llm gemini        # GEMINI_API_KEY from the environment

Prints a summary only (never a key). Qloo results are cached in .cache/qloo.json unless --no-cache.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from placecard import agent, config, presets, qloo  # noqa: E402
from placecard.llm import get_llm  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", choices=sorted(presets.LIVE_PRESETS))
    ap.add_argument("--llm", default="mock", choices=["mock", "gemini", "anthropic"])
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--json", help="write the full result (no keys in it) to this path")
    a = ap.parse_args()
    if not config.qloo_key():
        sys.exit("QLOO_API_KEY is not set.")
    cache = None if a.no_cache else os.path.join(ROOT, ".cache", "qloo.json")
    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
    client = qloo.LiveQloo(config.qloo_key(), cache_file=cache)
    p = presets.LIVE_PRESETS[a.preset]
    req = agent.normalise_request({"guests": p["guests"], "table_size": p["table_size"],
                                   "constraints_text": p["constraints"]})
    llm = get_llm(a.llm)
    t0 = time.time()
    out = agent.run(llm, client, req)
    client.save_cache()
    plan, st = out["plan"], out["stats"]
    print(f"== {a.preset} with {out['mode']['llm']}: status {out['status']} in {time.time() - t0:.1f}s")
    print(f"turns {st['turns']}, tool calls {st['tool_calls']}, tokens in/out {st['input_tokens']}/{st['output_tokens']}, "
          f"Qloo network calls {st['qloo_calls']}, quota left {client.quota()['remaining']}")
    calls, rejected = {}, 0
    for s in out["steps"]:
        if s["type"] == "tool_call":
            calls[s["name"]] = calls.get(s["name"], 0) + 1
        if s["type"] == "thought" and "unavailable" in s.get("text", ""):
            print("   LLM trouble:", s["text"][:300])
        if s["type"] == "tool_result" and s.get("error"):
            rejected += 1
            if s["name"] == "table_card":
                print("   card rejected:", s["summary"][:160])
    print("tool calls:", calls, "| rejected results:", rejected)
    if out["error"]:
        print("error:", out["error"])
    if plan:
        m = plan["metrics"]
        print(f"min_fit {m['min_fit']} (random {m['baseline']['min_fit']}), avg_cohesion {m['avg_cohesion']} "
              f"(random {m['baseline']['avg_cohesion']}), isolated {len(m['isolated'])}, no data {len(m['no_data'])}, "
              f"constraints ok {plan['all_constraints_ok']}")
        print("headline:", plan["headline"])
        print("assumptions:", plan["assumptions"][:4])
        print("notes:", plan["notes"][:3])
        for t, c in zip(plan["tables"], plan["cards"]):
            print(f"  {t['label']} cohesion {t['cohesion']:.2f} [{c['source']}] {c['title']}")
            print(f"     {c['opener']}")
            for tp in c["topics"]:
                print(f"     - {tp['tag']}: {tp['because']}")
            print(f"     ask: {c['ask']}")
    if a.json:
        with open(a.json, "w", encoding="utf8") as f:
            json.dump({k: v for k, v in out.items() if k != "steps"}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
