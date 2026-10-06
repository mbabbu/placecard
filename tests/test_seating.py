"""Seating optimiser: hand-checked maths, constraints, isolation, determinism, brute-force optimality, speed."""
import itertools
import math
import random
import time
import unittest

from placecard import seating
from placecard.seating import SeatingError


def clusters(sizes, noise=0.0, seed=0):
    """Guests in clean taste clusters: cluster c has its own 3 tags. Returns (names, vectors, cluster_of)."""
    rng = random.Random(seed)
    names, vecs, which = [], {}, {}
    for c, size in enumerate(sizes):
        for i in range(size):
            nm = f"C{c}_{i}"
            names.append(nm)
            which[nm] = c
            v = {f"c{c}_a": 0.9, f"c{c}_b": 0.8, f"c{c}_c": 0.7}
            if noise:
                v[f"n{rng.randrange(5)}"] = noise
            vecs[nm] = v
    return names, vecs, which


def tables_of(plan):
    return [set(t["guests"]) for t in plan["tables"]]


class Maths(unittest.TestCase):
    def test_cosine_hand_checked(self):
        self.assertAlmostEqual(seating.cosine({"x": 1, "y": 1}, {"x": 1}), 1 / math.sqrt(2))
        self.assertEqual(seating.cosine({"x": 1}, {"y": 1}), 0.0)
        self.assertAlmostEqual(seating.cosine({"x": 2, "y": 2}, {"x": 1, "y": 1}), 1.0)
        self.assertEqual(seating.cosine({}, {"x": 1}), 0.0)

    def test_flat_accepts_both_shapes_and_drops_non_positive(self):
        self.assertEqual(seating.flat({"a": {"affinity": 0.5, "name": "A"}, "b": 0.2, "c": 0, "d": None, "e": "x"}),
                         {"a": 0.5, "b": 0.2})

    def test_idf_and_weighted_compatibility_hand_checked(self):
        # A={t1,t2} B={t1,t3} C={t2}. df: t1=2 t2=2 t3=1, n=3 -> idf = ln(4/(1+df)) + 1
        names = ["A", "B", "C"]
        vecs = {"A": {"t1": 1, "t2": 1}, "B": {"t1": 1, "t3": 1}, "C": {"t2": 1}}
        i12, i3 = math.log(4 / 3) + 1, math.log(4 / 2) + 1
        C, known, idf, _ = seating.compat_matrix(names, vecs)
        self.assertAlmostEqual(idf["t1"], i12)
        self.assertAlmostEqual(idf["t3"], i3)
        want_ab = i12 * i12 / (math.sqrt(2) * i12 * math.sqrt(i12 ** 2 + i3 ** 2))
        self.assertAlmostEqual(C[0][1], want_ab, places=9)
        self.assertAlmostEqual(want_ab, 0.428, places=3)   # the number worked out by hand
        self.assertAlmostEqual(C[0][2], 1 / math.sqrt(2), places=9)
        self.assertEqual(C[1][2], 0.0)
        self.assertEqual([C[i][i] for i in range(3)], [0.0] * 3)
        self.assertTrue(all(known))

    def test_without_idf_is_plain_cosine(self):
        C, *_ = seating.compat_matrix(["A", "B"], {"A": {"x": 1, "y": 1}, "B": {"x": 1}}, use_idf=False)
        self.assertAlmostEqual(C[0][1], 1 / math.sqrt(2))

    def test_guest_without_data_is_neutral(self):
        names = ["A", "B", "C", "Z"]
        vecs = {"A": {"x": 1}, "B": {"x": 1, "y": 1}, "C": {"y": 1}, "Z": {}}
        C, known, _, _ = seating.compat_matrix(names, vecs, use_idf=False)
        self.assertEqual(known, [True, True, True, False])
        mean_known = (C[0][1] + C[0][2] + C[1][2]) / 3
        self.assertAlmostEqual(C[3][0], mean_known)
        self.assertAlmostEqual(C[1][3], mean_known)

    def test_table_sizes_are_balanced_and_bounded(self):
        self.assertEqual(seating.table_sizes(40, 8), [8] * 5)
        self.assertEqual(seating.table_sizes(43, 8), [8, 7, 7, 7, 7, 7])
        self.assertEqual(seating.table_sizes(10, 4), [4, 3, 3])
        self.assertEqual(seating.table_sizes(10, 8, tables=3), [4, 3, 3])
        with self.assertRaises(SeatingError):
            seating.table_sizes(10, 4, tables=2)
        with self.assertRaises(SeatingError):
            seating.table_sizes(10, 1)

    def test_score_assignment_hand_checked(self):
        # Two tables of two. C(A,B)=0.8, C(C,D)=0.6, cross pairs 0. lam=0.6, rho=0
        C = [[0, .8, 0, 0], [.8, 0, 0, 0], [0, 0, 0, .6], [0, 0, .6, 0]]
        close = [[0] * 4 for _ in range(4)]
        obj = seating.score_assignment([[0, 1], [2, 3]], C, [True] * 4, close, [True] * 4, lam=0.6, rho=0)
        # min_fit = 0.6, avg cohesion = (0.8 + 0.6) / 2 = 0.7 -> 0.6*0.6 + 0.4*0.7
        self.assertAlmostEqual(obj, 0.36 + 0.28)
        bad = seating.score_assignment([[0, 2], [1, 3]], C, [True] * 4, close, [True] * 4, lam=0.6, rho=0)
        self.assertEqual(bad, 0.0)


