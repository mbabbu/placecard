"""Quality check of Placecard against the LIVE Qloo API (no LLM needed).

    python scripts/live_eval.py wedding            # resolution, vectors, separation, seating
    python scripts/live_eval.py conference --own 0 # insights-only vectors (no own-tag blend)
    python scripts/live_eval.py wedding --sweep    # tune the objective on the same profiles (no new Qloo calls)

Needs QLOO_API_KEY in the environment; it is never printed. Qloo results are cached in .cache/qloo.json
(ignored by git) so repeated runs and sweeps cost no quota. The guests come from presets.LIVE_PRESETS and
carry the taste "archetype" they were generated from, which gives a ground truth for separation: do guests
who share an archetype score higher than guests who do not?
"""
import argparse
import bisect
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from placecard import config, presets, qloo, seating  # noqa: E402
from placecard.tools import ToolBox  # noqa: E402

ROSTERS = {"wedding": presets.LIVE_WEDDING, "conference": presets.LIVE_CONFERENCE}


def build(preset, own, cache):
    key = config.qloo_key()
    if not key:
        sys.exit("QLOO_API_KEY is not set.")
    config.OWN_TAG_WEIGHT = own
    client = qloo.LiveQloo(key, cache_file=cache)
    p = presets.LIVE_PRESETS[preset]
    box = ToolBox(client, p["guests"], table_size=p["table_size"], max_qloo_calls=2000, requirements=p["constraints"])
    t0 = time.time()
    while True:
        out, err = box.run("build_taste_profiles", {})
        if err or not out["remaining"]:
            break
    client.save_cache()
    return box, client, out, time.time() - t0


def auc(same, diff):
    ds = sorted(diff)
    return sum(bisect.bisect_left(ds, x) + 0.5 * (bisect.bisect_right(ds, x) - bisect.bisect_left(ds, x))
               for x in same) / (len(same) * len(ds))


def separation(box, preset, use_idf=True):
    roster = ROSTERS[preset]
    arch = {n: (p, s) for n, _g, p, s in roster}
    names = box.names
    vecs = {n: box.profiles[n]["vector"] for n in names}
    C, known, _idf, _w = seating.compat_matrix(names, vecs, use_idf)
    same, sec, diff = [], [], []
    for i, a in enumerate(names):
        for j in range(i + 1, len(names)):
            if not (known[i] and known[j]):
                continue
            b = names[j]
            (pa, sa), (pb, sb) = arch[a], arch[b]
            (same if pa == pb else sec if {pa, sa} & {pb, sb} else diff).append(C[i][j])
    nn = 0
    for i, a in enumerate(names):
        if not known[i]:
            continue
        j = max((j for j in range(len(names)) if j != i and known[j]), key=lambda j: C[i][j])
        nn += bool(set(arch[a]) & set(arch[names[j]]))
    mean = lambda x: sum(x) / len(x) if x else 0.0
    return {"auc_same_vs_diff": round(auc(same, diff), 3), "mean_same": round(mean(same), 3),
            "mean_overlap": round(mean(sec), 3), "mean_diff": round(mean(diff), 3),
            "nn_shares_archetype": round(nn / sum(known), 2)}


def table_purity(box, plan, preset):
    """Mean share of table-mates who have an archetype in common with a guest (higher = more coherent tables)."""
    arch = {n: set((p, s)) for n, _g, p, s in ROSTERS[preset]}
    shares = []
    for t in plan["tables"]:
        for a in t["guests"]:
            others = [b for b in t["guests"] if b != a]
            if others:
                shares.append(sum(1 for b in others if arch[a] & arch[b]) / len(others))
    return round(sum(shares) / len(shares), 3)


def oracle_purity(box, preset, constraints, seeds):
    """Table purity of the best seating the optimiser finds when it is given the ground-truth archetypes as tags:
    an upper bound for what any taste signal could achieve on this party."""
    vecs = {n: {p: 1.0, s: 0.5} for n, _g, p, s in ROSTERS[preset]}
    return mean_over(box, preset, vecs, constraints, seeds, calibrate_scores=False, use_idf=False)


def mean_over(box, preset, vectors, constraints, seeds, **kw):
    rows = []
    for seed in seeds:
        p = seating.seat(box.names, vectors, box.table_size, constraints=constraints, seed=seed, **kw)
        rows.append((table_purity(box, p, preset), p["min_fit"], p["avg_cohesion"], len(p["isolated"]),
                     p["baseline"]["min_fit"]))
    n = len(rows)
    return {"purity": round(sum(r[0] for r in rows) / n, 3), "min_fit": round(sum(r[1] for r in rows) / n, 3),
            "cohesion": round(sum(r[2] for r in rows) / n, 3), "isolated": round(sum(r[3] for r in rows) / n, 2),
            "random_min_fit": round(sum(r[4] for r in rows) / n, 3)}


