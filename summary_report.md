# Assignment 1 — Constraint Mapping Across Data Sources
### Summary Report

**Author:** Yingyun Zhan

---

## 1. Goal

Three vendors describe the same PJM/MISO grid in three different "dialects." A constraint
is physically defined by a `(facility, contingency)` pair, but Market, Dayzer and Panorama
each name, format and split that pair differently. The task is to build a translation layer
that links entries pointing at the **same physical constraint**, with honest handling of the
ambiguous cases.

Using **Market as the anchor** (5,230 constraints), the pipeline finds the best Dayzer
(13,813 candidates) and Pano (21,963 candidates) match for each Market row and exports
`constraint_mapping_results.csv`.

---

## 2. Approach

**ETL.** Each source is mapped onto a common schema
`(source, id, raw_name, facility_raw, contingency_raw)`. Dayzer packs both halves into one
`NAME` field, split on the first colon; Market and Pano already separate them.

**Normalisation.** Uppercase; standardise voltage notation (`138KV → 138 KV`); detach unit
digits glued to a voltage (`TRENTON2138KV → TRENTON 2 138 KV`); strip punctuation; remove
generic descriptors (`LINE`, `TRANSFORMER`, `MONITORED`, …). The aim is to make formats
comparable without changing meaning.

**Matching.** Facility and contingency similarity are scored separately with
`token_set_ratio` and combined as

```
final = 0.65 · facility_score + 0.35 · contingency_score
```

The facility carries more weight because the monitored element is the more stable part of a
constraint definition.

---

## 3. Three domain-informed design decisions

These are what separate a naive fuzzy match from a usable one.

**(a) Token-blocked candidate generation — speed *and* recall.**
A brute-force Market × Dayzer comparison is ~30 million pairs. Instead, an inverted index
maps each *discriminative* facility token (length ≥ 2, excluding bare voltages and `KV`) to
the candidates containing it, and only those candidates are scored. This cuts Market–Dayzer
to ~0.27M pairs (~100×) and Market–Pano to ~0.47M, while also improving quality by never
scoring strings with nothing in common. Crucially, blocking is on tokens, **not on an exact
voltage match**, so transformer constraints that legitimately span two voltages (e.g.
`230/66`) are still compared. Voltage is used only as a soft tie-breaker.

**(b) Base-state contingency canonicalisation.**
1,268 Market rows (~24%) have contingency `ACTUAL`; Dayzer also uses `ACTUAL`, while Pano
uses `BASE`, and some rows are blank. These all mean *no outage / actual topology*. They are
collapsed to one `BASE` token, so a perfect facility match is no longer dragged down by
`ACTUAL`-vs-`BASE` wording. Example: `EASTON 69 KV EAS-EMU : ACTUAL` jumped from a "low" 72
to a "high" match against Pano's `EASTON 69KV - EMUNI 69KV … : BASE`.

**(c) Keep all Pano, but flag staleness.**
~96% of Pano's 21,963 constraints are historical (their `Latest` date runs back to 2010).
Hard-filtering to live constraints looked tempting but **silently dropped the correct match
for older constraints** — e.g. `SAYRECON 230 KV SAY-SAY` lost its true `SAYREVIL-SAYRECON`
Pano twin and mis-matched to an unrelated station. Because a physical constraint is defined
by its name regardless of whether it is currently binding, the pipeline searches the full
Pano list and instead carries each match's `Latest` date plus a `pano_stale` flag into the
output. This keeps recall high while making recency explicit for any trading use.

---

## 4. Results

Confidence bands: `high ≥ 90`, `medium ≥ 80`, `low ≥ 70`, else `review`.

| Source | high | medium | low | review | ≥70 | ≥90 | median |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dayzer | 3,493 | 1,276 | 238 | 223 | **95.7%** | 66.8% | 100 |
| Pano   | 4,581 |   483 | 126 |  40 | **99.2%** | 87.6% | 100 |

- **3,380 of 5,230 Market constraints (65%)** resolve to a confident three-way match
  (overall `high`, i.e. *both* Dayzer and Pano strong).
- Only **236 rows (4.5%)** are overall `review` — genuinely ambiguous or partial matches.
- Distinct candidates used: 4,124 Dayzer ids and 4,595 Pano ids, i.e. matching is close to
  one-to-one rather than collapsing onto a few popular targets.
- Among matched Pano rows, **~87% point to a historical (stale) constraint**, confirming
  that Pano's live overlap with the current Market set is small — exactly why the stale flag
  is needed.

**Reading of the two sources.** Dayzer is the closest dialect to Market: once its single
`NAME` field is split, facility/voltage/contingency line up almost field-for-field. Pano
scores even higher on raw similarity because it embeds a parenthetical that often restates
the facility in Market's own style — but most of those hits are historical.

---

## 5. Output schema

Required columns first, then audit fields:

`market_constraint`, `dayzer_constraint`, `pano_constraint`,
`dayzer_score`, `dayzer_confidence`, `dayzer_status`, `dayzer_id`,
`pano_score`, `pano_confidence`, `pano_status`, `pano_id`, `pano_latest`, `pano_stale`,
`overall_score`, `overall_confidence`, `market_id`.

`*_status` is `matched` (≥70), `review` (50–70) or `unmatched` (<50). The nearest-neighbour
name is **always kept** so a reviewer can see what was proposed and judge it, rather than
having weak cells blanked out.

---

## 6. Limitations & honest caveats

- **Candidate map, not ground truth.** Fuzzy matching always returns a nearest neighbour.
  The confidence/status columns exist so `review`/`unmatched` rows are treated as unconfirmed.
- **One-directional.** Market is the anchor; a Market row can in principle share a best
  candidate with another Market row. Dedup / two-sided reconciliation would be the next step.
- **Weak contingency signal.** Where the contingency is base-state on both sides, matches
  rest almost entirely on the facility — fine for base-case constraints, but it means the
  contingency weight does little work for that ~24% of rows.
- **Dayzer rows without a colon (~4.6%)** have no contingency and lean entirely on facility.

---

## 7. Reproducing

```bash
python pipeline.py            # writes constraint_mapping_results.csv (~1 min)
```

`pipeline.py` uses `rapidfuzz` if installed (seconds) and otherwise falls back to a
pure-Python scorer that reproduces the same `token_set_ratio` values, so results are
identical in either environment. The notebook `Assignment1_Constraint_Mapping.ipynb` imports
the same module, so script and notebook cannot diverge.