class Basics(unittest.TestCase):
    def test_two_obvious_clusters_are_seated_together(self):
        names, vecs, _ = clusters([3, 3])
        for seed in range(5):
            plan = seating.seat(names, vecs, table_size=3, seed=seed, calibrate_scores=False)
            self.assertEqual(sorted(map(sorted, tables_of(plan))),
                             [["C0_0", "C0_1", "C0_2"], ["C1_0", "C1_1", "C1_2"]])
            self.assertEqual(plan["isolated"], [])
            self.assertGreater(plan["min_fit"], 0.99)

    def test_hand_checked_four_guests(self):
        # A1~A2 share a tag, B1~B2 share another: the only good split is {A1,A2},{B1,B2}.
        vecs = {"A1": {"a": 1, "m": 0.2}, "A2": {"a": 1, "n": 0.2}, "B1": {"b": 1, "m": 0.2}, "B2": {"b": 1, "n": 0.2}}
        names = list(vecs)
        plan = seating.seat(names, vecs, table_size=2, seed=0, calibrate_scores=False)
        self.assertEqual(sorted(map(sorted, tables_of(plan))), [["A1", "A2"], ["B1", "B2"]])
        C, *_ = seating.compat_matrix(names, vecs)
        self.assertAlmostEqual(plan["guests"]["A1"]["fit"], C[0][1], places=3)
        self.assertAlmostEqual(plan["guests"]["B2"]["fit"], C[2][3], places=3)
        self.assertEqual(plan["guests"]["A1"]["best_mate"]["name"], "A2")
        self.assertAlmostEqual(plan["avg_cohesion"], (C[0][1] + C[2][3]) / 2, places=3)
        self.assertAlmostEqual(plan["min_fit"], min(C[0][1], C[2][3]), places=3)

    def test_every_guest_seated_once_and_sizes_balanced(self):
        names, vecs, _ = clusters([5, 4, 7, 3, 6], noise=0.3)
        plan = seating.seat(names, vecs, table_size=6, seed=2)
        seated = [g for t in plan["tables"] for g in t["guests"]]
        self.assertEqual(sorted(seated), sorted(names))
        sizes = [t["size"] for t in plan["tables"]]
        self.assertLessEqual(max(sizes) - min(sizes), 1)
        self.assertLessEqual(max(sizes), 6)
        for nm, g in plan["guests"].items():
            self.assertIn(nm, plan["tables"][g["table"] - 1]["guests"])

    def test_incremental_search_matches_reference_objective(self):
        names, vecs, _ = clusters([5, 4, 7, 3, 6], noise=0.4, seed=4)
        plan = seating.seat(names, vecs, table_size=6, seed=1)
        self.assertAlmostEqual(plan["search"]["incremental_objective"], plan["objective"], places=3)

    def test_shared_tags_per_table_and_guest(self):
        names, vecs, _ = clusters([4, 4])
        plan = seating.seat(names, vecs, table_size=4)
        for t in plan["tables"]:
            tags = {x["tag_id"] for x in t["shared_tags"]}
            self.assertEqual(len(tags), 3)
            c = {g.split("_")[0] for g in t["guests"]}
            self.assertEqual(len(c), 1)
            self.assertTrue(all(x["tag_id"].startswith(f"{next(iter(c)).lower()}_") for x in t["shared_tags"]))
            self.assertEqual(t["shared_tags"][0]["coverage"], 1.0)
        g = plan["guests"]["C0_0"]
        self.assertEqual({x["tag_id"] for x in g["shared_tags"]}, {"c0_a", "c0_b", "c0_c"})
        self.assertEqual(len(g["shared_tags"][0]["with"]), 3)

    def test_beats_random_seating(self):
        names, vecs, _ = clusters([6, 6, 6, 6, 6], noise=0.5, seed=9)
        plan = seating.seat(names, vecs, table_size=6, seed=3)
        self.assertGreater(plan["min_fit"], plan["baseline"]["min_fit"] * 3)
        self.assertGreater(plan["avg_cohesion"], plan["baseline"]["avg_cohesion"] * 2)

    def test_single_table_and_tiny_parties(self):
        names, vecs, _ = clusters([3])
        plan = seating.seat(names, vecs, table_size=8)
        self.assertEqual(len(plan["tables"]), 1)
        self.assertEqual(plan["tables"][0]["size"], 3)
        plan = seating.seat(["A", "B"], {"A": {"x": 1}, "B": {"x": 1}}, table_size=4)
        self.assertEqual(len(plan["tables"]), 1)
        with self.assertRaises(SeatingError):
            seating.seat([], {}, 4)
        with self.assertRaises(SeatingError):
            seating.seat(["A", "A"], {}, 4)

    def test_guests_without_data_are_seated_and_flagged(self):
        names, vecs, _ = clusters([3, 3])
        names += ["Nodata1", "Nodata2"]
        plan = seating.seat(names, vecs, table_size=4, seed=1)
        self.assertEqual(sorted(plan["no_data"]), ["Nodata1", "Nodata2"])
        self.assertTrue(plan["guests"]["Nodata1"]["no_data"])
        self.assertEqual(sum(t["size"] for t in plan["tables"]), 8)
        self.assertNotIn("Nodata1", plan["isolated"])


