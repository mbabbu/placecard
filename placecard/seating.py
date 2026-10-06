"""Seating optimiser. Pure Python, no I/O, deterministic for a given seed, so the LLM never does arithmetic.

Input: one taste vector per guest ({tag_id: affinity}, from Qloo taste analysis) plus hard constraints.

1. Pairwise compatibility = cosine similarity of idf-weighted tag vectors (a tag that everyone has,
   like "Drama", counts for less than a distinctive one, like "Korean Cuisine").
2. Tables: n guests go to k = ceil(n / table_size) tables of balanced size. Hard constraints:
   together (guests or whole groups), apart, spread (a group split over the tables, at most
   ceil(n / tables) per table), fixed table, head table. Constraints are honoured by treating
   "together" guests as one block that is never split and by refusing any move that breaks the others.
3. Objective (maximised):  LAMBDA * min_fit + (1 - LAMBDA) * avg_cohesion - RHO * isolated_fraction
     fit(g)        = mean compatibility of g with their table-mates      (the worst guest matters most)
     cohesion(t)   = mean pairwise compatibility at table t
     isolated      = a guest with no table-mate above the "close" threshold, although such company exists
                     somewhere in the party ("isolation guarantee where possible")
4. Search: a farthest-first greedy start, then swap/move local search that evaluates candidate moves
   incrementally (O(table size) each), worst-fit guests first. A few seeded restarts; the best wins.
"""
import math
import random
import re

LAMBDA = 0.6
RHO = 1.0
DEFAULT_TABLE_SIZE = 8


class SeatingError(ValueError):
    """The request cannot be seated (unknown guest, contradictory or impossible constraints)."""


# ----------------------------------------------------------------------------- compatibility
def flat(vec):
    """Accept {tag: affinity} or {tag: {"affinity": x, ...}} and return {tag: float > 0}."""
    out = {}
    for t, x in (vec or {}).items():
        a = x.get("affinity") if isinstance(x, dict) else x
        try:
            a = float(a)
        except (TypeError, ValueError):
            continue
        if a > 0:
            out[t] = a
    return out


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    dot = sum(x * b.get(t, 0.0) for t, x in a.items())
    if dot <= 0:
        return 0.0
    na = math.sqrt(sum(x * x for x in a.values()))
    nb = math.sqrt(sum(x * x for x in b.values()))
    return min(dot / (na * nb), 1.0) if na and nb else 0.0


def idf_weights(vectors):
    """Smoothed inverse document frequency over the guests that have a vector."""
    vecs = [v for v in vectors if v]
    n = len(vecs)
    df = {}
    for v in vecs:
        for t in v:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}


def compat_matrix(names, vectors, use_idf=True):
    """Returns (C, known, idf, weighted). C[i][j] in [0, 1], C[i][i] = 0. Guests with no taste data
    ('known' False) get the party's average compatibility with everyone, i.e. a neutral placeholder."""
    flats = [flat(vectors.get(nm)) for nm in names]
    known = [bool(v) for v in flats]
    idf = idf_weights(flats) if use_idf else {}
    weighted = [{t: a * idf.get(t, 1.0) for t, a in v.items()} if use_idf else v for v in flats]
    n = len(names)
    C = [[0.0] * n for _ in range(n)]
    tot, cnt = 0.0, 0
    for i in range(n):
        if not known[i]:
            continue
        for j in range(i + 1, n):
            if known[j]:
                C[i][j] = C[j][i] = cosine(weighted[i], weighted[j])
                tot += C[i][j]
                cnt += 1
    neutral = tot / cnt if cnt else 0.0
    for i in range(n):
        for j in range(n):
            if i != j and not (known[i] and known[j]):
                C[i][j] = neutral
    return C, known, idf, weighted


CALIBRATION_QUANTILE = 0.9    # the pair at this quantile of the party's compatibilities ...
CALIBRATION_TARGET = 0.6      # ... is mapped to this score
CALIBRATION_SKIP_ABOVE = 0.3  # ... unless it already scores at least this much (hand-built SAMPLE vectors do)