def report(preset, own, cache, sweep, free=False):
    box, client, last, secs = build(preset, own, cache)
    guests = presets.LIVE_PRESETS[preset]["guests"]
    favs = sum(len(g["favorites"]) for g in guests)
    unmatched = [(n, f) for n, p in box.profiles.items() for f in p["unmatched"]]
    sizes = [len(p["vector"]) for p in box.profiles.values()]
    types = {}
    for p in box.profiles.values():
        for e in p["entities"]:
            types[e["type"]] = types.get(e["type"], 0) + 1
    print(f"== {preset} (own-tag weight {own})")
    print(f"favourites {favs}, matched {favs - len(unmatched)} ({100 * (favs - len(unmatched)) / favs:.1f}%), unmatched {unmatched}")
    print(f"resolved types {dict(sorted(types.items(), key=lambda kv: str(kv[0])))}")
    print(f"guests with no taste data {sum(1 for p in box.profiles.values() if p['status'] != 'ok')}; "
          f"vector size min/mean/max {min(sizes)}/{sum(sizes) / len(sizes):.0f}/{max(sizes)}")
    print(f"assumptions {len(box.assumptions)}: {box.assumptions[:3]}")
    print(f"profile build {secs:.1f}s, Qloo network calls {client.calls}, monthly quota left {client.quota()['remaining']}")
    print("separation", separation(box, preset))
    print("separation without idf", separation(box, preset, use_idf=False))
    box._auto_apply_requirements()
    names, vectors = box.names, {n: p["vector"] for n, p in box.profiles.items()}

    def seat(**kw):
        return seating.seat(names, vectors, box.table_size, constraints=box.constraints, seed=0, **kw)

    t0 = time.time()
    plan = seat()
    print(f"seating {time.time() - t0:.2f}s: min_fit {plan['min_fit']} vs random {plan['baseline']['min_fit']}; "
          f"avg_cohesion {plan['avg_cohesion']} vs random {plan['baseline']['avg_cohesion']}; "
          f"isolated {len(plan['isolated'])}, unavoidable {len(plan['unavoidable_isolated'])}; "
          f"close threshold {plan['close_threshold']}; constraints ok {plan['all_constraints_ok']}; "
          f"table purity {table_purity(box, plan, preset)}")
    rnd = seating.baseline_random(names, vectors, plan["table_sizes"], *seating.compat_matrix(names, vectors)[:2], 0, trials=200)
    print(f"random-seating purity is about {1 / len(plan['tables']):.2f}-0.2; random min_fit {rnd['min_fit']}")
    for t in plan["tables"]:
        tags = [box.tag_name(s["tag_id"]) for s in t["shared_tags"][:4]]
        print(f"  {t['label']:<11} cohesion {t['cohesion']:.3f} min_fit {t['min_fit']:.3f}  {', '.join(t['guests'][:5])}...  | {tags}")
    seeds = range(6)
    for label, cons in (("with the organiser's constraints", box.constraints), ("no constraints", None)):
        print(f"-- {label}: mean over {len(seeds)} seeds")
        print("   oracle (ground-truth archetypes as tags):", oracle_purity(box, preset, cons, seeds))
        print("   placecard default:", mean_over(box, preset, vectors, cons, seeds))
        if sweep:
            for cal in (True, False):
                for lam in (0.4, 0.6, 0.8):
                    for pct in (0.5, 0.7, 0.85):
                        for idf in (True, False):
                            r = mean_over(box, preset, vectors, cons, seeds, lam=lam, iso_percentile=pct, use_idf=idf,
                                          calibrate_scores=cal)
                            print(f"   calibrate {cal!s:5} lam {lam} pct {pct} idf {idf!s:5}: {r}")
    return box, plan


def check_pools(cache):
    """Resolve every favourite in presets.LIVE_POOLS (and the mystery list) and print what Qloo returns."""
    from concurrent.futures import ThreadPoolExecutor
    key = config.qloo_key()
    if not key:
        sys.exit("QLOO_API_KEY is not set.")
    client = qloo.LiveQloo(key, cache_file=cache)
    pools = [(k, f) for k, v in presets.LIVE_POOLS.items() for f in v]
    box = ToolBox(client, [{"name": "x", "favorites": []}], max_qloo_calls=5000)
    todo = list(dict.fromkeys(f for _k, f in pools))

    def one(fav):
        title, hint = qloo.split_hint(fav)
        notes = []
        pick = box._choose(title, box._search(title, hint), notes)
        return fav, pick, notes

    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(one, todo))
    client.save_cache()
    bad = 0
    for fav, pick, notes in results:
        if pick is None:
            bad += 1
            print(f"UNMATCHED  {fav!r}")
        else:
            print(f"ok  {fav!r:46} -> {pick['name'][:44]!r:48} {pick['type']:<11} pop {(pick['popularity'] or 0):.2f}"
                  + ("   NOTE " + notes[0][:90] if notes else ""))
    print(f"{len(results) - bad}/{len(results)} resolve; Qloo calls {client.calls}; quota left {client.quota()['remaining']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", choices=sorted(ROSTERS) + ["check"])
    ap.add_argument("--own", type=float, default=config.OWN_TAG_WEIGHT, help="own-tag blend weight (0 = insights only)")
    ap.add_argument("--cache", default=os.path.join(ROOT, ".cache", "qloo.json"))
    ap.add_argument("--sweep", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.cache), exist_ok=True)
    if a.preset == "check":
        check_pools(a.cache)
    else:
        report(a.preset, a.own, a.cache, a.sweep)