class Determinism(unittest.TestCase):
    def test_same_seed_same_plan(self):
        names, vecs, _ = clusters([5, 5, 5, 5], noise=0.5, seed=1)
        a = seating.seat(names, vecs, 5, seed=11)
        b = seating.seat(names, vecs, 5, seed=11)
        self.assertEqual(a, b)

    def test_other_seeds_are_valid_and_similar_in_quality(self):
        names, vecs, _ = clusters([6, 6, 6, 6], noise=0.5, seed=1)
        objs = [seating.seat(names, vecs, 6, seed=s)["objective"] for s in range(6)]
        self.assertLess(max(objs) - min(objs), 0.1)


class Constraints(unittest.TestCase):
    def setUp(self):
        self.names, self.vecs, self.which = clusters([6, 6, 6, 6], noise=0.3, seed=2)

    def test_together_pair_from_different_clusters(self):
        plan = seating.seat(self.names, self.vecs, 6, constraints={"together": [["C0_0", "C1_0"]]})
        self.assertTrue(plan["all_constraints_ok"])
        self.assertEqual(plan["guests"]["C0_0"]["table"], plan["guests"]["C1_0"]["table"])

    def test_together_group_of_three(self):
        grp = ["C0_0", "C1_0", "C2_0"]
        plan = seating.seat(self.names, self.vecs, 6, constraints={"together": [grp]})
        self.assertEqual(len({plan["guests"][g]["table"] for g in grp}), 1)

    def test_overlapping_together_groups_merge(self):
        plan = seating.seat(self.names, self.vecs, 6, constraints={"together": [["C0_0", "C1_0"], ["C1_0", "C2_0"]]})
        self.assertEqual(len({plan["guests"][g]["table"] for g in ("C0_0", "C1_0", "C2_0")}), 1)

    def test_apart_separates_two_cluster_mates(self):
        plan = seating.seat(self.names, self.vecs, 6, constraints={"apart": [["C0_0", "C0_1"]]})
        self.assertNotEqual(plan["guests"]["C0_0"]["table"], plan["guests"]["C0_1"]["table"])
        self.assertTrue(plan["all_constraints_ok"])

    def test_apart_group_of_four_goes_to_four_tables(self):
        grp = ["C0_0", "C0_1", "C0_2", "C0_3"]
        plan = seating.seat(self.names, self.vecs, 6, constraints={"apart": [grp]})
        self.assertEqual(len({plan["guests"][g]["table"] for g in grp}), 4)

    def test_fixed_table_and_head_table(self):
        plan = seating.seat(self.names, self.vecs, 6, constraints={"fixed": {"C3_3": 4}, "head": ["C2_0", "C2_1"]})
        self.assertEqual(plan["guests"]["C3_3"]["table"], 4)
        self.assertEqual(plan["guests"]["C2_0"]["table"], 1)
        self.assertEqual(plan["guests"]["C2_1"]["table"], 1)
        self.assertEqual(plan["tables"][0]["label"], "Head table")
        self.assertTrue(plan["tables"][0]["head"])
        self.assertTrue(plan["all_constraints_ok"])
        self.assertEqual({c["type"] for c in plan["constraints"]}, {"fixed", "head"})

    def test_everything_at_once_in_a_big_party(self):
        names, vecs, _ = clusters([8] * 8, noise=0.4, seed=5)
        cons = {"together": [names[:3], names[8:10]], "apart": [[names[3], names[4]], [names[16], names[17], names[18]]],
                "fixed": {names[40]: 5}, "head": [names[50]]}
        for seed in range(4):
            plan = seating.seat(names, vecs, 8, constraints=cons, seed=seed)
            self.assertTrue(plan["all_constraints_ok"], plan["constraints"])
            self.assertEqual(sum(t["size"] for t in plan["tables"]), 64)

    def test_constraints_hold_even_when_they_hurt_quality(self):
        free = seating.seat(self.names, self.vecs, 6, seed=0)
        cons = {"apart": [[a, b] for a, b in itertools.combinations(["C0_0", "C0_1", "C0_2"], 2)]}
        tight = seating.seat(self.names, self.vecs, 6, constraints=cons, seed=0)
        self.assertTrue(tight["all_constraints_ok"])
        self.assertLessEqual(tight["objective"], free["objective"] + 1e-9)

    def test_invalid_constraints_are_rejected_with_clear_messages(self):
        bad = [
            ({"together": [["C0_0", "Nobody"]]}, "Unknown guest"),
            ({"together": [["C0_0", "C0_1"]], "apart": [["C0_0", "C0_1"]]}, "together and apart"),
            ({"together": [self.names[:7]]}, "at most"),
            ({"fixed": {"C0_0": 9}}, "does not exist"),
            ({"fixed": {"C0_0": 0}}, "start at 1"),
            ({"fixed": {"C0_0": 2}, "head": ["C0_0"]}, "both at the head"),
            ({"together": [["C0_0", "C1_0"]], "fixed": {"C0_0": 1, "C1_0": 2}}, "different tables"),
            ({"apart": [["C0_0", "C0_1"]], "fixed": {"C0_0": 2, "C0_1": 2}}, "same table"),
            ({"fixed": {f"C{c}_{i}": 1 for c in range(2) for i in range(4)}}, "Too many"),
        ]
        for cons, msg in bad:
            with self.assertRaises(SeatingError, msg=str(cons)) as cm:
                seating.seat(self.names, self.vecs, 6, constraints=cons)
            self.assertIn(msg, str(cm.exception), str(cons))

    def per_table(self, plan, group):
        counts = {}
        for g in group:
            counts[plan["guests"][g]["table"]] = counts.get(plan["guests"][g]["table"], 0) + 1
        return counts

    def test_spread_splits_a_whole_taste_cluster_over_the_tables(self):
        grp = [f"C0_{i}" for i in range(6)]            # six guests who would love to share one table
        free = seating.seat(self.names, self.vecs, 6, seed=0)
        self.assertEqual(max(self.per_table(free, grp).values()), 6)
        plan = seating.seat(self.names, self.vecs, 6, constraints={"spread": [grp]}, seed=0)
        self.assertEqual(len(plan["tables"]), 4)
        self.assertLessEqual(max(self.per_table(plan, grp).values()), math.ceil(6 / 4))   # at most 2 per table
        rep = [r for r in plan["constraints"] if r["type"] == "spread"]
        self.assertEqual(len(rep), 1)
        self.assertTrue(rep[0]["ok"] and plan["all_constraints_ok"])
        self.assertEqual(rep[0]["cap"], 2)
        self.assertIn("Spread across tables (at most 2 per table)", rep[0]["text"])
        self.assertEqual(plan["table_sizes"], [6, 6, 6, 6])

    def test_spread_group_that_fits_the_tables_puts_one_per_table(self):
        grp = ["C0_0", "C0_1", "C0_2", "C0_3"]
        plan = seating.seat(self.names, self.vecs, 6, constraints={"spread": [grp]}, seed=1)
        self.assertEqual(sorted(self.per_table(plan, grp).values()), [1, 1, 1, 1])

    def test_spread_group_larger_than_tables_times_cap(self):
        grp = [f"C{c}_{i}" for c in range(2) for i in range(5)]       # 10 guests, 4 tables -> at most 3 each
        plan = seating.seat(self.names, self.vecs, 6, constraints={"spread": [grp]}, seed=3)
        self.assertLessEqual(max(self.per_table(plan, grp).values()), 3)
        self.assertTrue(plan["all_constraints_ok"])

    def test_spread_combines_with_together_apart_and_fixed(self):
        grp = [f"C0_{i}" for i in range(6)]
        cons = {"spread": [grp], "together": [["C0_0", "C0_1"]], "apart": [["C1_0", "C1_1"]],
                "fixed": {"C0_2": 1}}
        plan = seating.seat(self.names, self.vecs, 6, constraints=cons, seed=4)
        self.assertTrue(plan["all_constraints_ok"])
        self.assertEqual(plan["guests"]["C0_0"]["table"], plan["guests"]["C0_1"]["table"])
        self.assertLessEqual(max(self.per_table(plan, grp).values()), 2)
        self.assertEqual(plan["guests"]["C0_2"]["table"], 1)

    def test_impossible_spread_is_rejected(self):
        grp = [f"C0_{i}" for i in range(6)]
        with self.assertRaises(SeatingError) as cm:
            seating.seat(self.names, self.vecs, 6, constraints={"spread": [grp], "together": [grp[:3]]})
        self.assertIn("at most 2 per table", str(cm.exception))
        with self.assertRaises(SeatingError) as cm:
            seating.seat(self.names, self.vecs, 6, constraints={"spread": [grp], "fixed": {g: 2 for g in grp[:3]}})
        self.assertIn("Table 2 is fixed", str(cm.exception))
        with self.assertRaises(SeatingError):
            seating.seat(self.names, self.vecs, 6, constraints={"spread": [["C0_0", "Nobody"]]})

    def test_normalise_drops_singleton_groups_and_dedups(self):
        c = seating.normalise_constraints(["a", "b", "c"], {"together": [["a"], ["a", "b", "a"]], "head": ["c"]})
        self.assertEqual(c["together"], [["a", "b"]])
        self.assertEqual(c["fixed"], {"c": 1})