def calibrate(C, known, quantile=CALIBRATION_QUANTILE, target=CALIBRATION_TARGET, skip_above=CALIBRATION_SKIP_ABOVE):
    """Rescale compatibilities so the party's `quantile` pair scores `target` (clipped at 1). Returns (C', factor).

    Cosines of the sparse, ~300-tag vectors Qloo returns are small (typically 0.02-0.2, TUNING.md). The objective
    adds a fit term to an isolation penalty that is measured in 'fractions of guests', so with raw cosines one
    isolated guest outweighs every fit gain. Scaling every pair by one factor keeps the ranking of pairs (and the
    cosine itself unchanged) and puts the two terms on a comparable footing. A party whose pairs already score
    `skip_above` or more at that quantile is left alone."""
    n = len(C)
    vals = sorted(C[i][j] for i in range(n) for j in range(i + 1, n) if known[i] and known[j])
    if not vals:
        return C, 1.0
    ref = vals[min(int(len(vals) * quantile), len(vals) - 1)]
    if ref <= 1e-9 or (skip_above is not None and ref >= skip_above):
        return C, 1.0
    f = target / ref
    return [[min(c * f, 1.0) for c in row] for row in C], f


def close_threshold(C, known, percentile=0.7):
    vals = sorted(C[i][j] for i in range(len(C)) for j in range(i + 1, len(C)) if known[i] and known[j])
    if not vals:
        return 0.0
    return vals[min(int(len(vals) * percentile), len(vals) - 1)]


