"""Ting REVISI tests for the challenger (branch challenger-trophy-lane).

Test 1: Leg C head-to-head, SAME 5 seeds (7/11/13/23/29), identical cases:
  A0 old-aggressive (damping 1.0 / prior 2) + OLD 6h backtest (diagnostic:
     should reproduce the 0.1002 mean from EXPERIMENTS.md)
  A  old-aggressive (1.0/2) + NEW horizon-corrected backtest
  B  conservative (0.4/10) + NEW horizon-corrected backtest (shipped)
  M0 (black-box CLI, never opened), persistence. Per-seed losses.

Test 2: Leg C with REFRESH MISSPECIFICATION (tools/misspec.py): faster /
  slower refresh, one large refresh off the prior trend (up and down),
  no refresh near the target, changed player growth (fast / slow), noise
  on rate reads + rounding. Both variants A and B per seed, plus M0 and
  persistence as reference. The goal is NOT a simulator that favours the
  model — it is to see whether the aggressive config collapses when
  refresh does not follow model-truth assumptions, and whether the
  conservative variant holds up.

Test 3: Horizon-aware spread — the backtest-chosen (constant) multiplier
  vs a more conservative (wider) multiplier at the long horizon, same
  tau, conservative variant. Mean pinball of the 5 quantiles decides,
  not coverage alone (coverage90 reported as diagnostic).

Test 4: Final harness — an f00-shaped case (history 19 Nov 11:30Z ->
  21 Nov 11:30Z, target 26 Nov 00:00Z, horizon 6510 min): 55 trophies,
  5 valid quantiles, monotonic, deterministic (two CLI runs byte-
  identical), runtime < 8 s; the f00 case NEVER enters the frozen path;
  p00/p01 still score 0.000591 (frozen path intact).

Usage:
  python3 tools/revision_tests.py --test 1
  python3 tools/revision_tests.py --test 2
  python3 tools/revision_tests.py --test 3
  python3 tools/revision_tests.py --test 4
  python3 tools/revision_tests.py --test all
"""
import argparse
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_challenger as CC  # noqa: E402
import misspec as MS  # noqa: E402

CH = CC.CH
REPO = CC.REPO
LEVELS = CC.LEVELS
SEEDS = [7, 11, 13, 23, 29]
TAU_GRID = [6, 12, 24, 48]
MULT_GRID = [0.5, 0.75, 1.0, 1.5]
AGGRESSIVE = (1.0, 2)
CONSERVATIVE = (0.4, 10)


# ------------------------------------------------- old 6h backtest (diagnostic)
def _backtest_choose_6h(reads_all, damping, prior_weight):
    """Verbatim copy of the 6h-horizon _backtest_choose from 68f5d32.

    Diagnostic only: reproduces the original 0.1002 mean so Test 1 can
    separate "the knobs were aggressive" from "the backtest was
    horizon-mismatched". It scores the read 12 reads (6h) after each
    origin and applies the chosen multiplier to a 4.5-day forecast.
    """
    if len(reads_all) < 25:
        return 12, 1.0
    end = reads_all[-1][0]
    lo = end - timedelta(hours=72)
    cand = [i for i, (dt, _) in enumerate(reads_all)
            if dt >= lo and dt <= end - timedelta(hours=6) and i >= 12
            and i + 12 < len(reads_all)]
    if not cand:
        return 12, 1.0
    if len(cand) > 5:
        step = (len(cand) - 1) / 4.0
        cand = sorted({cand[int(round(k * step))] for k in range(5)})
    best, best_score = (12, 1.0), None
    for tau in CH.RICH_TAU_GRID:
        for mult in CH.RICH_MULT_GRID:
            total, cnt = 0.0, 0
            for oi in cand:
                origin_dt = reads_all[oi][0]
                window = [(dt, r) for dt, r in reads_all
                          if origin_dt - timedelta(hours=24) < dt <= origin_dt]
                if len(window) < 2:
                    continue
                target_dt = reads_all[oi + 12][0]
                horizon_h = (target_dt - origin_dt).total_seconds() / 3600.0
                fc = CH._forecast_window(window, target_dt, horizon_h, tau, mult,
                                         damping, prior_weight)
                if fc is None:
                    continue
                truth = reads_all[oi + 12][1]
                for (med, sig), y in zip(fc, truth):
                    qs = CH._quantiles_from(med, sig)
                    yl = CH.logit(y)
                    for tau_l, qv in zip(LEVELS, qs):
                        d = yl - CH.logit(qv)
                        total += 2.0 * max(tau_l * d, (tau_l - 1.0) * d)
                        cnt += 1
            if cnt and (best_score is None or total / cnt < best_score):
                best_score = total / cnt
                best = (tau, mult)
    return best


