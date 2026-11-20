#!/usr/bin/env python3
"""Phase 4 independent head-to-head (RANK1-REPLICATION).

Runs every candidate on IDENTICAL inputs with the SAME scorer
(tools/r1_score.py, the RULES.txt pinball-logit x2):

  candidates: rank1-fresh (local main.py), challenger-fixed075,
              M0, house starter (persistence).
  evaluators (kept separate -- never mixed):
    H  hazard simulator (tools/r1_sim.py, independent family),
       7 scenarios x seeds 1-5 (train ref) / 6-10 (held-out) / 11-15 (fresh),
       history 2.486d = the real f00 span (2026-11-18T23:50Z -> 11-21T11:30Z)
    C  cohort simulator (challenger tools/misspec.py legc_case_misspec +
       compare_challenger._legc_case), 9 misspec scenarios + base,
       seeds 7/11/13/23/29 (the challenger's own protocol)
    B  Leg B real data: frozen practice history retargeted at the live
       snapshot (2026-11-20 09:07Z), one diagnostic point
    P  practice p00/p01 (frozen path), same truth as Phase 3

Candidate code is extracted from git at runtime (never modified) and
imported as modules. Nothing is pushed, submitted or bundled here.
Results -> tools/r1_replication_h2h_results.json
"""
import importlib.util
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, REPO)

import r1_score
import r1_sim
import main as M

TMP = "/tmp/r1rep/h2h"
TMP_REPO = "/tmp/r1rep/h2h_repo"  # mimics the challenger repo layout, because
# compare_challenger.py resolves REPO = Path(__file__).parent.parent and
# loads REPO/"main.py" at import time.
os.makedirs(TMP, exist_ok=True)
os.makedirs(TMP_REPO + "/tools", exist_ok=True)
HIST_DAYS = 2.486  # real f00 span


def _git_show(rev, path, dest):
    subprocess.run(["git", "show", "%s:%s" % (rev, path)],
                   cwd=REPO, stdout=open(dest, "wb"), check=True)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_out(ret):
    if isinstance(ret, dict) and "trophies" in ret:
        return ret
    return {"trophies": ret}


