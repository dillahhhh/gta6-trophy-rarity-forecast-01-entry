#!/usr/bin/env python3
"""Phase 5 ablations, round 2 (RANK1-REPLICATION).

A. Mult fine grid + OLS combination, held-out seeds 6-10 (selection)
   AND fresh seeds 11-15 (confirmation) -- a variant is adopted only if
   it wins on BOTH.
B. Sparse-focused: refresh_slow scenario, seeds 1-25 (many <8-segment
   histories), to answer with a bigger sample whether ANY degraded-rich
   variant beats the starter fallback on sparse histories.
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
_orig_ts = M.theil_sen


def _apply(patch, ols=False):
    for k, v in BASE.items():
        setattr(M, k, v)
    for k, v in patch.items():
        setattr(M, k, v)
    M.theil_sen = _ols if ols else _orig_ts


def _eval(seeds, scenarios, patch, ols=False):
    _apply(patch, ols)
    rows = []
    for sc in scenarios:
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


def _mean(rows):
    return sum(r["loss"] for r in rows) / len(rows) if rows else float("nan")


def main():
    print("== A. mult/OLS grid: held-out 6-10 vs fresh 11-15 ==", flush=True)
    variants = [
        ("baseline", {}, False),
        ("mult0.50", {"RICH_MULT_FIXED": 0.50}, False),
        ("mult0.55", {"RICH_MULT_FIXED": 0.55}, False),
        ("mult0.60", {"RICH_MULT_FIXED": 0.60}, False),
        ("mult0.65", {"RICH_MULT_FIXED": 0.65}, False),
        ("ols", {}, True),
        ("ols+mult0.60", {"RICH_MULT_FIXED": 0.60}, True),
        ("ols+mult0.55", {"RICH_MULT_FIXED": 0.55}, True),
    ]
    for name, patch, ols in variants:
        h = _eval(range(6, 11), SCENARIOS, patch, ols)
        f = _eval(range(11, 16), SCENARIOS, patch, ols)
        print("  %-14s held-out %.4f (cov %.2f)  fresh %.4f (cov %.2f)"
              % (name, _mean(h), sum(r["cov90"] for r in h) / len(h),
                 _mean(f), sum(r["cov90"] for r in f) / len(f)), flush=True)
    print("== B. sparse-focused: refresh_slow, seeds 1-25 ==", flush=True)
    sparse_variants = [
        ("baseline(guard8->starter)", {}, False),
        ("guard2-d0.4-m1.5", {"RICH_MIN_SEGMENTS": 2, "RICH_DAMPING": 0.4,
                              "RICH_MULT_FIXED": 1.5}, False),
        ("guard6-d0.4", {"RICH_MIN_SEGMENTS": 6, "RICH_DAMPING": 0.4}, False),
        ("guard2-d1.0-m0.75", {"RICH_MIN_SEGMENTS": 2}, False),
        ("guard4-d0.4-m1.0", {"RICH_MIN_SEGMENTS": 4, "RICH_DAMPING": 0.4,
                              "RICH_MULT_FIXED": 1.0}, False),
    ]
    for name, patch, ols in sparse_variants:
        rows = _eval(range(1, 26), ["refresh_slow"], patch, ols)
        sparse = [r for r in rows if r["segments"] < 8]
        dense = [r for r in rows if r["segments"] >= 8]
        print("  %-26s ALL %.4f (n=%d)  sparse<8: %.4f vs starter %.4f (n=%d)  dense>=8: %.4f (n=%d)"
              % (name, _mean(rows), len(rows), _mean(sparse),
                 sum(r["starter"] for r in sparse) / len(sparse) if sparse else float("nan"),
                 len(sparse), _mean(dense), len(dense)), flush=True)
    _apply({})
    print("done", flush=True)


if __name__ == "__main__":
    main()