# ----------------------------------------------------------------------------- constraints
def spread_cap(n, k):
    """Most members of an n-guest 'spread' group that may share one of k tables."""
    return -(-n // max(k, 1))


def table_sizes(n, table_size, tables=None):
    """Balanced table sizes: sizes differ by at most one and never exceed table_size."""
    if table_size < 2:
        raise SeatingError("A table needs at least 2 seats.")
    if n < 1:
        raise SeatingError("There are no guests to seat.")
    k = int(tables) if tables else -(-n // table_size)
    if k < 1:
        raise SeatingError("There must be at least one table.")
    if k * table_size < n:
        raise SeatingError(f"{n} guests do not fit at {k} tables of {table_size} seats.")
    k = min(k, max(n // 2, 1))
    base, rem = divmod(n, k)
    return [base + 1] * rem + [base] * (k - rem)


def normalise_constraints(names, constraints):
    """Validate a constraints dict against the guest list and return a canonical copy.

    {"together": [[a, b, ...], ...], "apart": [[a, b, ...], ...], "spread": [[a, b, ...], ...],
     "fixed": {name: table_number}, "head": [names]}  (table numbers are 1-based; the head table is table 1).
    "spread": the listed guests are divided over the tables as evenly as possible (at most ceil(n / tables) each)."""
    known = set(names)
    c = constraints or {}

    def check(group, what):
        out = []
        for g in group:
            if g not in known:
                raise SeatingError(f"Unknown guest '{g}' in {what}.")
            if g not in out:
                out.append(g)
        return out

    together = [check(g, "a 'together' constraint") for g in c.get("together") or []]
    apart = [check(g, "an 'apart' constraint") for g in c.get("apart") or []]
    spread = [check(g, "a 'spread' constraint") for g in c.get("spread") or []]
    fixed = {}
    for g, t in (c.get("fixed") or {}).items():
        check([g], "a 'fixed' constraint")
        try:
            t = int(t)
        except (TypeError, ValueError):
            raise SeatingError(f"Table for {g} must be a number.") from None
        if t < 1:
            raise SeatingError("Table numbers start at 1.")
        fixed[g] = t
    head = check(c.get("head") or [], "the head table")
    for g in head:
        if fixed.get(g, 1) != 1:
            raise SeatingError(f"{g} is both at the head table and fixed to table {fixed[g]}.")
        fixed[g] = 1
    return {"together": [g for g in together if len(g) > 1], "apart": [g for g in apart if len(g) > 1],
            "spread": [g for g in spread if len(g) > 1], "fixed": fixed, "head": head}


def check_constraints(names, tab, constraints):
    """Report every constraint with ok True/False for a finished assignment (tab: name -> 0-based table)."""
    rep = []
    k = max(tab.values()) + 1 if tab else 1
    for g in constraints.get("spread", []):
        cap = spread_cap(len(g), k)
        per = {}
        for x in g:
            per[tab[x]] = per.get(tab[x], 0) + 1
        shown = ", ".join(g[:4]) + (f" and {len(g) - 4} more" if len(g) > 4 else "")
        rep.append({"type": "spread", "guests": g, "cap": cap, "ok": max(per.values()) <= cap,
                    "text": f"Spread across tables (at most {cap} per table): {shown}"})
    for g in constraints.get("together", []):
        ok = len({tab[x] for x in g}) == 1
        rep.append({"type": "together", "guests": g, "ok": ok, "text": "Together: " + ", ".join(g)})
    for g in constraints.get("apart", []):
        ok = len({tab[x] for x in g}) == len(g)
        rep.append({"type": "apart", "guests": g, "ok": ok, "text": "Apart: " + ", ".join(g)})
    for g, t in sorted(constraints.get("fixed", {}).items()):
        if g in constraints.get("head", []):
            continue
        rep.append({"type": "fixed", "guests": [g], "table": t, "ok": tab[g] == t - 1,
                    "text": f"{g} at table {t}"})
    if constraints.get("head"):
        rep.append({"type": "head", "guests": constraints["head"], "ok": all(tab[g] == 0 for g in constraints["head"]),
                    "text": "Head table: " + ", ".join(constraints["head"])})
    return rep


# ----------------------------------------------------------------------------- objective
def score_assignment(tables, C, mask, close, unavoid, lam=LAMBDA, rho=RHO):
    """Reference (non-incremental) objective. tables: list of lists of guest indices.
    mask[i]: counts toward min_fit; close[i][j]: 0/1 'close' pair; unavoid[i]: isolation cannot be avoided."""
    n = sum(len(t) for t in tables)
    fits, cohs, iso = [], [], 0
    for members in tables:
        s = len(members)
        pair = 0.0
        for a in members:
            w = sum(C[a][m] for m in members if m != a)
            pair += w
            if mask[a]:
                fits.append(w / (s - 1) if s > 1 else 0.0)
            if not unavoid[a] and not any(close[a][m] for m in members if m != a):
                iso += 1
        cohs.append(pair / (s * (s - 1)) if s > 1 else 0.0)
    min_fit = min(fits) if fits else 0.0
    avg_coh = sum(cohs) / len(cohs) if cohs else 0.0
    return lam * min_fit + (1 - lam) * avg_coh - rho * iso / max(n, 1)


class _Stuck(Exception):
    pass


class _Search:
    """Incremental local search over blocks of guests (singletons or 'together' groups)."""

    def __init__(self, C, mask, close, unavoid, blocks, pinned, apart_of, sizes, lam, rho, spreads=()):
        self.C, self.mask, self.close, self.unavoid = C, mask, close, unavoid
        self.spreads = list(spreads)    # [(set of guest indices, cap per table)]
        self.blocks, self.pinned, self.apart_of = blocks, pinned, apart_of
        self.k, self.n = len(sizes), len(C)
        self.sizes0, self.lam, self.rho = sizes, lam, rho
        self.lo, self.hi = min(sizes), max(sizes)
        self.blk_of = {}
        for b, mem in enumerate(blocks):
            for g in mem:
                self.blk_of[g] = b
        self.sp_of = [[si for si, (S, _cap) in enumerate(self.spreads) if g in S] for g in range(self.n)]

    # -- initial placement -------------------------------------------------------------
    def initial(self, rng):
        n, k, C = self.n, self.k, self.C
        members = [[] for _ in range(k)]
        tab = [-1] * n
        btab = [None] * len(self.blocks)

        def fits(b, t):
            mem = self.blocks[b]
            if len(members[t]) + len(mem) > self.sizes0[t]:
                return False
            for S, cap in self.spreads:
                if sum(1 for g in members[t] if g in S) + sum(1 for g in mem if g in S) > cap:
                    return False
            return not any(p in members[t] for g in mem for p in self.apart_of[g])

        def put(b, t):
            btab[b] = t
            for g in self.blocks[b]:
                tab[g] = t
                members[t].append(g)

        def affinity(b, t):
            if not members[t]:
                return 0.0
            return sum(C[g][m] for g in self.blocks[b] for m in members[t]) / (len(self.blocks[b]) * len(members[t]))

        for b, t in enumerate(self.pinned):
            if t is not None:
                if t >= k or not fits(b, t):
                    raise _Stuck("pinned")
                put(b, t)
        free = [b for b in range(len(self.blocks)) if self.pinned[b] is None]
        free.sort(key=lambda b: (-len(self.blocks[b]), not any(self.sp_of[g] for g in self.blocks[b]), rng.random()))
        # farthest-first seeds for tables that start empty
        empty = [t for t in range(k) if not members[t]]
        for t in empty:
            cand = [b for b in free if len(self.blocks[b]) <= self.sizes0[t] and fits(b, t)]
            if not cand:
                continue
            placed = [g for m in members for g in m]
            if placed:
                pick = min(cand, key=lambda b: (max(C[g][m] for g in self.blocks[b] for m in placed), rng.random()))
            else:
                pick = cand[rng.randrange(len(cand))]
            put(pick, t)
            free.remove(pick)
        for b in free:
            options = [t for t in range(k) if fits(b, t)]
            if not options:
                raise _Stuck("no table")
            # prefer the best-matching table; tie-break towards the emptiest
            t = max(options, key=lambda t: (affinity(b, t), self.sizes0[t] - len(members[t]), -t))
            put(b, t)
        if any(len(members[t]) != self.sizes0[t] for t in range(k)):
            raise _Stuck("sizes")
        return tab

    # -- state -------------------------------------------------------------------------
    def load(self, tab):
        n, k, C, close = self.n, self.k, self.C, self.close
        self.tab = list(tab)
        self.members = [[] for _ in range(k)]
        for g, t in enumerate(tab):
            self.members[t].append(g)
        self.S = [[0.0] * k for _ in range(n)]
        self.K = [[0] * k for _ in range(n)]
        for g in range(n):
            row, crow, S, Kc = C[g], close[g], self.S[g], self.K[g]
            for m in range(n):
                if m != g:
                    S[tab[m]] += row[m]
                    Kc[tab[m]] += crow[m]
        self.sc = [[sum(1 for g in self.members[t] if g in S) for t in range(k)] for S, _cap in self.spreads]
        self.tmin = [0.0] * k
        self.tcoh = [0.0] * k
        self.tiso = [0] * k
        for t in range(k):
            self._recompute(t)

    def _recompute(self, t):
        mem = self.members[t]
        s = len(mem)
        fits = [self.S[g][t] / (s - 1) if s > 1 else 0.0 for g in mem if self.mask[g]]
        self.tmin[t] = min(fits) if fits else 9.0  # 9.0 = "no constraint on the minimum"
        self.tcoh[t] = sum(self.S[g][t] for g in mem) / (s * (s - 1)) if s > 1 else 0.0
        self.tiso[t] = sum(1 for g in mem if not self.unavoid[g] and self.K[g][t] == 0)

    def objective(self):
        gmin = min(self.tmin)
        gmin = 0.0 if gmin > 8 else gmin
        return (self.lam * gmin + (1 - self.lam) * sum(self.tcoh) / self.k - self.rho * sum(self.tiso) / self.n)

    # -- incremental evaluation --------------------------------------------------------
    def _side(self, t, out, inc):
        """(sum of within-sums, min fit, isolated count) of table t after removing `out`, adding `inc`."""
        C, close, S, Kc, mask, unavoid = self.C, self.close, self.S, self.K, self.mask, self.unavoid
        s = len(self.members[t]) - len(out) + len(inc)
        tot, mn, iso = 0.0, 9.0, 0
        d = s - 1
        for m in self.members[t]:
            if m in out:
                continue
            w, kk, cm, clm = S[m][t], Kc[m][t], C[m], close[m]
            for a in out:
                w -= cm[a]
                kk -= clm[a]
            for b in inc:
                w += cm[b]
                kk += clm[b]
            tot += w
            if mask[m]:
                f = w / d if d > 0 else 0.0
                if f < mn:
                    mn = f
            if kk <= 0 and not unavoid[m]:
                iso += 1
        for b in inc:
            w, kk, cm, clm = S[b][t], Kc[b][t], C[b], close[b]
            for a in out:
                w -= cm[a]
                kk -= clm[a]
            for b2 in inc:
                if b2 != b:
                    w += cm[b2]
                    kk += clm[b2]
            tot += w
            if mask[b]:
                f = w / d if d > 0 else 0.0
                if f < mn:
                    mn = f
            if kk <= 0 and not unavoid[b]:
                iso += 1
        coh = tot / (s * (s - 1)) if s > 1 else 0.0
        return coh, mn, iso

    def _apart_ok(self, t, moving, leaving):
        """May `moving` (a block) join table t once `leaving` guests have left it?"""
        for g in moving:
            for p in self.apart_of[g]:
                if self.tab[p] == t and p not in leaving:
                    return False
        return True

    def _try(self, X, A, Y, B, cur_gmin_others):
        """Objective after exchanging block A (at X) with block B (at Y); B == [] moves A to Y. None if illegal."""
        sx = len(self.members[X]) - len(A) + len(B)
        sy = len(self.members[Y]) - len(B) + len(A)
        if sx < self.lo or sx > self.hi or sy < self.lo or sy > self.hi:
            return None
        if not self._apart_ok(Y, A, B) or (B and not self._apart_ok(X, B, A)):
            return None
        for si, (S, cap) in enumerate(self.spreads):
            a = sum(1 for g in A if g in S)
            b = sum(1 for g in B if g in S)
            if a != b and (self.sc[si][Y] - b + a > cap or self.sc[si][X] - a + b > cap):
                return None
        cx, mx, ix = self._side(X, A, B)
        cy, my, iy = self._side(Y, B, A)
        gmin = min(cur_gmin_others, mx, my)
        gmin = 0.0 if gmin > 8 else gmin
        coh = (sum(self.tcoh) - self.tcoh[X] - self.tcoh[Y] + cx + cy) / self.k
        iso = sum(self.tiso) - self.tiso[X] - self.tiso[Y] + ix + iy
        return self.lam * gmin + (1 - self.lam) * coh - self.rho * iso / self.n

    def _apply(self, X, A, Y, B):
        C, close, S, Kc = self.C, self.close, self.S, self.K
        for a in A:
            self.members[X].remove(a)
            self.tab[a] = Y
        for b in B:
            self.members[Y].remove(b)
            self.tab[b] = X
        self.members[Y].extend(A)
        self.members[X].extend(B)
        for a in A:
            for si in self.sp_of[a]:
                self.sc[si][X] -= 1
                self.sc[si][Y] += 1
        for b in B:
            for si in self.sp_of[b]:
                self.sc[si][Y] -= 1
                self.sc[si][X] += 1
        for g in range(self.n):
            Sg, Kg, cg, clg = S[g], Kc[g], C[g], close[g]
            for a in A:
                if g != a:
                    Sg[X] -= cg[a]
                    Sg[Y] += cg[a]
                    Kg[X] -= clg[a]
                    Kg[Y] += clg[a]
            for b in B:
                if g != b:
                    Sg[Y] -= cg[b]
                    Sg[X] += cg[b]
                    Kg[Y] -= clg[b]
                    Kg[X] += clg[b]
        self._recompute(X)
        self._recompute(Y)

    # -- search ------------------------------------------------------------------------
    def block_fit(self, b):
        mem = self.blocks[b]
        t = self.tab[mem[0]]
        s = len(self.members[t])
        return min((self.S[g][t] / (s - 1) if s > 1 else 0.0) for g in mem)

    def improve(self, targets=3, max_passes=60):
        eps = 1e-12
        moves = 0
        for _ in range(max_passes):
            improved = False
            cur = self.objective()
            order = sorted((b for b in range(len(self.blocks)) if self.pinned[b] is None), key=self.block_fit)
            for b in order:
                A = self.blocks[b]
                X = self.tab[A[0]]
                scores = []
                for t in range(self.k):
                    if t != X:
                        scores.append((-sum(self.S[g][t] for g in A) / (len(A) * max(len(self.members[t]), 1)), t))
                scores.sort()
                best_obj, best = cur + eps, None
                for _s, Y in scores[:targets]:
                    others = min((self.tmin[t] for t in range(self.k) if t != X and t != Y), default=9.0)
                    # a pure move into free space
                    obj = self._try(X, A, Y, [], others)
                    if obj is not None and obj > best_obj:
                        best_obj, best = obj, (X, A, Y, [])
                    seen = set()
                    for g in self.members[Y]:
                        bb = self.blk_of[g]
                        if bb in seen or self.pinned[bb] is not None:
                            continue
                        seen.add(bb)
                        B = self.blocks[bb]
                        obj = self._try(X, A, Y, B, others)
                        if obj is not None and obj > best_obj:
                            best_obj, best = obj, (X, A, Y, B)
                if best:
                    self._apply(*best)
                    cur = self.objective()
                    improved = True
                    moves += 1
            if not improved:
                break
        return moves


def _blocks_and_pins(names, idx, c, k, hi):
    n = len(names)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for grp in c["together"]:
        for g in grp[1:]:
            parent[find(idx[g])] = find(idx[grp[0]])
    groups = {}
    for g in range(n):
        groups.setdefault(find(g), []).append(g)
    blocks = sorted(groups.values(), key=lambda m: m[0])
    blk_of = {g: b for b, m in enumerate(blocks) for g in m}
    pinned = [None] * len(blocks)
    for name, t in c["fixed"].items():
        if t > k:
            raise SeatingError(f"Table {t} does not exist: there are only {k} tables.")
        b = blk_of[idx[name]]
        if pinned[b] not in (None, t - 1):
            raise SeatingError(f"Guests who must sit together are fixed to different tables (including {name}).")
        pinned[b] = t - 1
    for b, m in enumerate(blocks):
        if len(m) > hi:
            raise SeatingError(f"{len(m)} guests must sit together but a table has at most {hi} seats.")
    load = {}
    for b, t in enumerate(pinned):
        if t is not None:
            load[t] = load.get(t, 0) + len(blocks[b])
            if load[t] > hi:
                raise SeatingError(f"Too many guests are fixed to table {t + 1} (limit {hi}).")
    apart_of = [set() for _ in range(n)]
    for grp in c["apart"]:
        for a in grp:
            for b in grp:
                if a != b:
                    if blk_of[idx[a]] == blk_of[idx[b]]:
                        raise SeatingError(f"{a} and {b} must be both together and apart.")
                    apart_of[idx[a]].add(idx[b])
    for a in range(n):
        for p in apart_of[a]:
            ta, tp = pinned[blk_of[a]], pinned[blk_of[p]]
            if ta is not None and ta == tp:
                raise SeatingError(f"{names[a]} and {names[p]} must be apart but are fixed to the same table.")
    spreads = []
    for grp in c["spread"]:
        S, cap = {idx[g] for g in grp}, spread_cap(len(grp), k)
        for m in blocks:
            if sum(1 for g in m if g in S) > cap:
                raise SeatingError(f"Guests who must sit together include more than {cap} of the guests to be spread "
                                   f"across {k} tables (at most {cap} per table).")
        for t in range(k):
            fixed_here = sum(len([g for g in blocks[b] if g in S]) for b, pt in enumerate(pinned) if pt == t)
            if fixed_here > cap:
                raise SeatingError(f"Table {t + 1} is fixed for more than {cap} of the guests to be spread across "
                                   f"{k} tables (at most {cap} per table).")
        spreads.append((S, cap))
    return blocks, pinned, apart_of, spreads


# ----------------------------------------------------------------------------- public API
def check_feasible(names, constraints, table_size, tables=None):
    """Raise SeatingError if the constraints are contradictory or impossible for this party; else return
    the canonical constraints. Used to validate each constraint as the organiser adds it."""
    sizes = table_sizes(len(names), table_size, tables)
    c = normalise_constraints(names, constraints)
    _blocks_and_pins(names, {nm: i for i, nm in enumerate(names)}, c, len(sizes), max(sizes))
    return c



def seat(names, vectors, table_size=DEFAULT_TABLE_SIZE, tables=None, constraints=None, seed=0, restarts=3,
         lam=LAMBDA, rho=RHO, iso_percentile=0.7, use_idf=True, calibrate_scores=True):
    """Seat guests. Returns a plan dict (see plan_from_assignment)."""
    names = list(names)
    if len(set(names)) != len(names):
        raise SeatingError("Guest names must be unique.")
    sizes = table_sizes(len(names), table_size, tables)
    k = len(sizes)
    c = normalise_constraints(names, constraints)
    idx = {nm: i for i, nm in enumerate(names)}
    n = len(names)
    blocks, pinned, apart_of, spreads = _blocks_and_pins(names, idx, c, k, max(sizes))
    C, known, idf, _w = compat_matrix(names, vectors, use_idf)
    scale = 1.0
    if calibrate_scores:
        C, scale = calibrate(C, known)
    tau = close_threshold(C, known, iso_percentile)
    close = [[1 if (i != j and C[i][j] >= tau and known[i] and known[j]) else 0 for j in range(n)] for i in range(n)]
    unavoid = [not known[i] or not any(close[i]) for i in range(n)]
    search = _Search(C, known, close, unavoid, blocks, pinned, apart_of, sizes, lam, rho, spreads)
    best, best_obj, inc_obj = None, -1e9, 0.0
    stuck = 0
    for r in range(max(1, restarts) + 30):
        rng = random.Random(f"{seed}:{r}")
        try:
            tab = search.initial(rng)
        except _Stuck:
            stuck += 1
            continue
        search.load(tab)
        search.improve(targets=3)
        search.improve(targets=min(k, 6))
        if n <= 60:
            search.improve(targets=k)
        obj = search.objective()
        if obj > best_obj + 1e-12:
            best_obj, best = obj, list(search.tab)
            inc_obj = obj
        if r - stuck + 1 >= max(1, restarts):
            break
    if best is None:
        raise SeatingError(f"These constraints cannot all be met with {k} tables of up to {max(sizes)} seats. "
                           "Try fewer constraints, or more or bigger tables.")
    return plan_from_assignment(names, vectors, best, C, known, idf, tau, close, unavoid, c, k, seed, lam, rho,
                                search_info={"restarts": restarts, "sizes": sizes, "incremental_objective": round(inc_obj, 4),
                                             "similarity_scale": round(scale, 3)})


def plan_from_assignment(names, vectors, tab, C, known, idf, tau, close, unavoid, c, k, seed, lam=LAMBDA, rho=RHO,
                         search_info=None):
    n = len(names)
    tables = [[] for _ in range(k)]
    for g, t in enumerate(tab):
        tables[t].append(g)
    obj = score_assignment(tables, C, known, close, unavoid, lam, rho)
    flats = [flat(vectors.get(nm)) for nm in names]
    tag_names = {t: x["name"] for v in vectors.values() for t, x in (v or {}).items()
                 if isinstance(x, dict) and x.get("name")}
    require_fav = any(isinstance(x, dict) and "fav" in x for v in vectors.values() for x in (v or {}).values())
    elig = [displayable(vectors.get(nm), require_fav) for nm in names]
    guests, table_out = {}, []
    all_fits, cohs = [], []
    for t, mem in enumerate(tables):
        s = len(mem)
        fits = {}
        for g in mem:
            fits[g] = sum(C[g][m] for m in mem if m != g) / (s - 1) if s > 1 else 0.0
        coh = sum(fits.values()) / s if s > 1 else 0.0
        cohs.append(coh)
        for g in mem:
            mates = sorted((m for m in mem if m != g), key=lambda m: -C[g][m])
            iso = (not unavoid[g]) and not any(close[g][m] for m in mem if m != g)
            guests[names[g]] = {
                "table": t + 1, "fit": round(fits[g], 4), "no_data": not known[g],
                "best_mate": {"name": names[mates[0]], "compat": round(C[g][mates[0]], 4)} if mates else None,
                "close_mates": [names[m] for m in mates if close[g][m]][:6],
                "isolated": iso, "unavoidable": bool(known[g] and unavoid[g]),
                "shared_tags": guest_shared_tags([flats[g]], [flats[m] for m in mates if known[m]], idf,
                                                 [names[m] for m in mates if known[m]], names=tag_names,
                                                 elig=(elig[g], [elig[m] for m in mates if known[m]])),
            }
            if known[g]:
                all_fits.append(fits[g])
        ordered = sorted(mem, key=lambda g: -fits[g])
        known_mem = [g for g in mem if known[g]]
        table_out.append({
            "index": t + 1, "label": "Head table" if t == 0 and c["head"] else f"Table {t + 1}",
            "head": bool(t == 0 and c["head"]), "guests": [names[g] for g in ordered], "size": s,
            "cohesion": round(coh, 4),
            "min_fit": round(min((fits[g] for g in known_mem), default=0.0), 4),
            "shared_tags": table_shared_tags([flats[g] for g in known_mem], [names[g] for g in known_mem], idf,
                                             names=tag_names, elig=[elig[g] for g in known_mem]),
        })
    tabmap = {names[g]: tab[g] for g in range(n)}
    base = baseline_random(names, vectors, [len(m) for m in tables], C, known, seed)
    isolated = [nm for nm, g in guests.items() if g["isolated"]]
    return {
        "tables": table_out, "guests": guests,
        "objective": round(obj, 4), "min_fit": round(min(all_fits, default=0.0), 4),
        "avg_fit": round(sum(all_fits) / len(all_fits), 4) if all_fits else 0.0,
        "avg_cohesion": round(sum(cohs) / len(cohs), 4) if cohs else 0.0,
        "isolated": isolated,
        "unavoidable_isolated": [nm for nm, g in guests.items() if g["unavoidable"] and not g["no_data"]
                                 and g["close_mates"] == []],
        "no_data": [nm for nm, g in guests.items() if g["no_data"]],
        "close_threshold": round(tau, 4), "baseline": base,
        "constraints": check_constraints(names, tabmap, c), "seed": seed,
        "all_constraints_ok": all(r["ok"] for r in check_constraints(names, tabmap, c)),
        "table_sizes": [len(m) for m in tables], "search": search_info or {},
    }


def _canon(name):
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _distinct(rows, names, limit):
    """Keep the first row per tag name once spelling is ignored ('k pop', 'K-Pop' and 'Kpop' are one tag).
    rows are (score, tag_id, ...) tuples already sorted best first."""
    out, seen = [], set()
    for r in rows:
        key = _canon((names or {}).get(r[1], r[1]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) == limit:
            break
    return out


def displayable(vec, require_fav):
    """Tags of one guest's vector that may be shown to the organiser: topics (not mood) that a listed favourite
    actually carries. Vectors without 'kind' / 'fav' markers (SAMPLE data, hand-built tests) show every tag."""
    return {t for t, x in (vec or {}).items()
            if not isinstance(x, dict) or (x.get("kind", "topic") == "topic" and (not require_fav or x.get("fav", 0) > 0))}


def guest_shared_tags(one, mates, idf, mate_names, limit=4, names=None, elig=None):
    """Tags this guest shares with table-mates: [{tag_id, with: [names]}], strongest first.
    elig = (guest's displayable tags, [each mate's displayable tags]) or None for all."""
    me = one[0]
    out = []
    for t, a in me.items():
        if elig is not None and t not in elig[0]:
            continue
        who = [mate_names[i] for i, m in enumerate(mates) if t in m and (elig is None or t in elig[1][i])]
        if who:
            mean_m = sum(m[t] for m in mates if t in m) / len(who)
            out.append((min(a, mean_m) * idf.get(t, 1.0) * (0.5 + len(who) / max(len(mates), 1)), t, who))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [{"tag_id": t, "with": who[:6]} for _s, t, who in _distinct(out, names, limit)]


def table_shared_tags(flats, member_names, idf, limit=6, min_coverage=0.4, names=None, elig=None):
    """Tags that at least two guests (and >= min_coverage of the table) have, most characteristic first:
    [{tag_id, coverage, mean_affinity, members}]."""
    n = len(flats)
    if n < 2:
        return []
    rows = []
    tags = set()
    for f in flats:
        tags.update(f)
    for t in tags:
        have = [i for i, f in enumerate(flats) if t in f and (elig is None or t in elig[i])]
        if len(have) < 2 or len(have) / n < min_coverage:
            continue
        mean_a = sum(flats[i][t] for i in have) / len(have)
        cov = len(have) / n
        rows.append((cov * mean_a * idf.get(t, 1.0), t, cov, mean_a, [member_names[i] for i in have]))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [{"tag_id": t, "coverage": round(cov, 3), "mean_affinity": round(a, 3), "members": mem}
            for _s, t, cov, a, mem in _distinct(rows, names, limit)]


def baseline_random(names, vectors, sizes, C, known, seed, trials=20):
    """Average min_fit and cohesion of unconstrained random seatings, for the 'vs random' comparison."""
    rng = random.Random(f"base:{seed}")
    n = len(names)
    mins, cohs = [], []
    for _ in range(trials):
        order = list(range(n))
        rng.shuffle(order)
        pos, fits, coh = 0, [], []
        for s in sizes:
            mem = order[pos:pos + s]
            pos += s
            if s < 2:
                coh.append(0.0)
                continue
            f = {g: sum(C[g][m] for m in mem if m != g) / (s - 1) for g in mem}
            fits += [f[g] for g in mem if known[g]]
            coh.append(sum(f.values()) / s)
        mins.append(min(fits, default=0.0))
        cohs.append(sum(coh) / len(coh))
    return {"min_fit": round(sum(mins) / trials, 4), "avg_cohesion": round(sum(cohs) / trials, 4)}
