#!/usr/bin/env python3
"""Phase 5 ablations, round 3 (RANK1-REPLICATION): TWO-TIER sparse fallback.

Round 2 showed global damping 0.4 hurts dense cases, so the degraded
settings must apply ONLY to the sparse tier. This script prototypes a
two-tier forecaster:
  segments >= 8 : shipped rich (damping 1.0, mult 0.75, window 24h)
  6-7 segments  : degraded rich (damping 0.4, mult X, window W)
  < 6 segments  : starter fallback
and compares it against the shipped baseline (guard >= 8 -> starter)
on: refresh_slow seeds 1-25 (sparse-focused), held-out 6-10 (all
scenarios), fresh 11-15 (all scenarios). Reports the paired win rate
vs the starter on sparse cases and a per-segment-count breakdown.
"""
import json
import os
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


def _two_tier(case, sparse_mult, sparse_window):
    """Two-tier forecast: dense -> shipped rich; 6-7 segs -> degraded
    rich; <6 segs -> starter. Implemented by patching M's constants
    around a _rich_rows call (restored afterwards)."""
    segs = M.segments_of(case.get("history") or [])
    if len(segs) >= 8:
        return {"trophies": M.forecast_case(case)}
    if len(segs) < 6:
        return {"trophies": M.starter_rows(case)}
    # 6-7 segments: degraded rich.
    saved = (M.RICH_MIN_SEGMENTS, M.RICH_DAMPING, M.RICH_MULT_FIXED,
             M.RICH_WINDOW_HOURS)
    try:
        M.RICH_MIN_SEGMENTS = 2
        M.RICH_DAMPING = 0.4
        M.RICH_MULT_FIXED = sparse_mult
        M.RICH_WINDOW_HOURS = sparse_window
        rows = M._rich_rows(case)
    finally:
        (M.RICH_MIN_SEGMENTS, M.RICH_DAMPING, M.RICH_MULT_FIXED,
         M.RICH_WINDOW_HOURS) = saved
    if rows is None:
        return {"trophies": M.starter_rows(case)}
    return {"trophies": rows}


def _eval(cases, fn):
    rows = []
    for case, truth in cases:
        out = fn(case)
        s = r1_score.score(out, truth)
        st = r1_score.score({"trophies": M.starter_rows(case)}, truth)
        rows.append({"segments": r1_sim.n_segments(case),
                     "loss": s["loss"], "starter": st["loss"]})
    return rows


def _summ(name, rows):
    ml = sum(r["loss"] for r in rows) / len(rows)
    sparse = [r for r in rows if r["segments"] < 8]
    dense = [r for r in rows if r["segments"] >= 8]
    ms = sum(r["loss"] for r in sparse) / len(sparse) if sparse else float("nan")
    mss = sum(r["starter"] for r in sparse) / len(sparse) if sparse else float("nan")
    md = sum(r["loss"] for r in dense) / len(dense) if dense else float("nan")
    wins = sum(1 for r in sparse if r["loss"] < r["starter"])
    print("  %-28s overall %.4f  sparse<8: %.4f vs starter %.4f (win %d/%d)  dense>=8: %.4f"
          % (name, ml, ms, mss, wins, len(sparse), md), flush=True)
    return ms


def main():
    variants = [
        ("baseline (guard8->starter)", lambda c: {"trophies": M.forecast_case(c)}),
        ("two-tier mult0.75 win24h", lambda c: _two_tier(c, 0.75, 24.0)),
        ("two-tier mult1.0 win24h", lambda c: _two_tier(c, 1.0, 24.0)),
        ("two-tier mult1.5 win24h", lambda c: _two_tier(c, 1.5, 24.0)),
        ("two-tier mult1.0 winFULL", lambda c: _two_tier(c, 1.0, 10000.0)),
        ("two-tier mult0.75 winFULL", lambda c: _two_tier(c, 0.75, 10000.0)),
    ]
    # A. sparse-focused: refresh_slow seeds 1-25
    print("== A. refresh_slow seeds 1-25 (sparse-focused) ==", flush=True)
    cases = [r1_sim.gen_case(s, "refresh_slow", history_days=HIST_DAYS)
             for s in range(1, 26)]
    for name, fn in variants:
        _summ(name, _eval(cases, fn))
    # B. held-out 6-10 + fresh 11-15, all scenarios
    for tag, seeds in (("held-out 6-10", range(6, 11)), ("fresh 11-15", range(11, 16))):
        print("== B. %s, all scenarios ==" % tag, flush=True)
        cases = []
        for sc in SCENARIOS:
            for s in seeds:
                cases.append(r1_sim.gen_case(s, sc, history_days=HIST_DAYS))
        for name, fn in variants:
            _summ(name, _eval(cases, fn))
    # C. per-segment-count breakdown for the best two-tier variant
    print("== C. per-segment-count breakdown (two-tier mult1.0 win24h) ==", flush=True)
    cases = [r1_sim.gen_case(s, "refresh_slow", history_days=HIST_DAYS)
             for s in range(1, 26)]
    by_seg = {}
    for case, truth in cases:
        n = r1_sim.n_segments(case)
        out = _two_tier(case, 1.0, 24.0)
        s = r1_score.score(out, truth)
        st = r1_score.score({"trophies": M.starter_rows(case)}, truth)
        by_seg.setdefault(n, []).append((s["loss"], st["loss"]))
    for n in sorted(by_seg):
        rs = by_seg[n]
        print("  segs=%2d n=%2d  two-tier %.4f  starter %.4f  diff %+.4f"
              % (n, len(rs), sum(x[0] for x in rs) / len(rs),
                 sum(x[1] for x in rs) / len(rs),
                 sum(x[0] - x[1] for x in rs) / len(rs)), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
