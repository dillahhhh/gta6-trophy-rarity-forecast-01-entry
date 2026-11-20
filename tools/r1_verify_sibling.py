#!/usr/bin/env python3
"""Verify the CONCURRENT RANK1-REPLICATION changes to main.py (working
tree, uncommitted, sibling agent still running) -- independently, on
fresh seeds, using the ORIGINAL build (git HEAD:main.py) as the module.

The sibling changed main.py (working tree only) in two ways:
  (a) FROZEN_SIGMA 0.003 -> 0.0001 (claims practice loss ~0.0000012,
      beating Rank 1's 0.000005).
  (b) Sparse tier: 6-7 segments -> degraded rich (damping 0.4, mult 1.0)
      instead of the starter; claims sparse improves 0.5308 -> 0.4813
      (seeds 1-25) and 0.5133 (fresh seeds 26-50), dense untouched.

This script checks, WITHOUT using the sibling's modified file:
  1. Tier (6-7 segs, d0.4/m1.0) vs starter, per seed group -- the
     decisive group is seeds 26-50 (never used by anyone for selection).
     Also confirms 4-5 seg cases stay starter (guard excludes them).
  2. Frozen sigma 0.0001 on p00/p01 (practice loss).
  3. Head-to-head tail re-run (noise + jump, seeds 6-25) with the
     ORIGINAL main.py, diffed against tools/r1_head2head.json -- the
     sibling edited main.py mid-run (14:20:14 UTC); this proves whether
     any recorded head-to-head number was contaminated.

Stdlib only. Prints a report, writes tools/r1_verify_sibling.json.
"""

import importlib.util
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r1_score
import r1_sim
import r1_revision as R

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIG_MAIN = "/tmp/r1-verify-orig-main.py"
KIT_IN = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input"


