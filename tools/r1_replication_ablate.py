#!/usr/bin/env python3
"""Phase 5 ablations (RANK1-REPLICATION) on the hazard simulator.

Tests NEW ideas (not the builder's already-settled knobs) by patching
rank1-fresh main.py constants in-process. Selection on HELD-OUT seeds
6-10 only; winners are confirmed on fresh seeds 11-15 separately.

Variants (baseline = shipped: damping 1.0, mult 0.75, guard >=8,
window 24h, Theil-Sen):
  guard2-d1.0-m0.75 / guard2-d0.4-m0.75 / guard2-d0.4-m1.0 /
  guard2-d1.0-m1.0 / guard2-d0.4-m1.5 : degraded-rich for sparse histories
  guard4-d0.4 / guard6-d0.4           : intermediate guards
  mult0.60 / mult0.65                 : narrower spread (coverage 0.96-1.00
                                        suggests the shipped band is wide)
  window-full                         : fit on ALL segments, not last 24h
  ols                                 : OLS slope instead of Theil-Sen
  damping0.85                         : intermediate damping
History 2.486d = real f00 span. Scorer = r1_score (RULES.txt).
"""
import json
import math
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, REPO)

import r1_score
import r1_sim
import main as M

HIST_DAYS = 2.486
SCENARIOS = ["base", "refresh_fast", "refresh_slow", "growth_fast",
             "growth_slow", "noise", "jump"]


def _ols(xs, ys):
    """Ordinary least squares slope + intercept (for the OLS variant)."""
    n = len(xs)
    if n < 2 or not ys:
        return 0.0, (statistics.median(ys) if ys else 0.0)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


BASE = {"RICH_DAMPING": 1.0, "RICH_PRIOR_WEIGHT": 10.0,
        "RICH_MULT_FIXED": 0.75, "RICH_MIN_SEGMENTS": 8,
        "RICH_WINDOW_HOURS": 24.0}
VARIANTS = [
    ("baseline", {}),
    ("guard2-d1.0-m0.75", {"RICH_MIN_SEGMENTS": 2}),
    ("guard2-d0.4-m0.75", {"RICH_MIN_SEGMENTS": 2, "RICH_DAMPING": 0.4}),
    ("guard2-d0.4-m1.0", {"RICH_MIN_SEGMENTS": 2, "RICH_DAMPING": 0.4,
                          "RICH_MULT_FIXED": 1.0}),
    ("guard2-d1.0-m1.0", {"RICH_MIN_SEGMENTS": 2, "RICH_MULT_FIXED": 1.0}),
    ("guard2-d0.4-m1.5", {"RICH_MIN_SEGMENTS": 2, "RICH_DAMPING": 0.4,
                          "RICH_MULT_FIXED": 1.5}),
    ("guard4-d0.4", {"RICH_MIN_SEGMENTS": 4, "RICH_DAMPING": 0.4}),
    ("guard6-d0.4", {"RICH_MIN_SEGMENTS": 6, "RICH_DAMPING": 0.4}),
    ("mult0.60", {"RICH_MULT_FIXED": 0.60}),
    ("mult0.65", {"RICH_MULT_FIXED": 0.65}),
    ("window-full", {"RICH_WINDOW_HOURS": 10000.0}),
    ("damping0.85", {"RICH_DAMPING": 0.85}),
]


def _apply(patch, ols=False):
    for k, v in BASE.items():
        setattr(M, k, v)
    for k, v in patch.items():
        setattr(M, k, v)
    M.theil_sen = _ols if ols else _orig_ts


_orig_ts = M.theil_sen


def _eval(seeds, patch, ols=False):
    _apply(patch, ols)
    rows = []
    for sc in SCENARIOS:
        for seed in seeds:
            case, truth = r1_sim.gen_case(seed, sc, history_days=HIST_DAYS)
            out = M.forecast_case(case)
            s = r1_score.score({"trophies": out}, truth)
            st = r1_score.score({"trophies": M.starter_rows(case)}, truth)
            rows.append({"scenario": sc, "seed": seed,
                         "segments": r1_sim.n_segments(case),
                         "loss": s["loss"], "starter": st["loss"],
                         "cov90": s["coverage90"]})
    return rows


def _report(name, rows):
    ml = sum(r["loss"] for r in rows) / len(rows)
    sl = sum(r["starter"] for r in rows) / len(rows)
    sparse = [r for r in rows if r["segments"] < 8]
    dense = [r for r in rows if r["segments"] >= 8]
    ms = (sum(r["loss"] for r in sparse) / len(sparse)) if sparse else float("nan")
    mss = (sum(r["starter"] for r in sparse) / len(sparse)) if sparse else float("nan")
    md = (sum(r["loss"] for r in dense) / len(dense)) if dense else float("nan")
    cov = sum(r["cov90"] for r in rows) / len(rows)
    print("  %-18s overall %.4f (starter %.4f)  sparse<8: %.4f vs starter %.4f (n=%d)  dense>=8: %.4f (n=%d)  cov90 %.2f"
          % (name, ml, sl, ms, mss, len(sparse), md, len(dense), cov), flush=True)
    return {"name": name, "overall": ml, "sparse": ms, "sparse_starter": mss,
            "dense": md, "cov90": cov, "n_sparse": len(sparse)}


def main():
    print("== Phase 5 ablations, HELD-OUT seeds 6-10, history %.3fd ==" % HIST_DAYS, flush=True)
    results = []
    for name, patch in VARIANTS:
        rows = _eval(range(6, 11), patch)
        results.append(_report(name, rows))
    # OLS variant (needs the theil_sen replacement, not a constant patch)
    rows = _eval(range(6, 11), {}, ols=True)
    results.append(_report("ols", rows))
    _apply({})
    with open(os.path.join(REPO, "tools/r1_replication_ablate_results.json"), "w") as f:
        json.dump(results, f, indent=1)
    print("results -> tools/r1_replication_ablate_results.json", flush=True)


if __name__ == "__main__":
    main()
