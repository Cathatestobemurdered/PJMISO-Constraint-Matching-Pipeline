#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assignment 1: Constraint Mapping Across Data Sources
====================================================
Author: Yingyun Zhan

Map PJM/MISO transmission constraints across three vendor data sources
(Market, Dayzer, Panorama/Pano) so that entries describing the *same*
physical (facility, contingency) constraint can be linked together.

This single module is the one source of truth: the Jupyter notebook
imports from it so the script and the notebook can never drift apart.

Pipeline (ETL + fuzzy entity resolution)
----------------------------------------
1. Extract   : read the three raw CSVs.
2. Transform : map each vendor schema onto a common
               (source, id, raw_name, facility_raw, contingency_raw) shape,
               then normalize text and extract voltage levels.
3. Match     : for every Market constraint, find the best Dayzer and Pano
               candidate using a token-blocked fuzzy comparison.
4. Load      : export a CSV with the three required columns plus audit fields.

Key design choices (see summary_report.md for the rationale)
------------------------------------------------------------
* Candidate generation uses an INVERTED INDEX on discriminative facility
  tokens (length >= 2, excluding bare voltages / "KV"). This replaces the
  brute-force O(N*M) nested loop and cuts ~30M Market-Dayzer comparisons
  down to ~0.27M, while *not* hard-blocking on voltage so that transformer
  constraints that legitimately span two voltages are still compared.
* Voltage is used only as a soft tie-breaker, never as a hard filter.
* Base-state contingencies ("ACTUAL", "BASE", blank) are canonicalised so
  that a 100%-facility match is not dragged down by ACTUAL-vs-BASE wording.
* The matcher prefers rapidfuzz when installed, but falls back to a pure
  Python implementation that is mathematically identical to
  rapidfuzz.fuzz.token_set_ratio (Indel / LCS based), so the pipeline runs
  in any environment and produces the same scores.

