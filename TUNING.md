# Tuning Placecard on live Qloo data

Everything here was measured on 2026-10-06 against the real hackathon API (`https://hackathon.api.qloo.com`) with
`scripts/live_eval.py` (resolution, vectors, separation, seating) and `scripts/live_run.py` (the whole agent). Both
scripts print summaries only, never a key, and cache Qloo results in `.cache/qloo.json` (git-ignored), so every sweep
below used the same recorded responses. About 1,200 of the key's 10,000 monthly requests were spent on all of it.

## What the live API actually returns (and what the code had wrong)

The first version of the client was written from the docs. Calling the API showed these differences. Each is now
handled in `placecard/qloo.py`, with trimmed real responses in `tests/fixtures/`.

| Finding | Fix |
|---|---|
| `/search` entities have `types: ["urn:entity:movie"]` (a list), no `subtype`; tags are `{name, tag_id, type, value}` and there are up to ~320 per entity | `_norm_entity` reads `types`; own tags are kept (capped at 150) with ids |
| Some tags have a UUID `tag_id` and put the URN in `value` | `_tag_key` prefers the URN |
| `/search` `types` takes a comma list and an unrestricted search returns albums, actors, directors and localities | favourites are searched in nine culture types; a trailing hint `(game)`, `(book)`, `(band)` narrows it |
| `/search` spells it `urn:entity:videogame`; insights spells it `video_game` | two maps, `SEARCH_TYPES` and `TYPES` (the 400 body lists the allowed values) |
| `/v2/insights` with `filter.type=urn:tag` returns `results.tags[]` with `query.affinity`: **the field names were right** | unchanged |
| `take` above 50 is a 400 | clamped to 50 |
| Rate limits come back in headers: `X-Second-RateLimit-Limit: 5`, `X-Month-RateLimit-Limit: 10000` | requests are spaced at 4 per second across all threads; 429s back off 0.5-2.5 s up to three tries; the monthly remainder is read from the headers, shown in `/api/health` and used to stop spending 100 requests before it runs out |
| Names arrive HTML-escaped (`Habits &amp; Break`) and tags have stray spaces (`" indie"`) | `_clean` |
| **Affinity does not discriminate**: across 3,800 returned tags the minimum is 0.969, the median 0.995, and 82% are at least 0.99 | the tag *set* carries the signal; the affinity is kept but barely matters |
| **A fifth of the tags are venue metadata**: "VISA", "Five Star", "Identifies as women-owned", "Scallop Risotto" come back for a film or a band | dropped by category (`tag_kind`): 775 of 3,800 (20%) |
| Insights tags are not always attributable to a favourite (a guest with Squid Game, a podcast and a brand got "Florida" and "Golf") | the favourites' own tags are blended in and the tags shown on cards must be carried by a favourite (below) |

The guest vector is therefore: taste-analysis tags (mean 40 after dropping noise) plus the favourites' own tags (mean 83
per favourite), 238-292 tags per guest.

## 1. Do the favourites resolve?

216 candidate favourites were tried first. With the first rule (take the most popular exact-name match across all
types), **40 of 216 (19%) resolved to the wrong thing** or to nothing: Hades to a band, Kyoto to a restaurant in Israel,
Lagaan to a venue in Sri Lanka, "Zero to One" to a cafe in Tainan, "Beyonce" not matching "Beyoncé". Changes:

- search only the nine culture types (not albums, actors, localities);
- exact name, then the name before a subtitle (`Lagaan` = `Lagaan: Once Upon a Time in India`), then the closest spelling
  (ratio 0.7, was 0.6); accents are ignored;
- a venue (`place`) is discounted to 0.6 of its popularity because businesses share names with films and bands;
- optional hints (`Hades (game)`), retried without the hint if the typed search finds nothing;
- an "assumed" note only for a genuinely ambiguous title: another work (film, show, book, game) with the same name and at
  least 90% of its popularity ("Oldboy: 2003 film, there is also a 2013 one").

Result: all **194 distinct favourites in the live presets resolve (194/194)**, and the wedding (133 favourites) and
conference (82) lists match **100%**. A cold run costs 3.6 requests per guest (wedding 143 requests, conference 89).

## 2. Does the vector separate people?

Ground truth: every preset guest is generated from a primary and a secondary taste "archetype" (K-culture, outdoors,
tech, foodie, classics, Malaysian, and so on). Metric: AUC, the probability that two guests who share a primary
archetype score higher than two who share none (0.5 = chance, 1.0 = perfect). "NN" is the share of guests whose single
most-similar guest shares an archetype. Mean of the 40-guest wedding and the 24-guest conference.

| Own-tag weight (taste analysis alone = 0) | mean AUC | mean NN |
|---|---|---|
| 0 | 0.744 | 0.89 |
| 0.5 | 0.771 | 0.91 |
| 1 | 0.794 | 0.92 |
| **2 (chosen)** | **0.823** | 0.86 |
| 3 | 0.825 | 0.91 |
| 4 | 0.812 | 0.89 |

**Change:** blend the favourites' own tags in at weight 2 (`OWN_TAG_WEIGHT`). It lifts AUC from 0.744 to 0.823; more than 3
starts to drown the taste-analysis tags (the conference AUC falls from 0.80 to 0.76 at 4). Mood tags (style, character,
audience: "Polished", "Resilient") count half and are never shown on cards: the AUC is flat across weights 0 to 0.5
(0.827, 0.827, 0.823; 0.814 at full weight), so 0.5 stays as a hedge. Cosine values are small (mean 0.19 for guests who
share an archetype, 0.05 for partial overlap, 0.03 for none, wedding), which is why the next change is needed.