class Isolation(unittest.TestCase):
    def test_nobody_isolated_in_clean_clusters(self):
        names, vecs, _ = clusters([4, 4, 4, 4])
        plan = seating.seat(names, vecs, 4, seed=1)
        self.assertEqual(plan["isolated"], [])
        for g in plan["guests"].values():
            self.assertGreaterEqual(len(g["close_mates"]), 1)

    def test_pairs_are_kept_with_their_soulmate(self):
        # Eight guests, four pairs of soulmates, tables of 4. Nobody may sit with strangers only,
        # even though the list order puts soulmates at opposite ends.
        vecs, names = {}, []
        for p in range(4):
            for j in range(2):
                nm = f"P{p}{j}"
                names.append(nm)
                vecs[nm] = {f"p{p}": 1.0, f"p{p}x": 0.9}
        order = [names[i] for i in (0, 2, 4, 6, 1, 3, 5, 7)]
        plan = seating.seat(order, vecs, 4, seed=2)
        self.assertEqual(plan["isolated"], [])
        for p in range(4):
            self.assertEqual(plan["guests"][f"P{p}0"]["table"], plan["guests"][f"P{p}1"]["table"])

    def test_isolation_is_repaired_where_possible_under_constraints(self):
        names, vecs, _ = clusters([5, 5, 5])
        plan = seating.seat(names, vecs, 5, constraints={"fixed": {"C0_0": 2, "C1_0": 1}}, seed=3)
        self.assertEqual(plan["isolated"], [])
        self.assertTrue(plan["all_constraints_ok"])

    def test_unavoidable_isolation_is_flagged_but_does_not_hurt_others(self):
        names, vecs, _ = clusters([4, 4])
        names.append("Loner")
        vecs["Loner"] = {"unique_tag": 1.0}
        plan = seating.seat(names, vecs, 5, seed=1)
        self.assertIn("Loner", plan["unavoidable_isolated"])
        self.assertNotIn("Loner", plan["isolated"])
        for nm in names[:-1]:
            mates = [g for g in plan["tables"][plan["guests"][nm]["table"] - 1]["guests"] if g not in (nm, "Loner")]
            self.assertTrue(all(m.split("_")[0] == nm.split("_")[0] for m in mates))

    def test_isolated_guests_are_reported_when_constraints_force_it(self):
        names, vecs, _ = clusters([3, 3])
        plan = seating.seat(names, vecs, 3, constraints={"together": [["C0_0", "C1_0", "C1_1"]]}, seed=0)
        self.assertIn("C0_0", plan["isolated"])