The output is a CANDIDATE mapping table, not guaranteed ground truth:
fuzzy matching always returns a nearest neighbour, so weak matches are
labelled "review" / "unmatched" rather than silently trusted.
"""

import argparse
import re
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# 0. Scoring backend: rapidfuzz if available, else exact fallback
# ============================================================

try:
    from rapidfuzz import fuzz as _rf_fuzz

    def _token_set_ratio(a: str, b: str) -> float:
        return _rf_fuzz.token_set_ratio(a, b)

    SCORER_BACKEND = "rapidfuzz"

except ImportError:
    # Pure-Python fallback. fuzz.ratio in rapidfuzz is the normalised Indel
    # similarity, which for two strings equals 200 * LCS / (len_a + len_b).
    # token_set_ratio is then defined on top of ratio exactly as below, so
    # this reproduces rapidfuzz scores without the dependency.
    def _lcs_ratio(a: str, b: str) -> float:
        la, lb = len(a), len(b)
        if la == 0 and lb == 0:
            return 100.0
        if la == 0 or lb == 0:
            return 0.0
        if lb > la:                      # keep inner loop on the shorter string
            a, b, la, lb = b, a, lb, la
        prev = [0] * (lb + 1)
        for i in range(1, la + 1):
            ca = a[i - 1]
            cur = [0] * (lb + 1)
            for j in range(1, lb + 1):
                if ca == b[j - 1]:
                    cur[j] = prev[j - 1] + 1
                else:
                    cur[j] = prev[j] if prev[j] >= cur[j - 1] else cur[j - 1]
            prev = cur
        return 200.0 * prev[lb] / (la + lb)

    def _token_set_ratio(a: str, b: str) -> float:
        t1, t2 = set(a.split()), set(b.split())
        inter = sorted(t1 & t2)
        sect = " ".join(inter)
        c1 = (sect + " " + " ".join(sorted(t1 - t2))).strip()
        c2 = (sect + " " + " ".join(sorted(t2 - t1))).strip()
        return max(_lcs_ratio(sect, c1), _lcs_ratio(sect, c2), _lcs_ratio(c1, c2))

    SCORER_BACKEND = "python-fallback"


@lru_cache(maxsize=None)
def token_set_ratio(a: str, b: str) -> float:
    """Cached, order-insensitive token_set_ratio (the score is symmetric)."""
    if a > b:                            # canonical key -> better cache hit rate
        a, b = b, a
    return _token_set_ratio(a, b)


# ============================================================
# 1. Text cleaning helpers
# ============================================================

VOLTAGE_LEVELS = ("69", "115", "138", "161", "230", "345", "500", "765")
_VOLT_ALT = "|".join(VOLTAGE_LEVELS)
_VOLT_BOUNDARY = re.compile(rf"\b({_VOLT_ALT})\b")

GENERIC_WORDS = {
    "LINE", "XFORMER", "TRANSFORMER", "CONTINGENCY",
    "MONITOR", "MONITORED", "FACILITY", "PJM", "MISO", "LO", "FLO",
}

# Contingency wording that all means "base case / actual topology / no outage".
BASE_STATE_TOKENS = {"", "ACTUAL", "BASE", "BASECASE", "NONE", "NORMAL"}


def normalize_text(x) -> str:
    """
    Normalise facility / contingency text so vendor naming conventions become
    comparable. The business meaning is preserved; only formatting changes.

    Example
    -------
    "L500.Conastone-Peachbottom.5012" -> "L 500 CONASTONE PEACHBOTTOM 5012"
    """
    if pd.isna(x):
        return ""
    x = str(x).upper()
    # Detach a unit/bus digit glued to a voltage:  TRENTON2138KV -> TRENTON 2 138 KV
    x = re.sub(rf"([0-9])({_VOLT_ALT})\s*KV", r" \1 \2 KV ", x)
    # Space out the voltage in NNNKV / NNN KV forms only (keeps real numbers intact)
    x = re.sub(r"([0-9]+)\s*KV", r" \1 KV ", x)
    x = x.replace("KV", " KV ")
    # NOTE: we intentionally do NOT space-split bare voltage numbers globally,
    # because that corrupts circuit IDs (e.g. "2303" -> "230 3").
    x = re.sub(r"[^A-Z0-9]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()


def remove_generic_words(x) -> str:
    """Drop generic descriptors that add noise instead of identifying the asset."""
    if pd.isna(x):
        return ""
    return " ".join(t for t in str(x).split() if t not in GENERIC_WORDS)


def clean_text(x) -> str:
    """normalize_text followed by remove_generic_words."""
    return remove_generic_words(normalize_text(x))


def canonical_contingency(clean_contingency: str) -> str:
    """Collapse all base-state wordings to a single 'BASE' token."""
    toks = [t for t in clean_contingency.split() if t not in BASE_STATE_TOKENS]
    return " ".join(toks) if toks else "BASE"


def extract_voltage(clean_facility: str) -> str:
    """Primary voltage of a facility (first recognised level), else UNKNOWN."""
    m = _VOLT_BOUNDARY.search(clean_facility)
    return m.group(1) if m else "UNKNOWN"


def voltage_set(clean_facility: str) -> frozenset:
    """All voltage levels present (transformers carry two)."""
    return frozenset(_VOLT_BOUNDARY.findall(clean_facility))


def confidence_label(score) -> str:
    if score >= 90:
        return "high"
    if score >= 80:
        return "medium"
    if score >= 70:
        return "low"
    return "review"


# ============================================================
# 2. Extract + standardise schemas
# ============================================================

def standardize_market(market: pd.DataFrame) -> pd.DataFrame:
    need = ["CONSTRAINT", "CONTINGENCY", "CONSTRAINTID", "CONTINGENCYID"]
    _require(market, need, "Market")
    out = pd.DataFrame()
    out["source"] = ["market"] * len(market)
    out["id"] = market["CONSTRAINTID"].astype(str) + "_" + market["CONTINGENCYID"].astype(str)
    out["facility_raw"] = market["CONSTRAINT"]
    out["contingency_raw"] = market["CONTINGENCY"]
    out["raw_name"] = out["facility_raw"].astype(str) + " : " + out["contingency_raw"].astype(str)
    return out


def standardize_pano(pano: pd.DataFrame, active_since: str | None = None) -> pd.DataFrame:
    """
    Keep ALL Pano rows by default. ~96% of Pano constraints are historical
    (Latest going back to 2010), but a physical constraint is identified by
    its name regardless of whether it is currently active, so filtering them
    out would silently drop the correct match for older constraints. Instead
    we carry the Latest date through and flag stale matches in the output.
    Pass active_since to hard-filter if a live-only view is wanted.
    """
    need = ["Monitored Facility", "Contingency Name", "PID"]
    _require(pano, need, "Pano")
    pano = pano.copy()
    latest = pd.to_datetime(pano.get("Latest"), errors="coerce", utc=True)
    if active_since:
        keep = latest >= pd.Timestamp(active_since, tz="UTC")
        print(f"  Pano active filter (Latest >= {active_since}): "
              f"keeping {int(keep.sum()):,} / {len(pano):,} rows")
        pano, latest = pano[keep].reset_index(drop=True), latest[keep].reset_index(drop=True)
    out = pd.DataFrame()
    out["source"] = ["pano"] * len(pano)
    out["id"] = pano["PID"].astype(str)
    out["facility_raw"] = pano["Monitored Facility"]
    out["contingency_raw"] = pano["Contingency Name"]
    out["raw_name"] = out["facility_raw"].astype(str) + " : " + out["contingency_raw"].astype(str)
    out["latest"] = latest.values
    return out


def standardize_dayzer(dayzer: pd.DataFrame) -> pd.DataFrame:
    need = ["CID", "NAME"]
    _require(dayzer, need, "Dayzer")
    out = pd.DataFrame()
    out["source"] = ["dayzer"] * len(dayzer)
    out["id"] = dayzer["CID"].astype(str)
    out["raw_name"] = dayzer["NAME"]
    parts = dayzer["NAME"].astype(str).str.split(":", n=1)
    out["facility_raw"] = parts.str[0]
    out["contingency_raw"] = parts.str[1].fillna("")
    return out


def _require(df, cols, name):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name} file is missing required columns: {missing}")


def add_clean_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Add facility_clean, contingency_clean, voltage, voltage_set."""
    df = df.copy()
    df["facility_clean"] = df["facility_raw"].map(clean_text)
    df["contingency_clean"] = df["contingency_raw"].map(clean_text).map(canonical_contingency)
    df["voltage"] = df["facility_clean"].map(extract_voltage)
    df["voltage_set"] = df["facility_clean"].map(voltage_set)
    return df


