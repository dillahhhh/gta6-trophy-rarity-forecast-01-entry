"""Dev backtest for gta6-trophy-rarity-forecast-01 (NOT shipped to the grader
as model code; it imports main.py as a module).

Legs (see DESIGN.md + Ting's conditions):
  Leg A  rolling origins over a case's own history (real house reads) — the
         primary leg; in the practice the history is frozen, so it reports
         zero usable origins, honestly.
  Leg B  DIAGNOSTIC ONLY: origin = practice last read, pseudo-target = the
         live snapshot (2026-11-20 09:07 UTC). One point, fuzzy target time.
         Never a scored backtest point, never the sole selector.
  Leg C  SYNTHETIC ONLY (stress test, not calibration evidence): a refresh
         simulator calibrated on the observed anchors generates
         pseudo-histories; the forecaster runs on them and is scored at
         4-4.5-day horizons. Results are simulator-conditional.
  Stress 3 scenarios (Ting): refresh faster than median; refresh slower
         incl. NO refresh until target; player growth much larger / smaller
         than projected. Final spread = max(s_calibrated, s_stress).

Scoring everywhere is the same pinball loss on the logit scale as RULES.txt:
  loss = mean over trophies and levels of 2*max(tau*d, (tau-1)*d),
  d = logit(y) - logit(q), rates clamped to 0.01..99.99.

Usage: python3 tools/backtest.py [--legs A,B,C] [--seed N]
"""
import json
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import main as M

KIT = Path.home() / "workspace/makefun/gta6-trophy-rarity-forecast-01-kit" / "gta6-trophy-rarity-forecast-01-kit"
SNAP = REPO / "data" / "live-snapshot-2026-11-20.json"


def pinball_logit(y, q, tau):
    d = M.logit(y) - M.logit(q)
    return 2.0 * max(tau * d, (tau - 1.0) * d)


def score_forecast(forecast, truth_rates):
    """Mean pinball-logit loss of a forecast dict against a list of true rates."""
    rows = {r["id"]: r["q"] for r in forecast["trophies"]}
    total, n = 0.0, 0
    for i, y in enumerate(truth_rates):
        q = rows[i + 1]
        for tau, qv in zip(M.LEVELS, q):
            total += pinball_logit(y, qv, tau)
            n += 1
    return total / n


def coverage90(forecast, truth_rates):
    rows = {r["id"]: r["q"] for r in forecast["trophies"]}
    inside = 0
    for i, y in enumerate(truth_rates):
        q = rows[i + 1]
        if q[0] <= y <= q[4]:
            inside += 1
    return inside / len(truth_rates)