def brute_force_best(names, vecs, sizes, lam=seating.LAMBDA, rho=seating.RHO):
    C, known, _idf, _w = seating.compat_matrix(names, vecs)
    tau = seating.close_threshold(C, known, 0.7)
    n = len(names)
    close = [[1 if (i != j and C[i][j] >= tau and known[i] and known[j]) else 0 for j in range(n)] for i in range(n)]
    unavoid = [not known[i] or not any(close[i]) for i in range(n)]
    best = -1e9

    def rec(left, ti, acc):
        nonlocal best
        if ti == len(sizes) - 1:
            best = max(best, seating.score_assignment(acc + [left], C, known, close, unavoid, lam, rho))
            return
        for comb in itertools.combinations(left, sizes[ti]):
            rec([g for g in left if g not in comb], ti + 1, acc + [list(comb)])

    rec(list(range(n)), 0, [])
    return best


class Calibration(unittest.TestCase):
    """Live Qloo vectors give small cosines (about 0.02-0.2); calibrate() rescales them by one factor."""

    def test_the_90th_percentile_pair_maps_to_the_target_and_order_is_kept(self):
        C = [[0, .02, .04, .1], [.02, 0, .06, .08], [.04, .06, 0, .01], [.1, .08, .01, 0]]
        cal, f = seating.calibrate(C, [True] * 4, quantile=0.9, target=0.6)
        self.assertAlmostEqual(f, 0.6 / 0.1)                      # the top pair (0.1) is at the 0.9 quantile of 6 pairs
        self.assertAlmostEqual(cal[0][3], 0.6)
        self.assertAlmostEqual(cal[0][1], 0.12)
        self.assertEqual(cal[2][2], 0.0)
        flat_in = sorted(((C[i][j], (i, j)) for i in range(4) for j in range(i + 1, 4)))
        flat_out = sorted(((cal[i][j], (i, j)) for i in range(4) for j in range(i + 1, 4)))
        self.assertEqual([p for _, p in flat_in], [p for _, p in flat_out])

    def test_values_are_clipped_at_one_and_degenerate_input_is_left_alone(self):
        cal, f = seating.calibrate([[0, .9], [.9, 0]], [True, True], target=3.0, skip_above=None)
        self.assertEqual(cal[0][1], 1.0)
        zero = [[0, 0], [0, 0]]
        self.assertEqual(seating.calibrate(zero, [True, True]), (zero, 1.0))
        self.assertEqual(seating.calibrate(zero, [False, False]), (zero, 1.0))

    def test_scores_that_are_already_readable_are_left_alone(self):
        C = [[0, .5, .4], [.5, 0, .45], [.4, .45, 0]]
        self.assertEqual(seating.calibrate(C, [True] * 3), (C, 1.0))         # 90th percentile 0.5 >= 0.3

    def test_seat_reports_the_scale_and_keeps_the_same_tables(self):
        vecs = {}
        for g in range(3):
            for i in range(4):
                v = {f"g{g}_{k}": 1.0 for k in range(3)}
                v.update({f"noise{g}_{i}_{k}": 1.0 for k in range(30)})     # private tags: small cosines
                vecs[f"G{g}_{i}"] = v
        names = list(vecs)
        raw = seating.seat(names, vecs, 4, seed=1, calibrate_scores=False)
        cal = seating.seat(names, vecs, 4, seed=1)
        self.assertEqual(raw["search"]["similarity_scale"], 1.0)
        self.assertGreater(cal["search"]["similarity_scale"], 3.0)
        self.assertEqual(sorted(map(sorted, tables_of(raw))), sorted(map(sorted, tables_of(cal))))
        self.assertLess(raw["avg_cohesion"], 0.15)
        self.assertGreater(cal["avg_cohesion"], 0.5)
        for nm in names:
            self.assertLessEqual(cal["guests"][nm]["fit"], 1.0)

    def test_small_cosines_do_not_let_isolation_swamp_the_fit(self):
        # Two clean groups plus a loner who shares a faint tag with one of them. With vectors this sparse
        # the raw cosines are tiny; the calibrated plan still seats the groups together.
        vecs = {}
        for g in range(2):
            for i in range(4):
                v = {f"g{g}_{k}": 1.0 for k in range(3)}
                v.update({f"noise{g}_{i}_{k}": 1.0 for k in range(40)})   # many private tags -> small cosines
                vecs[f"G{g}_{i}"] = v
        names = list(vecs)
        plan = seating.seat(names, vecs, 4, seed=0)
        self.assertEqual(sorted(map(sorted, tables_of(plan))),
                         [sorted(n for n in names if n.startswith("G0")), sorted(n for n in names if n.startswith("G1"))])
        self.assertLess(seating.compat_matrix(names, vecs)[0][0][1], 0.1)      # raw cosines are small


