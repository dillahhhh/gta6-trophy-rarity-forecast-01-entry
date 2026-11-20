"""Ting REVISI #2 tests for the fixed-0.75 challenger (branch challenger-fixed075).

Ting's verdict (2026-11-20 19:45 WIB): REVISI — TAHAN RESUBMIT. The
eb82928 tuner picked tau=6 / mult=1.5 on the 48h-history f00-shaped
input (pinball 0.3050, worse than M0 0.3003) while fixed 0.75 scored
0.2433 on the same input. Fix: multiplier PINNED at 0.75 (the tuner
no longer chooses it at all); damping 0.4 / prior 10 / tau grid
unchanged. These tests decide GO/NO-GO for the fixed-0.75 variant.

Test 1: Leg C head-to-head, SAME 5 seeds (7/11/13/23/29):
  B075 conservative (0.4/10) + fixed 0.75 (SHIPPED)
  B100 conservative (0.4/10) + fixed 1.00 (monkeypatched RICH_MULT_FIXED)
  A075 aggressive (1.0/2) + fixed 0.75 (continuity reference)
  M0 (black-box CLI), persistence. Per-seed losses.

Test 2: Leg C with REFRESH MISSPECIFICATION (tools/misspec.py), same
  9 scenarios as revision_tests.py: B075 vs B100 vs M0 vs persistence.
  Refresh-slow-12h vs persistence is reported EXPLICITLY (Ting: do not
  hide it in the 9-scenario mean).

Test 3: Spread/coverage — shipped fixed 0.75 vs wider 1.0 and 1.5 at
  the same tau, same 5 seeds: pinball, coverage90, max q05-q95 width,
  and the id-2 q05 check (the pre-check anomaly: q05 ~50% at mult 1.5
  vs ~71% at 0.75 — the 0.75 value must hold).

Test 4: Final harness — f00-shaped case (history 19 Nov 11:30Z ->
  21 Nov 11:30Z, target 26 Nov 00:00Z, horizon 6510 min, 96 reads):
  _backtest_choose MUST return mult == 0.75; CLI output must equal
  the in-process forecast at mult 0.75 exactly; two CLI runs
  byte-identical; runtime < 8 s; f00 NEVER enters the frozen path;
  p00/p01 still score 0.000591.

Usage:
  python3 tools/fixed075_tests.py --test 1
  python3 tools/fixed075_tests.py --test 2
  python3 tools/fixed075_tests.py --test 3
  python3 tools/fixed075_tests.py --test 4
  python3 tools/fixed075_tests.py --test all
"""
import argparse
import importlib.util
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_challenger as CC  # noqa: E402
import misspec as MS  # noqa: E402
import revision_tests as RT  # noqa: E402

CH = CC.CH
SEEDS = [7, 11, 13, 23, 29]
TAU_GRID = [6, 12, 24, 48]
MULT_GRID = [0.5, 0.75, 1.0, 1.5]
AGGRESSIVE = (1.0, 2)
CONSERVATIVE = (0.4, 10)


def run_challenger_fixed(case, damping, prior, mult):
    """Challenger with RICH_MULT_FIXED monkeypatched to `mult` (test only).

    Production main.py never selects a multiplier; this only lets the
    tests compare fixed 0.75 (shipped) against fixed 1.0.
    """
    orig = CH.RICH_MULT_FIXED
    CH.RICH_MULT_FIXED = mult
    try:
        return CC.run_challenger(case, damping, prior, TAU_GRID, MULT_GRID)
    finally:
        CH.RICH_MULT_FIXED = orig


def coverage90(forecast, truth_rates):
    return RT.coverage90(forecast, truth_rates)


def max_width(forecast):
    return max(r["q"][4] - r["q"][0] for r in forecast["trophies"])


# ------------------------------------------------------------------ Test 1
def test1():
    print("== Test 1 (fixed075): Leg C, seeds 7/11/13/23/29 ==")
    print("seed | B075 cons+0.75 | B100 cons+1.00 | A075 aggr+0.75 | M0 | persistence")
    res = {"B075": [], "B100": [], "A075": [], "M0": [], "P": []}
    taus = []
    for s in SEEDS:
        case, truth = CC._legc_case(s)
        fc075, _ = run_challenger_fixed(case, *CONSERVATIVE, 0.75)
        l075 = CC.score_forecast(fc075, truth)
        fc100, _ = run_challenger_fixed(case, *CONSERVATIVE, 1.0)
        l100 = CC.score_forecast(fc100, truth)
        fcA, _ = run_challenger_fixed(case, *AGGRESSIVE, 0.75)
        lA = CC.score_forecast(fcA, truth)
        fm, _ = CC.run_m0(case)
        lM = CC.score_forecast(fm, truth)
        fp = CC.run_persistence(case)
        lP = CC.score_forecast(fp, truth)
        res["B075"].append(l075)
        res["B100"].append(l100)
        res["A075"].append(lA)
        res["M0"].append(lM)
        res["P"].append(lP)
        reads_all, _, _, _ = RT._rich_preamble(case)
        tau, mult = CH._backtest_choose(reads_all, *CONSERVATIVE)
        taus.append((tau, mult))
        print(f"  {s} | {l075:.4f} | {l100:.4f} | {lA:.4f} | {lM:.4f} | {lP:.4f} "
              f"(tuner: tau={tau} mult={mult})")
    for k, v in res.items():
        print(f"MEAN {k}: {sum(v) / len(v):.4f}  (per-seed: "
              + ", ".join(f"{x:.4f}" for x in v) + ")")
    b075 = sum(res["B075"]) / 5
    print(f"B075 - M0: {b075 - sum(res['M0']) / 5:+.4f}  "
          f"B075 - B100: {b075 - sum(res['B100']) / 5:+.4f}  "
          f"B075 - P: {b075 - sum(res['P']) / 5:+.4f}  "
          f"B075 wins vs M0: {sum(1 for a, b in zip(res['B075'], res['M0']) if a < b)}/5 seeds")
    print(f"chosen taus: {taus} (mult must be 0.75 everywhere)")
    assert all(m == 0.75 for _, m in taus), "tuner returned a mult != 0.75!"
    return res


