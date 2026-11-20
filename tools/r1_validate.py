#!/usr/bin/env python3
"""Independent validation runner for the fresh build (rank1-fresh).

Runs the rank-1 prompt's validation battery and prints a report:
  A. Practice check (p00/p01 frozen path vs the transcribed page rates).
  B. Leg B diagnostic (frozen history -> live snapshot as held-out target).
  C. Simulator head-to-head (my rich path vs the house starter, on
     identical synthetic histories, per scenario and seed).
  D. Knob selection on training seeds (1..5), evaluated on held-out
     seeds (6..10) -- answers the selection-bias concern.
  E. Per-trophy / coverage / quantile-calibration / per-interval /
     sparse-vs-dense reports for the selected config.
  F. Determinism (byte-identical twice), runtime (< 8 s/case), and
     output-format checks.

Results are printed and also written to tools/r1_results.json.
"""

import json
import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r1_score
import r1_sim
import main as M

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_IN = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input"


def _starter_out(case):
    return {"trophies": M.starter_rows(case)}


def _rich_out(case):
    return {"trophies": M.forecast_case(case)}


def _run_main_on_case(case, tag):
    """Write a case to a temp input dir, run main.py, return the .out."""
    d_in = "/tmp/r1-val-%s-in" % tag
    d_out = "/tmp/r1-val-%s-out" % tag
    os.makedirs(d_in, exist_ok=True)
    os.makedirs(d_out, exist_ok=True)
    with open(os.path.join(d_in, case["id"] + ".in"), "w") as f:
        json.dump(case, f)
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = d_in
    env["MAKEFUN_OUTPUT_DIR"] = d_out
    t0 = time.time()
    subprocess.run(
        [sys.executable, os.path.join(REPO, "main.py")],
        env=env, check=True, capture_output=True)
    dt = time.time() - t0
    with open(os.path.join(d_out, case["id"] + ".out")) as f:
        out = json.load(f)
    return out, dt


def section_a_practice():
    print("=== A. Practice check (frozen path) ===")
    src = json.load(open(os.path.join(REPO, "data/rank1-sources.json")))
    truth = src["page_rates_refreshed_2026_11_19_0916Z"]
    res = {}
    for pid in ("p00", "p01"):
        case = json.load(open(os.path.join(KIT_IN, pid + ".in")))
        out, dt = _run_main_on_case(case, pid)
        s = r1_score.score(out, truth)
        res[pid] = s
        print("  %s: loss %.6f  miss %.4f pp  medianAE %.4f  cov90 %.3f  (%.2fs)" % (
            pid, s["loss"], s["typical_miss_pp"], s["median_ae_pp"],
            s["coverage90"], dt))
    print("  (expected: loss ~0.0006, miss 0.00pp, cov90 1.000)")
    return res


def section_b_legb():
    print("=== B. Leg B diagnostic (frozen history -> live snapshot) ===")
    src = json.load(open(os.path.join(REPO, "data/rank1-sources.json")))
    live = src["live_snapshot_rates_2026_11_20_0907Z"]
    case = json.load(open(os.path.join(KIT_IN, "p01.in")))
    # Retarget the frozen-history case at the live snapshot time.
    case = dict(case)
    case["id"] = "legb-live"
    case["at"] = "2026-11-19T03:30:00Z"
    case["target"] = "2026-11-20T09:07:00Z"
    case["horizon_minutes"] = 1837
    out, dt = _run_main_on_case(case, "legb")
    s = r1_score.score(out, live)
    print("  live snapshot as target (30.6h ahead of frozen history):")
    print("  loss %.4f  miss %.3f pp  medianAE %.3f  cov90 %.3f" % (
        s["loss"], s["typical_miss_pp"], s["median_ae_pp"], s["coverage90"]))
    print("  (diagnostic only: a frozen history carries no signal about")
    print("   the future dilution, so no model can score well here; the")
    print("   starter fallback is the honest answer.)")
    return s


def _sim_head_to_head(seeds, scenarios, tag):
    """Run my forecaster vs the starter on identical synthetic cases."""
    rows = []
    for scenario in scenarios:
        for seed in seeds:
            case, truth = r1_sim.gen_case(seed, scenario)
            out_rich = _rich_out(case)
            out_starter = _starter_out(case)
            s_rich = r1_score.score(out_rich, truth)
            s_st = r1_score.score(out_starter, truth)
            rows.append({
                "scenario": scenario, "seed": seed,
                "segments": r1_sim.n_segments(case),
                "rich_loss": s_rich["loss"],
                "starter_loss": s_st["loss"],
                "rich_miss": s_rich["typical_miss_pp"],
                "starter_miss": s_st["typical_miss_pp"],
                "rich_cov": s_rich["coverage90"],
                "starter_cov": s_st["coverage90"],
                "rich_medae": s_rich["median_ae_pp"],
                "rich_per_trophy": s_rich["per_trophy"],
            })
    return rows