**Change:** `seating.calibrate` rescales every pair by one factor so the party's 90th-percentile pair scores 0.6. The
objective adds a fit term to an isolation penalty measured in fractions of the party, and with raw cosines of 0.02-0.2 a
single isolated guest outweighs every fit gain. Calibration keeps pair order, leaves the tables the same on these parties
(purity 0.496 against 0.497 raw) and makes the numbers readable (average cohesion 0.46 instead of 0.09). A party whose 90th-percentile pair already scores 0.3 or more (the hand-built SAMPLE vectors do) is left
untouched, so keyless mode and its screenshots are unchanged. The factor is reported in the plan's `search.similarity_scale`.
Brute-force optimality tests run with calibration switched off (`calibrate_scores=False`) because they compare against raw
values.

## 3. Objective, close-match percentile, IDF

Sweep on the same profiles, 6 seeds each, with and without the organiser's constraints, both presets (144 settings).
Purity is the share of table-mates who share an archetype with a guest (random seating is about 0.2-0.25; the best
possible, found by seating on the true archetypes, is 0.55-0.71).

| Setting | purity | least-matched guest's fit vs random | avg cohesion | isolated guests |
|---|---|---|---|---|
| lambda 0.4 | 0.505 | 3.13x | 0.280 | 0.06 |
| **lambda 0.6 (kept)** | 0.497 | 3.32x | 0.274 | 0.06 |
| lambda 0.8 | 0.487 | 3.42x | 0.268 | 0.06 |
| close-match percentile 0.5 | 0.502 | 3.35x | 0.278 | 0.00 |
| **percentile 0.7 (kept)** | 0.500 | 3.29x | 0.275 | 0.00 |
| percentile 0.85 | 0.488 | 3.22x | 0.270 | 0.19 |
| IDF off | 0.502 | 3.05x | 0.284 | 0.08 |
| **IDF on (kept)** | 0.491 | 3.53x | 0.264 | 0.04 |

- **0.6 / 0.4 stays.** Moving the weight trades the worst guest against the table average by a few percent each way and
  the middle setting is within 0.01 purity of the best. No change.
- **70th percentile stays.** At 0.85 "close" becomes so rare that guests start to count as isolated (0.19 per run),
  which pushes the optimiser to break up good tables for nothing; 0.5 and 0.7 are equal.
- **IDF stays.** It does not change separation (AUC 0.845 against 0.848 wedding, 0.801 against 0.796 conference) but
  raises the least-matched guest's fit from 3.05x to 3.53x random, because generic tags ("Drama", "Love") stop making
  strangers look alike.
- With real vectors **nobody was isolated** in any default run: every guest has at least one close mate. The isolation
  guarantee is a safety net here, not a constraint that binds (it did bind in the SAMPLE data and the unit tests).

Headline numbers, default settings, seed 0, with the organiser's requirements:

| | wedding (40 guests, 5 tables of 8) | conference (24 guests, 4 tables of 6) |
|---|---|---|
| least-matched guest's fit | 0.25 vs 0.08 random (3.1x) | 0.22 vs 0.05 random (4.4x) |
| average cohesion | 0.46 vs 0.32 random | 0.42 vs 0.25 random |
| isolated | 0 | 0 |
| requirements met | 3 of 3 | 2 of 2 |
| AUC / NN | 0.845 / 0.90 | 0.801 / 0.83 |
| seating time | 0.25 s | 0.07 s |

## 4. Cards: only grounded tags

The first live mock run produced cards such as "Violence: Grandma Rose and Pearl Tan listed Casablanca" and
"Natural Fibers (they listed AC/DC and Mad Max)". The shared tag came from the taste-analysis spill-over, not from the
favourites named next to it. Changes:

- a table's *shown* shared tags must be of a topic category (genre, theme, keyword, cuisine, interest...) **and carried by
  at least two guests' own favourites** (`fav` marker); mood tags and inferred-only tags still count for similarity but are
  never shown;
- the favourites a card may cite for a tag are only the ones that carry it (`_topic_material`);
- spelling variants (`k pop`, `Kpop`, `K-Pop`) are shown once;
- the "does not mention another table's tag" check now polices the tags the plan shows on tables, not the whole
  vocabulary: live vectors hold thousands of tags, many of them everyday words ("love", "connection"), and the old
  all-vocabulary check made models fail cards for ordinary prose (12 rejections and 135k input tokens in one
  `gemini-3.5-flash` run). Any tag the table itself shares may be named in the text, not only the ones listed as topics.

## 5. Rate limits and the call budget

The key allows 5 requests per second and 10,000 per month. Profiles are built on 6 threads; the throttle spaces requests
at 4 per second across all of them, so a cold wedding takes 40 s and no 429 was seen in any run (the retry path is covered
by unit tests with recorded 429 bodies). The per-run cap `MAX_QLOO_CALLS` is now 600 (a 120-guest list needs about 430).
Because the month is the real limit (about 70 cold weddings), the defaults are `RUNS_PER_DAY=120`, `RUNS_PER_IP_PER_HOUR=8`,
results are cached for 24 hours per process (preset lists cost nothing after the first run) and `QLOO_CACHE_FILE` keeps the
cache across restarts where there is a disk.