# ------------------------------------------------------------------ Test 2
def test2():
    print("== Test 2 (fixed075): Leg C with refresh misspecification ==")
    print("scenario | seed | B075 | B100 | M0 | persistence")
    summary = {}
    for name, kw in RT.SCENARIOS:
        b075, b100, m_losses, p_losses = [], [], [], []
        for s in SEEDS:
            case, truth = MS.legc_case_misspec(s, **kw)
            fc075, _ = run_challenger_fixed(case, *CONSERVATIVE, 0.75)
            fc100, _ = run_challenger_fixed(case, *CONSERVATIVE, 1.0)
            fm, _ = CC.run_m0(case)
            fp = CC.run_persistence(case)
            l075 = CC.score_forecast(fc075, truth)
            l100 = CC.score_forecast(fc100, truth)
            lM = CC.score_forecast(fm, truth)
            lP = CC.score_forecast(fp, truth)
            b075.append(l075)
            b100.append(l100)
            m_losses.append(lM)
            p_losses.append(lP)
            print(f"  {name} | {s} | {l075:.4f} | {l100:.4f} | {lM:.4f} | {lP:.4f}")
        summary[name] = (sum(b075) / 5, sum(b100) / 5,
                         sum(m_losses) / 5, sum(p_losses) / 5)
        print(f"MEAN {name}: B075={summary[name][0]:.4f} B100={summary[name][1]:.4f} "
              f"M0={summary[name][2]:.4f} P={summary[name][3]:.4f}  "
              f"(B075-M0: {summary[name][0] - summary[name][2]:+.4f}, "
              f"B075-P: {summary[name][0] - summary[name][3]:+.4f})")
    # Ting: refresh-slow-12h vs persistence, explicitly, not hidden in a mean.
    slow = summary["refresh-slow-12h"]
    print(f"EXPLICIT refresh-slow-12h: B075={slow[0]:.4f} vs persistence={slow[3]:.4f} "
          f"(B075-P: {slow[0] - slow[3]:+.4f}); B075 vs M0: {slow[0] - slow[2]:+.4f}")
    n_win_m0 = sum(1 for v in summary.values() if v[0] < v[2])
    n_win_p = sum(1 for v in summary.values() if v[0] < v[3])
    print(f"B075 beats M0 in {n_win_m0}/9 scenarios, beats persistence in {n_win_p}/9")
    return summary


# ------------------------------------------------------------------ Test 3
def test3():
    print("== Test 3 (fixed075): spread/coverage — 0.75 vs 1.0 vs 1.5 ==")
    print("seed | tau | pinball 0.75 | pinball 1.0 | pinball 1.5 | cov90 0.75 | maxwidth 0.75 | id2 q05 0.75")
    rows = []
    for s in SEEDS:
        case, truth = CC._legc_case(s)
        reads_all, window, target, horizon_h = RT._rich_preamble(case)
        tau, mult = CH._backtest_choose(reads_all, *CONSERVATIVE)
        assert mult == 0.75, f"seed {s}: tuner mult {mult} != 0.75"
        outs = {}
        for m in (0.75, 1.0, 1.5):
            fc = CH._forecast_window(window, target, horizon_h, tau, m,
                                     *CONSERVATIVE)
            rws = [{"id": t["id"], "q": CH._quantiles_from(md, sg)}
                   for t, (md, sg) in zip(case["trophies"], fc)]
            outs[m] = {"trophies": rws}
        l075 = CC.score_forecast(outs[0.75], truth)
        l100 = CC.score_forecast(outs[1.0], truth)
        l150 = CC.score_forecast(outs[1.5], truth)
        q2_075 = outs[0.75]["trophies"][1]["q"][0]
        q2_150 = outs[1.5]["trophies"][1]["q"][0]
        rows.append((l075, l100, l150))
        print(f"  {s} | {tau} | {l075:.4f} | {l100:.4f} | {l150:.4f} | "
              f"{coverage90(outs[0.75], truth):.2f} | {max_width(outs[0.75]):.2f}pp | "
              f"{q2_075:.2f} (1.5 would give {q2_150:.2f})")
    m075 = sum(r[0] for r in rows) / 5
    m100 = sum(r[1] for r in rows) / 5
    m150 = sum(r[2] for r in rows) / 5
    print(f"MEAN pinball: 0.75={m075:.4f}  1.0={m100:.4f}  1.5={m150:.4f}  "
          f"(0.75-1.0: {m075 - m100:+.4f}, 0.75-1.5: {m075 - m150:+.4f})")
    return rows