def section_c_head_to_head():
    print("=== C. Simulator head-to-head (rich vs starter) ===")
    scenarios = ["base", "refresh_fast", "refresh_slow",
                 "growth_fast", "growth_slow", "noise", "jump"]
    rows = _sim_head_to_head(range(1, 6), scenarios, "c")
    agg = {}
    for r in rows:
        agg.setdefault(r["scenario"], []).append(r)
    for sc in scenarios:
        rs = agg[sc]
        rl = sum(r["rich_loss"] for r in rs) / len(rs)
        sl = sum(r["starter_loss"] for r in rs) / len(rs)
        rc = sum(r["rich_cov"] for r in rs) / len(rs)
        seg = sum(r["segments"] for r in rs) / len(rs)
        win = sum(1 for r in rs if r["rich_loss"] < r["starter_loss"])
        print("  %-13s segs %4.1f  rich %.4f  starter %.4f  cov90 %.2f  win %d/%d" % (
            sc, seg, rl, sl, rc, win, len(rs)))
    rl_all = sum(r["rich_loss"] for r in rows) / len(rows)
    sl_all = sum(r["starter_loss"] for r in rows) / len(rows)
    win_all = sum(1 for r in rows if r["rich_loss"] < r["starter_loss"])
    print("  OVERALL (%d cases): rich %.4f vs starter %.4f, win %d/%d" % (
        len(rows), rl_all, sl_all, win_all, len(rows)))
    return rows


def section_d_knobs():
    print("=== D. Knob selection (train seeds 1..5) -> held-out (6..10) ===")
    scenarios = ["base", "refresh_fast", "refresh_slow",
                 "growth_fast", "growth_slow", "noise", "jump"]
    # Coordinate descent: vary one knob at a time around the defaults.
    variants = []
    for d in (0.2, 0.4, 0.6, 1.0):
        variants.append(("damping=%.1f" % d, {"RICH_DAMPING": d}))
    for p in (5.0, 10.0, 20.0):
        variants.append(("prior=%.0f" % p, {"RICH_PRIOR_WEIGHT": p}))
    for m in (0.5, 0.75, 1.0, 1.5):
        variants.append(("mult=%.2f" % m, {"RICH_MULT_FIXED": m}))
    variants.append(("tau=12 fixed", {"RICH_TAU_GRID": (12.0,)}))
    variants.append(("tau=24 fixed", {"RICH_TAU_GRID": (24.0,)}))
    variants.append(("tau=6 fixed", {"RICH_TAU_GRID": (6.0,)}))
    defaults = {"RICH_DAMPING": 0.4, "RICH_PRIOR_WEIGHT": 10.0,
                "RICH_MULT_FIXED": 0.75, "RICH_TAU_GRID": (6.0, 12.0, 24.0, 48.0)}

    def _apply(patch):
        for k, v in defaults.items():
            setattr(M, k, v)
        for k, v in patch.items():
            setattr(M, k, v)

    def _mean_loss(seeds, patch):
        _apply(patch)
        rows = _sim_head_to_head(seeds, scenarios, "d")
        return sum(r["rich_loss"] for r in rows) / len(rows), rows

    _apply({})
    train_default, _ = _mean_loss(range(1, 6), {})
    print("  default (damping 0.4, prior 10, mult 0.75, tau auto): train %.4f" % train_default)
    best = {}
    for name, patch in variants:
        ml, _ = _mean_loss(range(1, 6), patch)
        print("  %-16s train %.4f  (delta %+.4f)" % (name, ml, ml - train_default))
        best[name] = ml
    # Pick the best single-knob change (if it beats default by > 0.005).
    winner = min(best, key=best.get)
    print("  best single-knob variant on train: %s (%.4f)" % (winner, best[winner]))
    # Combine the best of each knob family.
    combo = {}
    for fam, names in (("damping", [n for n in best if n.startswith("damping")]),
                       ("prior", [n for n in best if n.startswith("prior")]),
                       ("mult", [n for n in best if n.startswith("mult")])):
        bn = min(names, key=lambda n: best[n])
        combo.update(dict([v for n, v in variants if n == bn][0].items()))
    # Tau: keep auto unless a fixed tau clearly wins on train.
    tau_names = [n for n in best if n.startswith("tau")]
    bt = min(tau_names, key=lambda n: best[n])
    if best[bt] < train_default - 0.005:
        combo.update(dict([v for n, v in variants if n == bt][0].items()))
    print("  combined best knobs: %s" % combo)
    # Held-out evaluation: default vs combined. NOTE: _mean_loss applies
    # the patch it is given, so the combined run must receive `combo`
    # (passing {} would silently re-apply the defaults -- a bug that
    # made an earlier run report identical held-out losses).
    _apply({})
    held_default, _ = _mean_loss(range(6, 11), {})
    held_combo, held_rows = _mean_loss(range(6, 11), combo)
    print("  HELD-OUT seeds 6..10: default %.4f  vs  combined %.4f" % (
        held_default, held_combo))
    _apply({})
    return {"combo": combo, "held_default": held_default,
            "held_combo": held_combo, "held_rows": held_rows,
            "train_default": train_default, "variant_train": best}