def run_challenger_oldbt(case, damping, prior):
    """Challenger with the OLD 6h backtest (monkey-patched, then restored)."""
    orig = CH._backtest_choose
    CH._backtest_choose = _backtest_choose_6h
    try:
        return CC.run_challenger(case, damping, prior, TAU_GRID, MULT_GRID)
    finally:
        CH._backtest_choose = orig


def coverage90(forecast, truth_rates):
    rows = {r["id"]: r["q"] for r in forecast["trophies"]}
    hit = 0
    for i, y in enumerate(truth_rates):
        q = rows[i + 1]
        if q[0] <= y <= q[4]:
            hit += 1
    return hit / len(truth_rates)


def _rich_preamble(case):
    """Replicate _rich_forecast's preamble: (reads_all, window, target, horizon_h)."""
    at = CH.parse_time(case["at"])
    target = CH.parse_time(case["target"])
    horizon_h = case["horizon_minutes"] / 60.0
    reads_all = sorted(((CH._read_time(r), list(r["rates"]))
                        for r in case.get("history") or []),
                       key=lambda p: p[0])
    window = [(dt, r) for dt, r in reads_all
              if at - timedelta(hours=24) < dt <= at]
    return reads_all, window, target, horizon_h


# ------------------------------------------------------------------ Test 1
def test1():
    print("== Test 1: Leg C head-to-head, seeds 7/11/13/23/29 ==")
    print("seed | A0 aggr+old6h | A aggr+newbt | B cons+newbt | M0 | persistence")
    res = {"A0": [], "A": [], "B": [], "M0": [], "P": []}
    for s in SEEDS:
        case, truth = CC._legc_case(s)
        fc0, _ = run_challenger_oldbt(case, *AGGRESSIVE)
        l0 = CC.score_forecast(fc0, truth)
        fcA, _ = CC.run_challenger(case, *AGGRESSIVE, TAU_GRID, MULT_GRID)
        lA = CC.score_forecast(fcA, truth)
        fcB, _ = CC.run_challenger(case, *CONSERVATIVE, TAU_GRID, MULT_GRID)
        lB = CC.score_forecast(fcB, truth)
        fm, _ = CC.run_m0(case)
        lM = CC.score_forecast(fm, truth)
        fp = CC.run_persistence(case)
        lP = CC.score_forecast(fp, truth)
        res["A0"].append(l0)
        res["A"].append(lA)
        res["B"].append(lB)
        res["M0"].append(lM)
        res["P"].append(lP)
        print(f"  {s} | {l0:.4f} | {lA:.4f} | {lB:.4f} | {lM:.4f} | {lP:.4f}")
    for k, v in res.items():
        print(f"MEAN {k}: {sum(v) / len(v):.4f}  (per-seed: "
              + ", ".join(f"{x:.4f}" for x in v) + ")")
    print(f"B - M0: {sum(res['B']) / 5 - sum(res['M0']) / 5:+.4f}  "
          f"A - M0: {sum(res['A']) / 5 - sum(res['M0']) / 5:+.4f}  "
          f"A0 - A: {sum(res['A0']) / 5 - sum(res['A']) / 5:+.4f} (backtest-fix cost)")
    return res