# ------------------------------------------------------------------ Test 4
def test4():
    print("== Test 4 (fixed075): final harness (f00-shaped case) ==")
    t0 = datetime(2026, 11, 19, 11, 30, tzinfo=timezone.utc)
    f00, truth7 = MS.legc_case_misspec(7, n_seg=8, seg_hours=6, t0=t0,
                                       target_h=108.5)
    f00["id"] = "f00-shape"
    print(f"  f00-shape: at={f00['at']} target={f00['target']} "
          f"horizon_minutes={f00['horizon_minutes']} reads={len(f00['history'])}")
    frozen = CH._is_frozen_case(f00)
    print(f"  _is_frozen_case(f00-shape): {frozen} (must be False)")
    assert not frozen, "f00-shape entered the frozen path!"
    # The tuner must return mult == 0.75 on these exact reads.
    reads_all, window, target, horizon_h = RT._rich_preamble(f00)
    tau, mult = CH._backtest_choose(reads_all, *CONSERVATIVE)
    print(f"  _backtest_choose on f00 reads: tau={tau} mult={mult} (mult must be 0.75)")
    assert mult == 0.75, f"tuner returned mult {mult} != 0.75!"
    # In-process forecast at (tau, 0.75) — the CLI output must equal this.
    fc_in = CH._forecast_window(window, target, horizon_h, tau, 0.75,
                                *CONSERVATIVE)
    rows_in = [{"id": t["id"], "q": CH._quantiles_from(md, sg)}
               for t, (md, sg) in zip(f00["trophies"], fc_in)]
    outs1, dt1 = RT._run_cli({"f00-shape": f00})
    outs2, dt2 = RT._run_cli({"f00-shape": f00})
    same = outs1["f00-shape"] == outs2["f00-shape"]
    print(f"  CLI run 1: {dt1:.2f}s  run 2: {dt2:.2f}s  byte-identical: {same}")
    assert same, "two CLI runs differ!"
    assert max(dt1, dt2) < 8.0, "runtime >= 8s!"
    fc_cli = RT._check_format(outs1["f00-shape"], "f00-shape")
    print("  f00-shape format: PASS (55 rows, 5 quantiles, monotone, in [0,100], <=65536 B)")
    # CLI == in-process fixed-0.75 forecast, quantile by quantile.
    cli_q = {r["id"]: r["q"] for r in fc_cli["trophies"]}
    maxdiff = 0.0
    for r in rows_in:
        for a, b in zip(r["q"], cli_q[r["id"]]):
            maxdiff = max(maxdiff, abs(a - b))
    print(f"  CLI vs in-process (tau={tau}, mult=0.75): max |diff| = {maxdiff:.6f} pp "
          f"(must be 0)")
    assert maxdiff == 0.0, "CLI output differs from the fixed-0.75 forecast!"
    # The pre-check anomaly must be gone: id 2 q05 ~71 at 0.75, not ~50.
    q2 = cli_q[2][0]
    print(f"  id 2 q05 = {q2:.3f} (pre-check: 71.127 at 0.75 vs 49.849 at 1.5)")
    assert q2 > 60.0, f"id 2 q05 {q2} still shows the 1.5-style wide tail!"
    # f00 pinball vs the seed-7 simulator truth (reference: pre-check
    # fixed 0.75 scored 0.2433, M0 0.3003, tuner-1.5 0.3050).
    l_cli = CC.score_forecast(fc_cli, truth7)
    fm, _ = CC.run_m0(f00)
    l_m0 = CC.score_forecast(fm, truth7)
    print(f"  f00 pinball (seed 7 truth): CLI fixed-0.75 = {l_cli:.4f}  "
          f"M0 = {l_m0:.4f}  (pre-check: 0.2433 vs 0.3003)")
    # p00/p01 frozen path intact, via the kit input dir + §4 page rates.
    kit_in = CC.KIT / "input"
    p00 = json.loads((kit_in / "p00.in").read_text(encoding="utf-8"))
    p01 = json.loads((kit_in / "p01.in").read_text(encoding="utf-8"))
    outs, dt = RT._run_cli({"p00": p00, "p01": p01})
    truth = [CH.PAGE_RATES[i] for i in range(1, 56)]
    for name in ("p00", "p01"):
        fc = json.loads(outs[name].decode("utf-8"))
        loss = CC.score_forecast(fc, truth)
        RT._check_format(outs[name], name)
        print(f"  {name}: frozen-path loss {loss:.6f} (target ~0.00059)")
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