def main():
    _git_show("challenger-fixed075", "main.py", TMP + "/challenger_main.py")
    _git_show("main", "main.py", TMP + "/m0_main.py")
    _git_show("challenger-fixed075", "main.py", TMP_REPO + "/main.py")
    _git_show("challenger-fixed075", "tools/misspec.py", TMP_REPO + "/tools/misspec.py")
    _git_show("challenger-fixed075", "tools/compare_challenger.py",
              TMP_REPO + "/tools/compare_challenger.py")
    sys.path.insert(0, TMP)
    C = _load("challenger_mod", TMP + "/challenger_main.py")
    M0 = _load("m0_mod", TMP + "/m0_main.py")
    MS = _load("misspec_mod", TMP_REPO + "/tools/misspec.py")
    CC = _load("compare_mod", TMP_REPO + "/tools/compare_challenger.py")

    cands = {
        "rank1-fresh": lambda case: _as_out(M.forecast_case(case)),
        "challenger": lambda case: _as_out(C.forecast_case(case)),
        "M0": lambda case: _as_out(M0.forecast_case(case, "m0")),
        "starter": lambda case: {"trophies": M.starter_rows(case)},
    }
    results = {"hazard": [], "cohort": [], "legB": [], "practice": {}}

    # ---- H: hazard simulator ----
    print("== H. hazard simulator (history %.3fd) ==" % HIST_DAYS, flush=True)
    scenarios = ["base", "refresh_fast", "refresh_slow", "growth_fast",
                 "growth_slow", "noise", "jump"]
    for sc in scenarios:
        for seed in list(range(1, 16)):
            group = "train" if seed <= 5 else ("heldout" if seed <= 10 else "fresh")
            case, truth = r1_sim.gen_case(seed, sc, history_days=HIST_DAYS)
            row = {"scenario": sc, "seed": seed, "group": group,
                   "segments": r1_sim.n_segments(case)}
            for name, fn in cands.items():
                t0 = time.time()
                out = fn(case)
                s = r1_score.score(out, truth)
                row[name] = {"loss": s["loss"], "miss": s["typical_miss_pp"],
                             "cov90": s["coverage90"], "medae": s["median_ae_pp"],
                             "dt": round(time.time() - t0, 2)}
            results["hazard"].append(row)
        print("  %s done" % sc, flush=True)

    # ---- C: cohort simulator (challenger protocol) ----
    print("== C. cohort simulator (misspec, seeds 7/11/13/23/29) ==", flush=True)
    scen9 = [
        ("base", {}),
        ("refresh-fast-3h", {"n_seg": 40, "seg_hours": 3}),
        ("refresh-slow-12h", {"n_seg": 10, "seg_hours": 12}),
        ("jump-up-x1.5", {"jump_at": 10, "jump_factor": 1.5}),
        ("jump-down-x0.6", {"jump_at": 10, "jump_factor": 0.6}),
        ("no-refresh-24h", {"freeze_last_h": 24.0}),
        ("growth-fast-x1.5", {"growth": 1.5}),
        ("growth-slow-x0.5", {"growth": 0.5}),
        ("read-noise-0.05pp", {"noise_sigma": 0.05}),
    ]
    for name, kw in scen9:
        for s in (7, 11, 13, 23, 29):
            case, truth = MS.legc_case_misspec(s, **kw)
            row = {"scenario": name, "seed": s}
            for cname, fn in cands.items():
                out = fn(case)
                sc = r1_score.score(out, truth)
                row[cname] = {"loss": sc["loss"], "miss": sc["typical_miss_pp"],
                              "cov90": sc["coverage90"], "medae": sc["median_ae_pp"]}
            results["cohort"].append(row)
        print("  %s done" % name, flush=True)
    for s in (7, 11, 13, 23, 29):
        case, truth = CC._legc_case(s)
        row = {"scenario": "legc-base", "seed": s}
        for cname, fn in cands.items():
            out = fn(case)
            sc = r1_score.score(out, truth)
            row[cname] = {"loss": sc["loss"], "miss": sc["typical_miss_pp"],
                          "cov90": sc["coverage90"], "medae": sc["median_ae_pp"]}
        results["cohort"].append(row)
    print("  legc-base done", flush=True)

    # ---- B: Leg B real data ----
    print("== B. Leg B (frozen history -> live snapshot) ==", flush=True)
    src = json.load(open(os.path.join(REPO, "data/rank1-sources.json")))
    live = src["live_snapshot_rates_2026_11_20_0907Z"]
    kit_in = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input"
    case = json.load(open(os.path.join(kit_in, "p01.in")))
    case = dict(case)
    case["id"] = "legb-live"
    case["at"] = "2026-11-19T03:30:00Z"
    case["target"] = "2026-11-20T09:07:00Z"
    case["horizon_minutes"] = 1837
    for cname, fn in cands.items():
        out = fn(case)
        sc = r1_score.score(out, live)
        results["legB"].append({cname: {"loss": sc["loss"],
                                        "miss": sc["typical_miss_pp"],
                                        "cov90": sc["coverage90"],
                                        "medae": sc["median_ae_pp"]}})
        print("  %s loss %.4f" % (cname, sc["loss"]), flush=True)

    # ---- P: practice (frozen path), same scorer/truth ----
    print("== P. practice ==", flush=True)
    truth = src["page_rates_refreshed_2026_11_19_0916Z"]
    for pid in ("p00", "p01"):
        case = json.load(open(os.path.join(kit_in, pid + ".in")))
        for cname, fn in cands.items():
            out = fn(case)
            sc = r1_score.score(out, truth)
            results["practice"].setdefault(pid, {})[cname] = sc["loss"]
            print("  %s %s loss %.6f" % (pid, cname, sc["loss"]), flush=True)

    with open(os.path.join(REPO, "tools/r1_replication_h2h_results.json"), "w") as f:
        json.dump(results, f, indent=1)
    print("results -> tools/r1_replication_h2h_results.json", flush=True)

    def _summ(rows):
        agg = {}
        for r in rows:
            agg.setdefault(r["scenario"], []).append(r)
        for sc, rs in sorted(agg.items()):
            line = "  %-16s" % sc
            for cname in ("rank1-fresh", "challenger", "M0", "starter"):
                ml = sum(x[cname]["loss"] for x in rs) / len(rs)
                line += " %s=%.4f" % (cname[:4], ml)
            print(line, flush=True)
    print("-- hazard summary (mean loss per scenario, all seeds) --", flush=True)
    _summ(results["hazard"])
    print("-- cohort summary (mean loss per scenario) --", flush=True)
    _summ(results["cohort"])
    for group in ("train", "heldout", "fresh"):
        rs = [r for r in results["hazard"] if r["group"] == group]
        line = "hazard %s:" % group
        for cname in ("rank1-fresh", "challenger", "M0", "starter"):
            ml = sum(x[cname]["loss"] for x in rs) / len(rs)
            line += " %s=%.4f" % (cname, ml)
        print(line, flush=True)


if __name__ == "__main__":
    main()
