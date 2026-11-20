"""Final long-horizon spread check (dev tool, NOT model code).

Ting's finalization checklist item 2: keep lambda = 1.25 as baseline; do ONE
last long-horizon spread check comparing lambda 1.25 against the
stress-adjusted spread, scored by MEAN PINBALL-LOGIT over all five quantiles
(NOT coverage alone). If the wider spread improves coverage but worsens
loss, do NOT pick the wider one. Do not widen just to chase 0.90 coverage.

Compares, on Leg C (model-truth simulator, 4.5-day horizon, truth known),
variant M0, same scenario columns for every candidate:
  A: lambda 1.25, no stress floor
  B: lambda 1.25 + stress floor  (current pipeline behaviour)
  C: lambda 1.00, no floor       (context: is 1.25 a local optimum?)
  D: lambda 1.50, no floor       (context: does widening help pinball?)
Each scored by mean pinball-logit (5 quantiles) + coverage90 (diagnostic).
Run on two truth seeds (7 and 11) to check consistency.

Usage: python3 tools/final_spread_check.py
"""
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(TOOLS))
import main as M
import backtest as BT


def score_all(fc, y):
    return BT.score_forecast(fc, y), BT.coverage90(fc, y)


def check_seed(seed):
    print(f"== Leg C spread check, model-truth seed {seed} ==")
    case, segments, truth = BT._model_truth_case(seed=seed)
    t_target = M.parse_time(case["at"]) + timedelta(days=4.5)
    case["target"] = t_target.strftime("%Y-%m-%dT%H:%M:%SZ")
    case["horizon_minutes"] = int(4.5 * 1440)
    y = BT._model_truth_target(segments, truth, t_target)
    ctx = M.build_context(case, segments)
    t0 = time.time()
    cols = M.run_scenarios(ctx, "M0", M.N_FINAL, M.SEED)
    base = M.quantiles_from_scenarios(ctx, cols)
    s_stress = M.stress_halfwidths(ctx, "M0")
    dt = time.time() - t0
    print(f"  scenarios + stress floor computed in {dt:.2f}s")
    results = {}
    for label, lam, floor in (("A lam=1.25 no-floor", 1.25, False),
                              ("B lam=1.25 +floor", 1.25, True),
                              ("C lam=1.00 no-floor", 1.00, False),
                              ("D lam=1.50 no-floor", 1.50, False)):
        fc = M._scale_quantiles_logit(base, lam)
        if floor:
            fc = M._apply_stress_floor(fc, s_stress)
        loss, cov = score_all(fc, y)
        results[label] = (loss, cov)
        print(f"  {label}: pinball-logit={loss:.4f} coverage90={cov:.2f}")
    # How many trophies does the floor actually widen?
    fc_a = M._scale_quantiles_logit(base, 1.25)
    fc_b = M._apply_stress_floor(fc_a, s_stress)
    widened = 0
    for ra, rb in zip(fc_a["trophies"], fc_b["trophies"]):
        if rb["q"] != ra["q"]:
            widened += 1
    print(f"  floor widens {widened}/55 trophies")
    return results


def main():
    all_res = {}
    for seed in (7, 11):
        all_res[seed] = check_seed(seed)
    print("== Decision (pinball-logit decides, coverage is diagnostic) ==")
    for seed, res in all_res.items():
        a, b = res["A lam=1.25 no-floor"][0], res["B lam=1.25 +floor"][0]
        verdict = ("floor HURTS pinball -> do NOT pick wider"
                   if b > a else "floor helps/neutral pinball -> keep floor")
        print(f"  seed {seed}: A={a:.4f} B={b:.4f} -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
