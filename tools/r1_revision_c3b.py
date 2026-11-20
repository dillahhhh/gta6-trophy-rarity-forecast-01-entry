#!/usr/bin/env python3
"""REVISI follow-up C3b: confirm the degraded-path winner on a LARGER
sparse sample before touching main.py.

C3 found, on 11 sparse cases (segs < 8, seeds 6-15):
  d0.4 guard>=3 mult1.25: held-out 0.4867 vs starter 0.5126, fresh 0.5123
  vs starter 0.5749, wins 4/5 and 5/6. mult1.0 also beat the starter.
  The full-window variants silently fell back to the starter (their
  window_hours=1e9 overflowed datetime inside _rich_rows, and
  forecast_case's exception handler masked it) -- retested here with
  a safe 72h window.

This script: fresh seeds 16-25 (never used for any selection) +
held-out 6-10 + fresh 11-15, scenarios refresh_slow + refresh_mid,
history 2.486d (real f00 span). Candidates: starter, and the mult
sweep at damping 0.4 guard>=3, plus guard>=2 and tau24 variants.
Decision rule: implement only if a candidate beats the starter on
held-out AND fresh AND extra seeds (mean loss AND win rate > 50%).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r1_score
import r1_sim
import main as M
import r1_revision as R

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    r1_sim._params = R._patched_params
    cases = []  # (scenario, seed, tag, case, truth, segments)
    try:
        for sc in ("refresh_slow", "refresh_mid"):
            for seed in list(range(6, 11)) + list(range(11, 16)) + list(range(16, 26)):
                case, truth = r1_sim.gen_case(seed, sc, history_days=2.486)
                n = r1_sim.n_segments(case)
                if n < 8:
                    tag = "held-out" if seed <= 10 else ("fresh" if seed <= 15 else "extra")
                    cases.append((sc, seed, tag, case, truth, n))
    finally:
        r1_sim._params = R._orig_params
    print("sparse cases (segs < 8): %d  (held-out %d, fresh %d, extra %d)" % (
        len(cases),
        sum(1 for c in cases if c[2] == "held-out"),
        sum(1 for c in cases if c[2] == "fresh"),
        sum(1 for c in cases if c[2] == "extra")))
    print("segment distribution: %s" % sorted(c[5] for c in cases))
    candidates = [
        ("starter (current fallback)", None),
        ("d0.4 g>=3 mult0.75", dict(damping=0.4, min_segments=3)),
        ("d0.4 g>=3 mult1.0", dict(damping=0.4, min_segments=3, mult=1.0)),
        ("d0.4 g>=3 mult1.25", dict(damping=0.4, min_segments=3, mult=1.25)),
        ("d0.4 g>=3 mult1.5", dict(damping=0.4, min_segments=3, mult=1.5)),
        ("d0.4 g>=2 mult1.25", dict(damping=0.4, min_segments=2, mult=1.25)),
        ("d0.4 g>=3 mult1.25 tau24", dict(damping=0.4, min_segments=3, mult=1.25, tau_grid=(24.0,))),
        ("d0.4 g>=3 mult1.25 win72h", dict(damping=0.4, min_segments=3, mult=1.25, window_hours=72.0)),
        ("d0.5 g>=3 mult1.25", dict(damping=0.5, min_segments=3, mult=1.25)),
        ("d0.3 g>=3 mult1.25", dict(damping=0.3, min_segments=3, mult=1.25)),
    ]
    starter_losses = [
        r1_score.score({"trophies": M.starter_rows(c)}, t)["loss"]
        for _, _, _, c, t, _ in cases
    ]
    results = {}
    for name, kw in candidates:
        per_tag = {"held-out": [], "fresh": [], "extra": []}
        wins = {"held-out": [0, 0], "fresh": [0, 0], "extra": [0, 0]}
        for (sc, seed, tag, case, truth, n), sl in zip(cases, starter_losses):
            if kw is None:
                loss = sl
            else:
                loss = r1_score.score(R._candidate_out(case, **kw), truth)["loss"]
            per_tag[tag].append(loss)
            wins[tag][1] += 1
            wins[tag][0] += 1 if loss < sl else 0
        results[name] = {"per_tag": per_tag, "wins": wins}
        print("  %-28s held-out %.4f (%s)  fresh %.4f (%s)  extra %.4f (%s)" % (
            name,
            sum(per_tag["held-out"]) / len(per_tag["held-out"]), "%d/%d" % tuple(wins["held-out"]),
            sum(per_tag["fresh"]) / len(per_tag["fresh"]), "%d/%d" % tuple(wins["fresh"]),
            sum(per_tag["extra"]) / len(per_tag["extra"]), "%d/%d" % tuple(wins["extra"])))
    # Per-case detail for the leading candidate vs starter.
    lead = "d0.4 g>=3 mult1.25"
    print("\nper-case losses, %s vs starter (scenario seed segs tag: lead / starter):" % lead)
    kw = dict(damping=0.4, min_segments=3, mult=1.25)
    for (sc, seed, tag, case, truth, n), sl in zip(cases, starter_losses):
        ll = r1_score.score(R._candidate_out(case, **kw), truth)["loss"]
        print("  %-13s %2d segs=%d %-8s %.4f / %.4f  %s" % (
            sc, seed, n, tag, ll, sl, "WIN" if ll < sl else "loss"))
    # C3c. Median-move cap: the degraded path's losses are blowups (wrong
    # trend extrapolated), its wins modest. Test capping the median's logit
    # move from the last rate (post-hoc rigid logit shift of the output
    # quantiles -- equivalent to capping the move inside _rich_rows).
    print("\nC3c. Median-move cap on d0.4 g>=3 mult1.25 (post-hoc logit shift):")
    def _capped_out(case, cap):
        out = R._candidate_out(case, damping=0.4, min_segments=3, mult=1.25)
        last = case["history"][-1]["rates"]
        rows = []
        for row, lr in zip(out["trophies"], last):
            med = row["q"][2]
            move = M.logit_rate(med) - M.logit_rate(lr)
            cm = max(-cap, min(cap, move))
            d = cm - move
            qs = [round(M.inv_logit_rate(M.logit_rate(q) + d), 4) for q in row["q"]]
            rows.append({"id": row["id"], "q": M.enforce_nondecreasing(qs)})
        return {"trophies": rows}
    for cap in (0.5, 1.0, 1.5):
        per_tag = {"held-out": [], "fresh": [], "extra": []}
        wins = {"held-out": [0, 0], "fresh": [0, 0], "extra": [0, 0]}
        for (sc, seed, tag, case, truth, n), sl in zip(cases, starter_losses):
            loss = r1_score.score(_capped_out(case, cap), truth)["loss"]
            per_tag[tag].append(loss)
            wins[tag][1] += 1
            wins[tag][0] += 1 if loss < sl else 0
        print("  cap %.1f logit: held-out %.4f (%s)  fresh %.4f (%s)  extra %.4f (%s)" % (
            cap,
            sum(per_tag["held-out"]) / len(per_tag["held-out"]), "%d/%d" % tuple(wins["held-out"]),
            sum(per_tag["fresh"]) / len(per_tag["fresh"]), "%d/%d" % tuple(wins["fresh"]),
            sum(per_tag["extra"]) / len(per_tag["extra"]), "%d/%d" % tuple(wins["extra"])))
    with open(os.path.join(REPO, "tools/r1_revision_c3b.json"), "w") as f:
        json.dump({name: {"means": {t: (sum(v) / len(v) if v else None)
                                    for t, v in r["per_tag"].items()},
                          "wins": r["wins"]}
                   for name, r in results.items()}, f, indent=1)
    print("\nResults written to tools/r1_revision_c3b.json")


if __name__ == "__main__":
    main()
