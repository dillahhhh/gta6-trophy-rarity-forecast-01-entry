#!/usr/bin/env python3
"""Follow-up experiments for the fresh build (after the first full run).

1. Damping on HELD-OUT seeds, correctly (the first run's held-out
   comparison had a bug: both arms silently used the defaults).
2. A sparse-history guard: fall back to the starter when the last-24h
   window has fewer than N segments (the rich path lost to the starter
   on refresh_slow, ~6 segments). Tests N in {4, 6, 8}.
3. The combination (best damping + best guard).

Prints mean loss per scenario for each variant, on train seeds 1..5 and
held-out seeds 6..10. Used to decide the shipped constants/guard in
main.py. Results are recorded in FRESH-BUILD-REPORT.md.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r1_score
import r1_sim
import main as M

SCENARIOS = ["base", "refresh_fast", "refresh_slow",
             "growth_fast", "growth_slow", "noise", "jump"]


def window_segments(case):
    segs = M.segments_of(case["history"])
    ckpt = M.parse_ts(case["at"])
    win = [s for s in segs if s["at"] >= ckpt - timedelta(hours=24.0)]
    return len(win), len(segs)


def forecast_guarded(case, min_win_segments):
    """Rich path, but fall back to the starter when the last-24h window
    has fewer than min_win_segments (too thin to trust a trend)."""
    n_win, _ = window_segments(case)
    if n_win < min_win_segments:
        return {"trophies": M.starter_rows(case)}
    return {"trophies": M.forecast_case(case)}


def run_variant(seeds, damping=None, mult=None, guard=None):
    """Evaluate one variant over all scenarios x seeds. Returns
    (mean loss, per-scenario mean loss dict)."""
    saved = (M.RICH_DAMPING, M.RICH_MULT_FIXED)
    if damping is not None:
        M.RICH_DAMPING = damping
    if mult is not None:
        M.RICH_MULT_FIXED = mult
    per_sc = {}
    all_losses = []
    for sc in SCENARIOS:
        losses = []
        for seed in seeds:
            case, truth = r1_sim.gen_case(seed, sc)
            if guard is None:
                out = {"trophies": M.forecast_case(case)}
            else:
                out = forecast_guarded(case, guard)
            losses.append(r1_score.score(out, truth)["loss"])
        per_sc[sc] = sum(losses) / len(losses)
        all_losses.extend(losses)
    M.RICH_DAMPING, M.RICH_MULT_FIXED = saved
    return sum(all_losses) / len(all_losses), per_sc


def main():
    for label, seeds in (("TRAIN 1..5", range(1, 6)),
                         ("HELD-OUT 6..10", range(6, 11))):
        print("=== %s ===" % label)
        variants = [
            ("default d0.4 m0.75", dict()),
            ("damping 1.0", dict(damping=1.0)),
            ("damping 0.6", dict(damping=0.6)),
            ("damping 1.0 + mult 1.0", dict(damping=1.0, mult=1.0)),
            ("guard>=4", dict(guard=4)),
            ("guard>=6", dict(guard=6)),
            ("guard>=8", dict(guard=8)),
            ("damping 1.0 + guard>=6", dict(damping=1.0, guard=6)),
            ("damping 0.6 + guard>=6", dict(damping=0.6, guard=6)),
        ]
        for name, kw in variants:
            mean, per_sc = run_variant(seeds, **kw)
            slow = per_sc["refresh_slow"]
            print("  %-24s mean %.4f  refresh_slow %.4f  base %.4f" % (
                name, mean, slow, per_sc["base"]))


if __name__ == "__main__":
    main()