# ============================================================
# 3. Matching: inverted-index blocking + fuzzy scoring
# ============================================================

def _block_tokens(facility_clean: str) -> set:
    """Discriminative tokens used for candidate generation."""
    return {t for t in facility_clean.split()
            if len(t) >= 2 and t not in VOLTAGE_LEVELS and t != "KV"}


def run_matching(source_df, candidate_df, candidate_name,
                 facility_weight=0.65, contingency_weight=0.35):
    """
    For each source row, return the best candidate row.

    final_score = 0.65 * facility_score + 0.35 * contingency_score
    (facility is weighted higher because the monitored element is the more
    stable part of a constraint definition). Voltage agreement is used only
    to break near-ties, never to exclude candidates.
    """
    print(f"Matching Market -> {candidate_name} ...")

    cand = candidate_df.reset_index(drop=True)
    c_id = cand["id"].tolist()
    c_name = cand["raw_name"].tolist()
    c_fac = cand["facility_clean"].tolist()
    c_cont = cand["contingency_clean"].tolist()
    c_volt = cand["voltage_set"].tolist()

    # Inverted index: token -> list of candidate row positions.
    index = defaultdict(list)
    for pos, fac in enumerate(c_fac):
        for tok in _block_tokens(fac):
            index[tok].append(pos)
    all_positions = range(len(cand))

    rows = []
    n = len(source_df)
    for i, (_, r) in enumerate(source_df.iterrows(), start=1):
        if i % 1000 == 0:
            print(f"  {i:,}/{n:,} rows")

        s_fac, s_cont, s_volt = r["facility_clean"], r["contingency_clean"], r["voltage_set"]

        # Candidate shortlist via shared discriminative tokens.
        shortlist = set()
        for tok in _block_tokens(s_fac):
            shortlist.update(index.get(tok, ()))
        if not shortlist:                # no token overlap -> scan everything
            shortlist = all_positions

        best = None  # (final_score, volt_match, fac_score, cont_score, pos)
        for pos in shortlist:
            fac_s = token_set_ratio(s_fac, c_fac[pos])
            cont_s = token_set_ratio(s_cont, c_cont[pos])
            final = facility_weight * fac_s + contingency_weight * cont_s
            volt_match = 1 if (s_volt & c_volt[pos]) else 0
            key = (final, volt_match, fac_s, cont_s, pos)
            if best is None or key > best:
                best = key

        if best is None:
            rows.append((None, None, -1.0, -1.0, -1.0))
        else:
            final, _, fac_s, cont_s, pos = best
            rows.append((c_id[pos], c_name[pos], fac_s, cont_s, final))

    return pd.DataFrame(
        rows,
        columns=["matched_id", "matched_raw_name",
                 "facility_score", "contingency_score", "final_score"],
    )


# ============================================================
# 4. Assemble the result table
# ============================================================