class Optimality(unittest.TestCase):
    def test_matches_brute_force_on_small_random_parties(self):
        rng = random.Random(21)
        for trial in range(12):
            n, size = rng.choice([(6, 3), (8, 4), (8, 3), (7, 4)])
            names = [f"g{i}" for i in range(n)]
            tags = [f"t{i}" for i in range(7)]
            vecs = {nm: {t: rng.uniform(0.3, 1) for t in rng.sample(tags, rng.randint(2, 4))} for nm in names}
            plan = seating.seat(names, vecs, size, seed=trial, restarts=6, calibrate_scores=False)
            want = brute_force_best(names, vecs, plan["table_sizes"])
            self.assertAlmostEqual(plan["objective"], want, places=3, msg=f"trial {trial} n={n} size={size}")


class Performance(unittest.TestCase):
    def party(self, n, seed=3):
        rng = random.Random(seed)
        tags = [f"t{i}" for i in range(60)]
        arche = [rng.sample(tags, 6) for _ in range(12)]
        names = [f"G{i}" for i in range(n)]
        vecs = {}
        for nm in names:
            a, b = rng.choice(arche), rng.choice(arche)
            v = {t: rng.uniform(0.5, 1) for t in a}
            for t in rng.sample(b, 2):
                v[t] = rng.uniform(0.3, 0.8)
            for t in rng.sample(tags, 3):
                v[t] = rng.uniform(0.2, 0.5)
            vecs[nm] = v
        return names, vecs

    def test_120_guests_under_two_seconds_with_constraints(self):
        names, vecs = self.party(120)
        cons = {"together": [names[:4], names[10:13]], "apart": [[names[0], names[20]], [names[1], names[30], names[40]]],
                "fixed": {names[5]: 3}, "head": [names[6], names[7]]}
        t0 = time.time()
        plan = seating.seat(names, vecs, 8, constraints=cons, seed=5)
        self.assertLess(time.time() - t0, 2.0)
        self.assertTrue(plan["all_constraints_ok"])
        self.assertEqual(len(plan["tables"]), 15)
        self.assertEqual(plan["isolated"], [])
        self.assertGreater(plan["min_fit"], plan["baseline"]["min_fit"] * 2)

    def test_120_guests_without_constraints(self):
        names, vecs = self.party(120, seed=8)
        t0 = time.time()
        plan = seating.seat(names, vecs, 10, seed=1)
        self.assertLess(time.time() - t0, 2.0)
        self.assertEqual(sum(t["size"] for t in plan["tables"]), 120)


if __name__ == "__main__":
    unittest.main()