def load_case(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_synthetic_case(segments, at, horizon_minutes, target, trophies):
    """Build a case dict from a list of (slot_datetime, P, rates) segment starts,
    expanding each segment into 30-min reads (identical within a segment)."""
    hist = []
    for j, (slot, P, rates) in enumerate(segments):
        end = segments[j + 1][0] if j + 1 < len(segments) else at
        t = slot
        while t < end:
            hist.append({"slot": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "players_tracked": P,
                         "trophies_earned": int(sum(r * P / 100 for r in rates)),
                         "rates": list(rates)})
            t += timedelta(minutes=30)
    return {"schema": "makefun/gta6-trophies-case/v1", "id": "synth",
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_minutes": horizon_minutes,
            "target": target.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "levels": list(M.LEVELS),
            "game": {"name": "Grand Theft Auto VI", "platform": "PS5",
                     "release_date": "2026-11-19",
                     "source": "https://www.exophase.com/game/grand-theft-auto-vi-psn/trophies/"},
            "trophies": trophies, "history": hist, "notes": "synthetic"}


def _synthetic_case():
    """The shared synthetic 4-segment history (P explodes, slow trophies
    dilute, fast ones rise) for the stage tests."""
    p00 = load_case(KIT / "input" / "p00.in")
    trophies = p00["trophies"]
    base = p00["history"][-1]["rates"]
    t0 = datetime(2026, 11, 18, 23, 50, tzinfo=timezone.utc)
    segs = []
    for k, (dh, P, mult_slow, fast) in enumerate([
            (0, 638, 1.0, 1.0),
            (5, 3868, 0.17, 1.3),
            (12, 20000, 0.05, 2.0),
            (30, 90000, 0.015, 3.0)]):
        rates = []
        for i, r in enumerate(base):
            if r >= 99.99:
                rates.append(100.0)
            elif i in (2, 19):
                rates.append(round(min(99.99, r * fast), 2))
            else:
                rates.append(round(max(0.01, r * mult_slow), 2))
        segs.append((t0 + timedelta(hours=dh), P, rates))
    at = t0 + timedelta(hours=36)
    case = make_synthetic_case(segs, at, 90, at + timedelta(minutes=90), trophies)
    return case, segs


def test_stage01():
    """Stage 0-1 on a synthetic multi-segment history (hand-built anchors)."""
    print("== Stage 0-1: segmentation + adoption + renewal (synthetic) ==")
    case, _ = _synthetic_case()
    segments = M.segment_history(case)
    print(f"  reads: {len(case['history'])}, segments detected: {len(segments)} (want 4)")
    assert len(segments) == 4, "segmentation failed"
    for s in segments:
        print(f"    {s['start']} P={s['P']} rates[0,2,19]={s['rates'][0]},{s['rates'][2]},{s['rates'][19]}")
    t_rel = M.parse_time(case["game"]["release_date"])
    fit = M.fit_adoption(segments, t_rel)
    print(f"  adoption: a={fit['a']:.3f} b={fit['b']:.3f} sigma={fit['sigma']:.3f} "
          f"max_daily_growth={fit['max_daily_growth']:.2f}")
    gaps = M.fit_gaps(segments)
    print(f"  gaps model: {gaps}")
    rng = random.Random(M.SEED)
    times = M.simulate_refresh_times(rng, gaps, segments[-1]["start"], M.parse_time(case["at"]),
                                     M.parse_time(case["at"]) + timedelta(days=4.5))
    print(f"  simulated refreshes to target+4.5d: {len(times)} (first {times[0] if times else None})")
    print("  Stage 0-1 OK")
    return True


def test_stage02():
    """Stage 2: trophy curves + M0/M1/M2 shrinkage on the synthetic history."""
    print("== Stage 2: trophy curves + shrinkage ==")
    case, _ = _synthetic_case()
    segments = M.segment_history(case)
    fit = M.fit_trophy_curves(case, segments)
    print(f"  rho={fit['rho']:.3f} sigma_res={fit['sigma_res']:.3f} sigma_g={fit['sigma_g']:.3f}")
    for name in ("M0", "M1", "M2"):
        p = fit["variants"][name]
        print(f"  {name}: id1 plat a={p[0][0]:.4f} k={p[0][1]:.4f} j={p[0][2]:.4f} | "
              f"id2 100% a={p[1][0]:.4f} k={p[1][1]:.4f} j={p[1][2]:.4f} | "
              f"id3 fast a={p[2][0]:.3f} k={p[2][1]:.3f} j={p[2][2]:.3f} | "
              f"id20 fast a={p[19][0]:.3f} k={p[19][1]:.3f} j={p[19][2]:.3f} | "
              f"id5 slow a={p[4][0]:.4f} k={p[4][1]:.4f} j={p[4][2]:.4f}")
        # The 100% trophy must stay ~100% in every variant (feasibility).
        assert p[1][0] > 99.9 and p[1][2] > 90, f"{name}: id2 collapsed to a={p[1][0]} j={p[1][2]}"
    # The fitted curves must reproduce the observed segment rates (censored).
    for name in ("M0",):
        for i in (0, 2, 19):
            a, k, j = fit["variants"][name][i]
            rates = M._recursion_rates(a, k, j, fit["r0"][i], segments)
            obs = [seg["rates"][i] for seg in segments]
            print(f"  {name} id{i+1}: fitted={[round(r, 2) for r in rates]} observed={obs}")
    print("  Stage 2 OK")
    return True


def _model_truth_case(seed: int = 7, n_seg: int = 20, seg_hours: int = 6):
    """History GENERATED FROM the cohort model (known a, k, j per trophy, P
    from the adoption curve, refreshes every seg_hours). The fit should
    recover it, so this is the clean ground for the ablation test and Leg C.
    Returns (case, segments, truth)."""
    p00 = load_case(KIT / "input" / "p00.in")
    trophies = p00["trophies"]
    base = p00["history"][-1]["rates"]
    rng = random.Random(seed)
    truth = []
    for r in base:
        truth.append((max(r, 0.005),
                      rng.choice([0.005, 0.02, 0.08, 0.3, 1.0]),
                      rng.choice([0.0, 0.001, 0.01, 0.05])))
    t_rel = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
    t0 = datetime(2026, 11, 18, 23, 50, tzinfo=timezone.utc)
    segs = []
    P = 638.0
    E = [P * r / 100.0 for r in base]
    for s in range(n_seg):
        t = t0 + timedelta(hours=seg_hours * s)
        tau = M.hours_between(t_rel, t)
        Pnew = math.exp(6.44 + 1.358 * math.log1p(tau))
        rates = []
        for i, (a, k, j) in enumerate(truth):
            E[i] = (a / 100) * P + (E[i] - (a / 100) * P) * math.exp(-k * seg_hours)
            E[i] += (j / 100) * (Pnew - P)
            rates.append(round(min(max(100 * E[i] / Pnew, 0.0), 100.0), 2))
        segs.append((t, int(round(Pnew)), rates))
        P = Pnew
    at = t0 + timedelta(hours=seg_hours * n_seg)
    case = make_synthetic_case(segs, at, 90, at + timedelta(minutes=90), trophies)
    return case, M.segment_history(case), truth


def _path_rate(a, k, j, r0, b_scale, n_steps: int = 18, step_h: int = 6):
    """Direct recursion of one trophy over 4.5 days under a P path whose
    adoption slope is scaled by b_scale. The P0 unit test: P must move R."""
    t_rel = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
    t = datetime(2026, 11, 20, 5, 50, tzinfo=timezone.utc)
    P = 90000.0
    E = P * r0 / 100.0
    for _ in range(n_steps):
        tau = M.hours_between(t_rel, t + timedelta(hours=step_h))
        Pnew = math.exp(6.44 + 1.358 * b_scale * math.log1p(tau))
        E = (a / 100) * P + (E - (a / 100) * P) * math.exp(-k * step_h)
        E += (j / 100) * (Pnew - P)
        P = Pnew
        t += timedelta(hours=step_h)
    return 100 * E / P


def test_stage03():
    """Stage 3: joint scenarios + the P0 ablation test.

    Ting's P0 fix demands proof: change the P growth rate and the predicted
    rate MUST move. If it does not move, the model is still wrong (P would
    have cancelled out of the rate equation). The decisive test is the
    direct recursion on slow-k trophies (where the P effect lives — fast-k
    trophies converge to their asymptote within the horizon, so their
    population-mean movement is small by construction); the scenario-level
    ablation on model-truth data is reported alongside."""
    print("== Stage 3: joint scenarios + ablation (P growth must move rates) ==")
    case, _ = _synthetic_case()
    # Lengthen the horizon to 4.5 days so the growth difference can bite.
    t_target = M.parse_time(case["at"]) + timedelta(days=4.5)
    case["target"] = t_target.strftime("%Y-%m-%dT%H:%M:%SZ")
    case["horizon_minutes"] = int(4.5 * 1440)
    segments = M.segment_history(case)
    ctx = M.build_context(case, segments)
    t0 = time.time()
    cols = M.run_scenarios(ctx, "M0", 1500, M.SEED)
    fc = M.quantiles_from_scenarios(ctx, cols)
    dt = time.time() - t0
    print(f"  1500 scenarios in {dt:.2f}s; sample rows:")
    for i in (0, 1, 2, 19):
        print(f"    id{i+1}: q={fc['trophies'][i]['q']} (last observed "
              f"{segments[-1]['rates'][i]})")
    # (a) Direct recursion proof on slow-k trophies.
    print("  direct recursion (P growth x1.5 / x0.5 vs x1.0):")
    worst = 99.0
    for (a, k, j, r0) in [(5.0, 0.005, 0.1, 1.0), (5.0, 0.02, 0.1, 1.0),
                          (20.0, 0.005, 0.0, 3.0), (0.5, 0.005, 0.0, 0.1)]:
        rf = _path_rate(a, k, j, r0, 1.5)
        rb = _path_rate(a, k, j, r0, 1.0)
        rs = _path_rate(a, k, j, r0, 0.5)
        df = abs(M.logit(max(rf, 0.01)) - M.logit(max(rb, 0.01)))
        ds = abs(M.logit(max(rs, 0.01)) - M.logit(max(rb, 0.01)))
        worst = min(worst, df, ds)
        print(f"    a={a} k={k} j={j}: fast={rf:.3f} base={rb:.3f} slow={rs:.3f} "
              f"|dlogit| fast={df:.3f} slow={ds:.3f}")
    assert worst > 0.1, "ABLATION FAILED: P growth does not move the rate"
    # (b) Scenario-level ablation on model-truth data (diagnostic numbers).
    case2, segments2, _ = _model_truth_case()
    t_target2 = M.parse_time(case2["at"]) + timedelta(days=4.5)
    case2["target"] = t_target2.strftime("%Y-%m-%dT%H:%M:%SZ")
    case2["horizon_minutes"] = int(4.5 * 1440)
    ctx2 = M.build_context(case2, segments2)
    med = {}
    for label, scale in (("fast", 1.5), ("base", 1.0), ("slow", 0.5)):
        cols_s = M.run_scenarios(ctx2, "M0", 800, M.SEED, adoption_scale=scale)
        fc_s = M.quantiles_from_scenarios(ctx2, cols_s)
        med[label] = [r["q"][2] for r in fc_s["trophies"]]
    idx = [i for i in range(55) if 0.1 < med["base"][i] < 90]
    d_fast = sum(abs(M.logit(med["fast"][i]) - M.logit(med["base"][i])) for i in idx) / len(idx)
    d_slow = sum(abs(M.logit(med["slow"][i]) - M.logit(med["base"][i])) for i in idx) / len(idx)
    up = sum(1 for i in idx if med["slow"][i] > med["base"][i])
    print(f"  scenario ablation (model-truth, {len(idx)} non-floor): "
          f"mean |dlogit| fast={d_fast:.3f} slow={d_slow:.3f}; "
          f"slow-growth raises the median for {up}/{len(idx)} trophies")
    assert d_slow > 0.01 and up > len(idx) // 2, "scenario ablation: wrong direction"
    print("  Stage 3 OK (ablation passes: P growth moves predicted rates)")
    return True


def test_stage04():
    """Stage 4: Leg A selection + lambda calibration + stress floor, on the
    model-truth long history (enough segments for >=2 origins)."""
    print("== Stage 4: Leg A + lambda + stress floor ==")
    case, segments, truth = _model_truth_case()
    t_target = M.parse_time(case["at"]) + timedelta(days=4.5)
    case["target"] = t_target.strftime("%Y-%m-%dT%H:%M:%SZ")
    case["horizon_minutes"] = int(4.5 * 1440)
    origins = M.leg_a_origins(case, segments)
    print(f"  segments: {len(segments)}, Leg A origins: {origins}")
    assert len(origins) >= 2, "need >=2 origins for calibration"
    octxs = M._origin_contexts(case, segments, origins)
    winner, errs = M.select_variant(octxs)
    print(f"  variant errors (median |logit|): "
          + ", ".join(f"{v}={errs[v]:.3f}" for v in ("M0", "M1", "M2"))
          + f" -> winner {winner}")
    lam, mean = M.calibrate_lambda(octxs, winner)
    print(f"  lambda grid (mean pinball-logit): "
          + ", ".join(f"{l}={mean[l]:.3f}" for l in M.LAMBDA_GRID)
          + f" -> lambda {lam}")
    t0 = time.time()
    fc = M.model_forecast(case, segments, "stage4")
    dt = time.time() - t0
    print(f"  full pipeline in {dt:.2f}s; sample rows:")
    for i in (0, 1, 2, 19):
        print(f"    id{i+1}: q={fc['trophies'][i]['q']} (truth a={truth[i][0]:.2f} "
              f"k={truth[i][1]} j={truth[i][2]})")
    # Validity: 55 rows, q non-decreasing in [0,100].
    assert len(fc["trophies"]) == 55
    for row in fc["trophies"]:
        qs = row["q"]
        assert len(qs) == 5 and all(0.0 <= q <= 100.0 for q in qs)
        assert all(qs[k] <= qs[k + 1] for k in range(4)), f"inverted quantiles {row}"
    print("  Stage 4 OK")
    return True


def main():
    ok = test_stage01()
    ok = test_stage02() and ok
    ok = test_stage03() and ok
    ok = test_stage04() and ok
    print("ALL STAGE TESTS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