def build_result_table(market_std, dayzer_matches, pano_matches, pano_std,
                       review_threshold=70, stale_before="2026-01-01"):
    """
    Required columns first (market_constraint, dayzer_constraint,
    pano_constraint), then scores, confidence and an explicit match_status
    so reviewers can see the nearest neighbour AND whether to trust it.
    Pano matches also carry the constraint's Latest date and a stale flag.
    """
    def status(score):
        return "matched" if score >= review_threshold else (
            "review" if score >= 50 else "unmatched")

    res = pd.DataFrame()
    # --- the three columns the assignment asks for ---
    res["market_constraint"] = market_std["raw_name"]
    res["dayzer_constraint"] = dayzer_matches["matched_raw_name"]
    res["pano_constraint"] = pano_matches["matched_raw_name"]

    # --- Dayzer audit fields ---
    res["dayzer_score"] = dayzer_matches["final_score"].round(2)
    res["dayzer_confidence"] = res["dayzer_score"].map(confidence_label)
    res["dayzer_status"] = res["dayzer_score"].map(status)
    res["dayzer_id"] = dayzer_matches["matched_id"]

    # --- Pano audit fields (+ recency) ---
    res["pano_score"] = pano_matches["final_score"].round(2)
    res["pano_confidence"] = res["pano_score"].map(confidence_label)
    res["pano_status"] = res["pano_score"].map(status)
    res["pano_id"] = pano_matches["matched_id"]

    latest_by_id = dict(zip(pano_std["id"], pano_std["latest"]))
    pano_latest = pano_matches["matched_id"].map(latest_by_id)
    res["pano_latest"] = pd.to_datetime(pano_latest, utc=True).dt.date.astype("string")
    cutoff = pd.Timestamp(stale_before, tz="UTC")
    res["pano_stale"] = pd.to_datetime(pano_latest, utc=True) < cutoff

    # --- overall (weaker of the two, so we never over-claim a triple) ---
    res["overall_score"] = res[["dayzer_score", "pano_score"]].min(axis=1).round(2)
    res["overall_confidence"] = res["overall_score"].map(confidence_label)

    # --- anchor id last, for traceability ---
    res["market_id"] = market_std["id"]
    return res


# ============================================================
# 5. Orchestration
# ============================================================

def run_pipeline(market_path, dayzer_path, pano_path, output_path,
                 review_threshold=70, pano_active_since=None,
                 pano_stale_before="2026-01-01"):
    t0 = time.time()
    print(f"Scoring backend: {SCORER_BACKEND}")

    print("Loading datasets ...")
    market = pd.read_csv(market_path)
    dayzer = pd.read_csv(dayzer_path)
    pano = pd.read_csv(pano_path)
    print(f"  Market {len(market):,} | Dayzer {len(dayzer):,} | Pano {len(pano):,}")

    print("Standardising schemas ...")
    market_std = add_clean_fields(standardize_market(market))
    dayzer_std = add_clean_fields(standardize_dayzer(dayzer))
    pano_std = add_clean_fields(standardize_pano(pano, pano_active_since))

    for name, df in [("Market", market_std), ("Dayzer", dayzer_std), ("Pano", pano_std)]:
        unk = int((df["voltage"] == "UNKNOWN").sum())
        print(f"  {name} UNKNOWN voltage: {unk:,} / {len(df):,}")

    tm = time.time()
    dayzer_matches = run_matching(market_std, dayzer_std, "Dayzer")
    pano_matches = run_matching(market_std, pano_std, "Pano")
    print(f"Matching done in {time.time() - tm:.1f}s")

    result = build_result_table(market_std, dayzer_matches, pano_matches, pano_std,
                                review_threshold=review_threshold,
                                stale_before=pano_stale_before)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    print(f"Exported {len(result):,} rows -> {output_path}")
    print(f"Total runtime: {time.time() - t0:.1f}s")
    return result


def parse_args():
    p = argparse.ArgumentParser(description="Assignment 1: constraint mapping.")
    p.add_argument("--market", default="Market_PJMISO_constraint_list.csv")
    p.add_argument("--dayzer", default="Dayzer_PJMISO_constraint_list.csv")
    p.add_argument("--pano", default="Pano_PJMISO_constraint_list.csv")
    p.add_argument("--output", default="constraint_mapping_results.csv")
    p.add_argument("--threshold", type=float, default=70)
    p.add_argument("--pano-active-since", default=None,
                   help="Optional hard filter: keep only Pano rows with Latest >= this date.")
    p.add_argument("--pano-stale-before", default="2026-01-01",
                   help="Flag (do not drop) Pano matches whose Latest is before this date.")
    return p.parse_args()


if __name__ == "__main__":
    a = parse_args()
    run_pipeline(a.market, a.dayzer, a.pano, a.output,
                 review_threshold=a.threshold,
                 pano_active_since=a.pano_active_since,
                 pano_stale_before=a.pano_stale_before)