# ------------------------------------------------------------------ Test 2
SCENARIOS = [
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


def test2():
    print("== Test 2: Leg C with refresh misspecification ==")
    print("scenario | seed | A aggr | B cons | M0 | persistence")
    summary = {}
    for name, kw in SCENARIOS:
        a_losses, b_losses, m_losses, p_losses = [], [], [], []
        for s in SEEDS:
            case, truth = MS.legc_case_misspec(s, **kw)
            fcA, _ = CC.run_challenger(case, *AGGRESSIVE, TAU_GRID, MULT_GRID)
            fcB, _ = CC.run_challenger(case, *CONSERVATIVE, TAU_GRID, MULT_GRID)
            fm, _ = CC.run_m0(case)
            fp = CC.run_persistence(case)
            lA = CC.score_forecast(fcA, truth)
            lB = CC.score_forecast(fcB, truth)
            lM = CC.score_forecast(fm, truth)
            lP = CC.score_forecast(fp, truth)
            a_losses.append(lA)
            b_losses.append(lB)
            m_losses.append(lM)
            p_losses.append(lP)
            print(f"  {name} | {s} | {lA:.4f} | {lB:.4f} | {lM:.4f} | {lP:.4f}")
        summary[name] = (sum(a_losses) / 5, sum(b_losses) / 5,
                         sum(m_losses) / 5, sum(p_losses) / 5)
        print(f"MEAN {name}: A={summary[name][0]:.4f} B={summary[name][1]:.4f} "
              f"M0={summary[name][2]:.4f} P={summary[name][3]:.4f}  "
              f"(B-A: {summary[name][1] - summary[name][0]:+.4f}, "
              f"B-M0: {summary[name][1] - summary[name][2]:+.4f})")
    return summary


# ------------------------------------------------------------------ Test 3
def test3():
    print("== Test 3: horizon-aware spread — constant vs wider mult ==")
    print("seed | chosen (tau,mult) | const mult | wider mult | const cov90 | wide cov90")
    const_losses, wide_losses = [], []
    for s in SEEDS:
        case, truth = CC._legc_case(s)
        reads_all, window, target, horizon_h = _rich_preamble(case)
        tau, mult = CH._backtest_choose(reads_all, *CONSERVATIVE)
        grid = list(MULT_GRID)
        wider = next((m for m in grid if m > mult), None)
        if wider is None:
            wider = min(1.5, mult * 1.5)
        fc_const = CH._forecast_window(window, target, horizon_h, tau, mult,
                                       *CONSERVATIVE)
        fc_wide = CH._forecast_window(window, target, horizon_h, tau, wider,
                                      *CONSERVATIVE)
        rows_c = [{"id": t["id"], "q": CH._quantiles_from(m, sg)}
                  for t, (m, sg) in zip(case["trophies"], fc_const)]
        rows_w = [{"id": t["id"], "q": CH._quantiles_from(m, sg)}
                  for t, (m, sg) in zip(case["trophies"], fc_wide)]
        lc = CC.score_forecast({"trophies": rows_c}, truth)
        lw = CC.score_forecast({"trophies": rows_w}, truth)
        const_losses.append(lc)
        wide_losses.append(lw)
        print(f"  {s} | ({tau},{mult}) | {lc:.4f} (mult {mult}) | {lw:.4f} "
              f"(mult {wider}) | {coverage90({'trophies': rows_c}, truth):.2f} | "
              f"{coverage90({'trophies': rows_w}, truth):.2f}")
    print(f"MEAN const: {sum(const_losses) / 5:.4f}  "
          f"MEAN wider: {sum(wide_losses) / 5:.4f}  "
          f"(wider - const: {sum(wide_losses) / 5 - sum(const_losses) / 5:+.4f})")
    return const_losses, wide_losses


# ------------------------------------------------------------------ Test 4
def _run_cli(in_files, timeout=60):
    """Run the branch main.py CLI on {name: case} files; return (outputs, seconds)."""
    with tempfile.TemporaryDirectory(prefix="challenger-t4-") as td:
        in_dir, out_dir = Path(td) / "in", Path(td) / "out"
        in_dir.mkdir()
        for name, case in in_files.items():
            (in_dir / f"{name}.in").write_text(json.dumps(case), encoding="utf-8")
        import os
        env = dict(os.environ)
        env["MAKEFUN_INPUT_DIR"] = str(in_dir)
        env["MAKEFUN_OUTPUT_DIR"] = str(out_dir)
        t0 = time.time()
        r = subprocess.run([sys.executable, str(REPO / "main.py")], env=env,
                           capture_output=True, text=True, timeout=timeout)
        dt = time.time() - t0
        if r.returncode != 0:
            raise RuntimeError(f"CLI exited {r.returncode}: {r.stdout}{r.stderr}")
        outs = {}
        for name in in_files:
            outs[name] = (out_dir / f"{name}.out").read_bytes()
        return outs, dt


def _check_format(raw, label):
    out = json.loads(raw.decode("utf-8"))
    assert len(raw) <= 65536, f"{label}: output > 65536 B"
    assert len(out["trophies"]) == 55, f"{label}: not 55 rows"
    for row in out["trophies"]:
        q = row["q"]
        assert len(q) == 5, f"{label}: row {row['id']} not 5 quantiles"
        assert all(0.0 <= v <= 100.0 for v in q), f"{label}: row {row['id']} out of range"
        assert all(q[i] <= q[i + 1] for i in range(4)), f"{label}: row {row['id']} not monotone"
        # (non-decreasing: the 100% trophy legitimately has all five
        # quantiles exactly 100.0, so strict < would false-fail it.)
    return out


def test4():
    print("== Test 4: final harness (f00-shaped case) ==")
    from datetime import datetime as _dt
    t0 = _dt(2026, 11, 19, 11, 30, tzinfo=timezone.utc)
    f00, _truth = MS.legc_case_misspec(7, n_seg=8, seg_hours=6, t0=t0,
                                       target_h=108.5)
    f00["id"] = "f00-shape"
    print(f"  f00-shape: at={f00['at']} target={f00['target']} "
          f"horizon_minutes={f00['horizon_minutes']} reads={len(f00['history'])}")
    frozen = CH._is_frozen_case(f00)
    print(f"  _is_frozen_case(f00-shape): {frozen} (must be False)")
    assert not frozen, "f00-shape entered the frozen path!"
    outs1, dt1 = _run_cli({"f00-shape": f00})
    outs2, dt2 = _run_cli({"f00-shape": f00})
    same = outs1["f00-shape"] == outs2["f00-shape"]
    print(f"  CLI run 1: {dt1:.2f}s  run 2: {dt2:.2f}s  byte-identical: {same}")
    assert same, "two CLI runs differ!"
    assert max(dt1, dt2) < 8.0, "runtime >= 8s!"
    _check_format(outs1["f00-shape"], "f00-shape")
    print("  f00-shape format: PASS (55 rows, 5 quantiles, monotone, in [0,100], <=65536 B)")
    # p00/p01 frozen path intact, via the kit input dir + §4 page rates.
    kit_in = CC.KIT / "input"
    p00 = json.loads((kit_in / "p00.in").read_text(encoding="utf-8"))
    p01 = json.loads((kit_in / "p01.in").read_text(encoding="utf-8"))
    outs, dt = _run_cli({"p00": p00, "p01": p01})
    truth = [CH.PAGE_RATES[i] for i in range(1, 56)]
    for name in ("p00", "p01"):
        fc = json.loads(outs[name].decode("utf-8"))
        loss = CC.score_forecast(fc, truth)
        _check_format(outs[name], name)
        print(f"  {name}: frozen-path loss {loss:.6f} (target ~0.00059), "
              f"runtime {dt:.2f}s for both")
        assert abs(loss - 0.000591) < 0.00005, f"{name} frozen loss moved!"
    print("  Test 4: PASS")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", default="all", choices=["1", "2", "3", "4", "all"])
    args = ap.parse_args()
    if args.test in ("1", "all"):
        test1()
    if args.test in ("2", "all"):
        test2()
    if args.test in ("3", "all"):
        test3()
    if args.test in ("4", "all"):
        test4()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