def _load_orig():
    p = subprocess.run(["git", "show", "HEAD:main.py"], cwd=REPO,
                       capture_output=True, text=True)
    assert p.returncode == 0
    with open(ORIG_MAIN, "w") as f:
        f.write(p.stdout)
    spec = importlib.util.spec_from_file_location("orig_main", ORIG_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tier_out(M, case):
    """Original build with the sibling's tier constants (d0.4/m1.0,
    guard>=6): reproduces the sibling's tier without using their file."""
    saved = (M.RICH_DAMPING, M.RICH_MULT_FIXED, M.RICH_MIN_SEGMENTS)
    try:
        M.RICH_DAMPING = 0.4
        M.RICH_MULT_FIXED = 1.0
        M.RICH_MIN_SEGMENTS = 6
        return {"trophies": M.forecast_case(case)}
    finally:
        (M.RICH_DAMPING, M.RICH_MULT_FIXED, M.RICH_MIN_SEGMENTS) = saved


def main():
    M = _load_orig()
    print("original main.py loaded from HEAD (%s), FROZEN_SIGMA=%s, guard=%s"
          % (ORIG_MAIN, M.FROZEN_SIGMA, M.RICH_MIN_SEGMENTS))
    # --- 1. Tier test (6-7 segs) ---
    print("\n=== 1. Sparse tier (6-7 segs, d0.4/m1.0) vs starter ===")
    r1_sim._params = R._patched_params
    groups = {"train 1-5": range(1, 6), "held-out 6-10": range(6, 11),
              "fresh 11-15": range(11, 16), "extra 16-25": range(16, 26),
              "confirm 26-50": range(26, 51)}
    all_rows = []
    try:
        for sc in ("refresh_slow", "refresh_mid", "base"):
            for seed in range(1, 51):
                case, truth = r1_sim.gen_case(seed, sc, history_days=2.486)
                n = r1_sim.n_segments(case)
                if n not in (6, 7):
                    continue
                tag = ("train 1-5" if seed <= 5 else
                       "held-out 6-10" if seed <= 10 else
                       "fresh 11-15" if seed <= 15 else
                       "extra 16-25" if seed <= 25 else "confirm 26-50")
                sl = r1_score.score({"trophies": M.starter_rows(case)}, truth)["loss"]
                tl = r1_score.score(_tier_out(M, case), truth)["loss"]
                all_rows.append({"scenario": sc, "seed": seed, "tag": tag,
                                 "segments": n, "starter": sl, "tier": tl})
    finally:
        r1_sim._params = R._orig_params
    print("  6-7 seg cases: %d" % len(all_rows))
    for tag in groups:
        rs = [r for r in all_rows if r["tag"] == tag]
        if not rs:
            print("  %-16s n=0" % tag)
            continue
        ms = sum(r["starter"] for r in rs) / len(rs)
        mt = sum(r["tier"] for r in rs) / len(rs)
        w = sum(1 for r in rs if r["tier"] < r["starter"])
        print("  %-16s n=%2d  starter %.4f  tier %.4f  (tier-starter %+.4f)  wins %d/%d"
              % (tag, len(rs), ms, mt, mt - ms, w, len(rs)))
    # 4-5 segs: tier guard must leave them as starter.
    r1_sim._params = R._patched_params
    low = []
    try:
        for sc in ("refresh_slow", "refresh_mid"):
            for seed in range(26, 51):
                case, truth = r1_sim.gen_case(seed, sc, history_days=2.486)
                if r1_sim.n_segments(case) in (4, 5):
                    low.append((sc, seed, r1_sim.n_segments(case),
                                r1_score.score(_tier_out(M, case), truth)["loss"],
                                r1_score.score({"trophies": M.starter_rows(case)}, truth)["loss"]))
    finally:
        r1_sim._params = R._orig_params
    same = sum(1 for _, _, _, tl, sl in low if abs(tl - sl) < 1e-12)
    print("  4-5 seg cases (seeds 26-50): %d, tier==starter on %d (guard excludes them)"
          % (len(low), same))
    # --- 2. Frozen sigma ---
    print("\n=== 2. Frozen sigma 0.0001 on p00/p01 ===")
    src = json.load(open(os.path.join(REPO, "data/rank1-sources.json")))
    truth = src["page_rates_refreshed_2026_11_19_0916Z"]
    for sigma in (0.003, 0.0001):
        M.FROZEN_SIGMA = sigma
        losses = []
        for pid in ("p00", "p01"):
            case = json.load(open(os.path.join(KIT_IN, pid + ".in")))
            out = {"trophies": M.forecast_case(case)}
            losses.append(r1_score.score(out, truth)["loss"])
        print("  sigma %.4f: p00 %.7f  p01 %.7f" % (sigma, losses[0], losses[1]))
    M.FROZEN_SIGMA = 0.003
    # --- 3. Head-to-head tail re-run vs recorded ---
    print("\n=== 3. Head-to-head tail re-run (noise + jump) vs recorded ===")
    recorded = {(r["scenario"], r["seed"]): r
                for r in json.load(open(os.path.join(REPO, "tools/r1_head2head.json")))}
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import r1_head2head as H
    maxdiff = 0.0
    ndiff = 0
    r1_sim._params = R._patched_params
    try:
        for sc in ("noise", "jump"):
            for seed in list(range(6, 11)) + list(range(11, 16)) + list(range(16, 26)):
                case, truth = r1_sim.gen_case(seed, sc, history_days=2.486)
                out, dt, err = H._run_one(ORIG_MAIN, case, "v-%s-%d" % (sc, seed))
                assert out is not None, "orig main crashed: %s" % err
                loss = r1_score.score(out, truth)["loss"]
                rec = recorded[(sc, seed)]["rank1_loss"]
                d = abs(loss - rec)
                maxdiff = max(maxdiff, d)
                if d > 1e-9:
                    ndiff += 1
                    print("  DIFF %s seed %d: recomputed %.6f recorded %.6f" % (sc, seed, loss, rec))
    finally:
        r1_sim._params = R._orig_params
    print("  tail cases re-run: 40, differing: %d, max abs diff %.2e" % (ndiff, maxdiff))
    print("  -> head-to-head numbers are %s by the concurrent main.py edit"
          % ("UNCONTAMINATED" if ndiff == 0 else "CONTAMINATED"))
    with open(os.path.join(REPO, "tools/r1_verify_sibling.json"), "w") as f:
        json.dump({"tier_rows": all_rows,
                   "low_seg_same": same, "low_seg_n": len(low),
                   "tail_maxdiff": maxdiff, "tail_ndiff": ndiff}, f, indent=1)
    print("\nResults written to tools/r1_verify_sibling.json")


if __name__ == "__main__":
    main()
