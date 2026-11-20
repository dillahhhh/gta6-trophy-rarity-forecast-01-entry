#!/usr/bin/env python3
"""Three-family comparison (RANK1-REVISION): rich path vs house starter
on IDENTICAL cases with the SAME scorer (tools/r1_score.py, RULES.txt
pinball-logit x2):

  H  hazard simulator (tools/r1_sim.py): 7 scenarios x seeds 1-15,
     history 2.486d (the real f00 span).
  C  cohort simulator (challenger tools/misspec.py + compare_challenger):
     9 misspec scenarios + legc-base, seeds 7/11/13/23/29.
  R  random-walk + calendar shocks (tools/r1_rw_sim.py): 7 scenarios
     x seeds 1-15, history 2.486d. NO log(1+t/tau) anywhere.

Candidate code is the working tree's main.py (rank1-revision). Nothing
is pushed, submitted or bundled here. Results -> /tmp (stdout report);
the human-readable report is tools/RANK1-REVISION-THIRD-FAMILY-REPORT.md.
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
import r1_rw_sim
import main as M

TMP_REPO = "/tmp/r1rep/h2h_repo"
HIST_DAYS = 2.486


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure_challenger_tools():
    if os.path.exists(os.path.join(TMP_REPO, "tools", "compare_challenger.py")):
        return
    os.makedirs(TMP_REPO + "/tools", exist_ok=True)
    for rev_path, dest in [
        ("main.py", TMP_REPO + "/main.py"),
        ("tools/misspec.py", TMP_REPO + "/tools/misspec.py"),
        ("tools/compare_challenger.py", TMP_REPO + "/tools/compare_challenger.py"),
    ]:
        subprocess.run(["git", "show", "challenger-fixed075:%s" % rev_path],
                       cwd=REPO, stdout=open(dest, "wb"), check=True)


def _run_family(rows, label):
    rich_losses = [r["rich"] for r in rows]
    st_losses = [r["starter"] for r in rows]
    wins = sum(1 for r in rows if r["rich"] < r["starter"])
    print("== %s: n=%d rich=%.4f starter=%.4f wins=%d/%d (%.0f%%) ==" % (
        label, len(rows), sum(rich_losses) / len(rich_losses),
        sum(st_losses) / len(st_losses), wins, len(rows),
        100.0 * wins / len(rows)), flush=True)
    by_scen = {}
    for r in rows:
        by_scen.setdefault(r["scenario"], []).append(r)
    for sc, rs in sorted(by_scen.items()):
        ml_r = sum(x["rich"] for x in rs) / len(rs)
        ml_s = sum(x["starter"] for x in rs) / len(rs)
        w = sum(1 for x in rs if x["rich"] < x["starter"])
        print("  %-18s rich=%.4f starter=%.4f wins=%d/%d" % (sc, ml_r, ml_s, w, len(rs)), flush=True)
    return {
        "n": len(rows),
        "rich_mean": sum(rich_losses) / len(rich_losses),
        "starter_mean": sum(st_losses) / len(st_losses),
        "wins": wins,
    }


def main():
    _ensure_challenger_tools()
    MS = _load("misspec_mod", TMP_REPO + "/tools/misspec.py")
    CC = _load("compare_mod", TMP_REPO + "/tools/compare_challenger.py")

    rich = lambda case: {"trophies": M.forecast_case(case)}
    starter = lambda case: {"trophies": M.starter_rows(case)}
    summary = {}

    # ---- H: hazard ----
    print("== H. hazard ==", flush=True)
    scenarios = ["base", "refresh_fast", "refresh_slow", "growth_fast",
                 "growth_slow", "noise", "jump"]
    rows = []
    for sc in scenarios:
        for seed in range(1, 16):
            case, truth = r1_sim.gen_case(seed, sc, history_days=HIST_DAYS)
            rows.append({"scenario": sc, "seed": seed, "group":
                         "train" if seed <= 5 else ("heldout" if seed <= 10 else "fresh"),
                         "segments": r1_sim.n_segments(case),
                         "rich": r1_score.score(rich(case), truth)["loss"],
                         "starter": r1_score.score(starter(case), truth)["loss"]})
        print("  %s done" % sc, flush=True)
    summary["hazard"] = _run_family(rows, "H hazard")
    for group in ("train", "heldout", "fresh"):
        rs = [r for r in rows if r["group"] == group]
        ml_r = sum(x["rich"] for x in rs) / len(rs)
        ml_s = sum(x["starter"] for x in rs) / len(rs)
        w = sum(1 for x in rs if x["rich"] < x["starter"])
        print("  hazard %-8s rich=%.4f starter=%.4f wins=%d/%d" % (group, ml_r, ml_s, w, len(rs)), flush=True)

    # ---- C: cohort ----
    print("== C. cohort ==", flush=True)
    rows = []
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
            rows.append({"scenario": name, "seed": s,
                         "rich": r1_score.score(rich(case), truth)["loss"],
                         "starter": r1_score.score(starter(case), truth)["loss"]})
        print("  %s done" % name, flush=True)
    for s in (7, 11, 13, 23, 29):
        case, truth = CC._legc_case(s)
        rows.append({"scenario": "legc-base", "seed": s,
                     "rich": r1_score.score(rich(case), truth)["loss"],
                     "starter": r1_score.score(starter(case), truth)["loss"]})
    print("  legc-base done", flush=True)
    summary["cohort"] = _run_family(rows, "C cohort")

    # ---- R: random walk + calendar shocks ----
    print("== R. random walk + shocks ==", flush=True)
    rw_scen = ["base", "rw_fast", "rw_slow", "shock_heavy", "shock_none",
               "growth_fast", "growth_slow"]
    rows = []
    for sc in rw_scen:
        for seed in range(1, 16):
            case, truth = r1_rw_sim.gen_case(seed, sc, history_days=HIST_DAYS)
            rows.append({"scenario": sc, "seed": seed, "group":
                         "train" if seed <= 5 else ("heldout" if seed <= 10 else "fresh"),
                         "segments": r1_rw_sim.n_segments(case),
                         "rich": r1_score.score(rich(case), truth)["loss"],
                         "starter": r1_score.score(starter(case), truth)["loss"]})
        print("  %s done" % sc, flush=True)
    summary["random_walk"] = _run_family(rows, "R random-walk")
    for group in ("train", "heldout", "fresh"):
        rs = [r for r in rows if r["group"] == group]
        ml_r = sum(x["rich"] for x in rs) / len(rs)
        ml_s = sum(x["starter"] for x in rs) / len(rs)
        w = sum(1 for x in rs if x["rich"] < x["starter"])
        print("  rw %-8s rich=%.4f starter=%.4f wins=%d/%d" % (group, ml_r, ml_s, w, len(rs)), flush=True)

    fams_won = sum(1 for f in summary.values() if f["rich_mean"] < f["starter_mean"])
    print("VERDICT: rich beats starter on mean loss in %d/3 families" % fams_won, flush=True)
    with open("/tmp/r1_third_family_summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    print("summary -> /tmp/r1_third_family_summary.json", flush=True)


if __name__ == "__main__":
    main()
