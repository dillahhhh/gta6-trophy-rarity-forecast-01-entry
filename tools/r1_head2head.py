#!/usr/bin/env python3
"""Black-box head-to-head: rank1-fresh vs challenger fixed-0.75 on the
SAME independent hazard simulator (r1_sim), all scenarios x seeds
6-25, history 2.486d (real f00 span).

Ting (21:02 WIB): "Kita belum punya bukti head-to-head yang sah antara
rank1-fresh dan challenger karena keduanya diuji pada keluarga simulator
berbeda." This closes that gap WITHOUT reading the challenger's code:
the challenger's main.py is extracted with `git show` to /tmp and run
as a black box (same contract: reads $MAKEFUN_INPUT_DIR, writes
$MAKEFUN_OUTPUT_DIR). No branch is checked out, nothing is modified,
no challenger source is opened.

Reports per-scenario mean loss for both, overall, win rate, and the
split by segment count (< 8 vs >= 8) -- the split is the decision:
rank1-fresh falls back to the starter below 8 segments, the challenger
(so its own report claims) runs its damped rich path there.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r1_score
import r1_sim
import r1_revision as R

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHALLENGER_MAIN = "/tmp/r1-h2h-challenger-main.py"


def _extract_challenger():
    p = subprocess.run(
        ["git", "show", "challenger-fixed075:main.py"],
        cwd=REPO, capture_output=True, text=True)
    assert p.returncode == 0, "git show failed: %s" % p.stderr[:300]
    with open(CHALLENGER_MAIN, "w") as f:
        f.write(p.stdout)
    print("challenger main.py extracted to %s (%d bytes, NOT read)"
          % (CHALLENGER_MAIN, len(p.stdout)))


def _run_one(main_path, case, tag):
    d_in = "/tmp/r1-h2h-%s-in" % tag
    d_out = "/tmp/r1-h2h-%s-out" % tag
    os.makedirs(d_in, exist_ok=True)
    os.makedirs(d_out, exist_ok=True)
    for f in os.listdir(d_out):
        os.remove(os.path.join(d_out, f))
    with open(os.path.join(d_in, case["id"] + ".in"), "w") as f:
        json.dump(case, f)
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = d_in
    env["MAKEFUN_OUTPUT_DIR"] = d_out
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, main_path], env=env, capture_output=True, text=True)
    dt = time.time() - t0
    if p.returncode != 0:
        return None, dt, p.stderr[:300]
    with open(os.path.join(d_out, case["id"] + ".out")) as f:
        return json.load(f), dt, ""


def main():
    _extract_challenger()
    r1_sim._params = R._patched_params
    scenarios = ["base", "refresh_fast", "refresh_mid", "refresh_slow",
                 "growth_fast", "growth_slow", "noise", "jump"]
    rows = []
    try:
        for sc in scenarios:
            for seed in list(range(6, 11)) + list(range(11, 16)) + list(range(16, 26)):
                case, truth = r1_sim.gen_case(seed, sc, history_days=2.486)
                n_seg = r1_sim.n_segments(case)
                tag = "held-out" if seed <= 10 else ("fresh" if seed <= 15 else "extra")
                out_r, dt_r, err_r = _run_one(
                    os.path.join(REPO, "main.py"), case, "r-%s-%d" % (sc, seed))
                out_c, dt_c, err_c = _run_one(
                    CHALLENGER_MAIN, case, "c-%s-%d" % (sc, seed))
                if out_r is None or out_c is None:
                    print("  CRASH %s seed %d: rank1=%s challenger=%s" % (
                        sc, seed, err_r, err_c))
                    continue
                lr = r1_score.score(out_r, truth)["loss"]
                lc = r1_score.score(out_c, truth)["loss"]
                rows.append({"scenario": sc, "seed": seed, "tag": tag,
                             "segments": n_seg, "rank1_loss": lr,
                             "challenger_loss": lc,
                             "rank1_runtime": round(dt_r, 3),
                             "challenger_runtime": round(dt_c, 3)})
    finally:
        r1_sim._params = R._orig_params
    print("\nhead-to-head on the hazard simulator (history 2.486d):")
    print("  %-13s %s" % ("scenario", "n   rank1  challenger  wins(rank1)  rank1<8 / chall<8"))
    for sc in scenarios:
        rs = [r for r in rows if r["scenario"] == sc]
        mr = sum(r["rank1_loss"] for r in rs) / len(rs)
        mc = sum(r["challenger_loss"] for r in rs) / len(rs)
        w = sum(1 for r in rs if r["rank1_loss"] < r["challenger_loss"])
        sparse = [r for r in rs if r["segments"] < 8]
        ms_r = sum(r["rank1_loss"] for r in sparse) / len(sparse) if sparse else float("nan")
        ms_c = sum(r["challenger_loss"] for r in sparse) / len(sparse) if sparse else float("nan")
        print("  %-13s %2d  %.4f  %.4f      %2d/%d      %s / %s (n=%d)"
              % (sc, len(rs), mr, mc, w, len(rs),
                 ("%.4f" % ms_r) if sparse else "-",
                 ("%.4f" % ms_c) if sparse else "-", len(sparse)))
    for split, pred in (("segments >= 8", lambda r: r["segments"] >= 8),
                        ("segments < 8", lambda r: r["segments"] < 8),
                        ("ALL", lambda r: True)):
        rs = [r for r in rows if pred(r)]
        mr = sum(r["rank1_loss"] for r in rs) / len(rs)
        mc = sum(r["challenger_loss"] for r in rs) / len(rs)
        w = sum(1 for r in rs if r["rank1_loss"] < r["challenger_loss"])
        print("  %-13s n=%2d  rank1 %.4f  challenger %.4f  rank1 wins %d/%d"
              % (split, len(rs), mr, mc, w, len(rs)))
    print("  runtimes: rank1 max %.2fs, challenger max %.2fs (limit 8s)"
          % (max(r["rank1_runtime"] for r in rows),
             max(r["challenger_runtime"] for r in rows)))
    with open(os.path.join(REPO, "tools/r1_head2head.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print("Results written to tools/r1_head2head.json")


if __name__ == "__main__":
    main()