def section_e_reports(held_rows):
    print("=== E. Per-trophy / calibration / sparse-vs-dense (held-out) ===")
    # Aggregate per-trophy loss over held-out rows.
    agg = {}
    cov = {}
    for r in held_rows:
        for t in r["rich_per_trophy"]:
            agg.setdefault(t["id"], []).append(t["loss"])
            cov.setdefault(t["id"], []).append(1 if t["covered"] else 0)
    worst = sorted(agg.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:8]
    print("  worst trophies by mean loss (held-out):")
    for tid, losses in worst:
        print("    id %2d  loss %.4f  cov90 %.2f" % (
            tid, sum(losses) / len(losses),
            sum(cov[tid]) / len(cov[tid])))
    # Coverage by scenario (sparse vs dense).
    by_sc = {}
    for r in held_rows:
        by_sc.setdefault(r["scenario"], []).append(r)
    print("  coverage90 / loss by scenario (sparse=refresh_slow, dense=refresh_fast):")
    for sc, rs in sorted(by_sc.items()):
        print("    %-13s loss %.4f  cov90 %.2f  miss %.3f pp  medAE %.3f" % (
            sc, sum(x["rich_loss"] for x in rs) / len(rs),
            sum(x["rich_cov"] for x in rs) / len(rs),
            sum(x["rich_miss"] for x in rs) / len(rs),
            sum(x["rich_medae"] for x in rs) / len(rs)))


def section_f_determinism():
    print("=== F. Determinism / runtime / format ===")
    case, _ = r1_sim.gen_case(7, "base")
    out1, dt1 = _run_main_on_case(case, "f1")
    out2, dt2 = _run_main_on_case(case, "f2")
    b1 = json.dumps(out1, separators=(",", ":"), sort_keys=True)
    b2 = json.dumps(out2, separators=(",", ":"), sort_keys=True)
    print("  byte-identical across two runs: %s" % (b1 == b2))
    print("  runtime per case: %.2fs and %.2fs (limit 8s)" % (dt1, dt2))
    # Format checks on a real output.
    p00 = json.load(open("/tmp/r1-val-p00-out/p00.out"))
    ok = True
    ok &= len(p00["trophies"]) == 55
    for row in p00["trophies"]:
        q = row["q"]
        ok &= len(q) == 5 and all(isinstance(v, (int, float)) for v in q)
        ok &= all(0.0 <= v <= 100.0 for v in q)
        ok &= all(q[i] <= q[i + 1] for i in range(4))
    size = os.path.getsize("/tmp/r1-val-p00-out/p00.out")
    print("  format valid (55 rows, 5 quantiles, non-decreasing, in [0,100]): %s" % ok)
    print("  output size %d bytes (limit 65,536)" % size)
    return {"identical": b1 == b2, "dt1": dt1, "dt2": dt2, "format_ok": ok, "size": size}


def main():
    results = {}
    results["A"] = section_a_practice()
    results["B"] = section_b_legb()
    results["C"] = section_c_head_to_head()
    d = section_d_knobs()
    results["D"] = {k: v for k, v in d.items() if k != "held_rows"}
    results["D"]["held_rows_summary"] = [
        {k: r[k] for k in ("scenario", "seed", "segments", "rich_loss",
                           "starter_loss", "rich_cov", "rich_miss")}
        for r in d["held_rows"]]
    section_e_reports(d["held_rows"])
    results["F"] = section_f_determinism()
    with open(os.path.join(REPO, "tools/r1_results.json"), "w") as f:
        json.dump(results, f, indent=1, default=str)
    print("\nResults written to tools/r1_results.json")


if __name__ == "__main__":
    main()
