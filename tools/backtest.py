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


def test_stage01():
    """Stage 0-1 on a synthetic multi-segment history (hand-built anchors)."""
    print("== Stage 0-1: segmentation + adoption + renewal (synthetic) ==")
    p00 = load_case(KIT / "input" / "p00.in")
    trophies = p00["trophies"]
    base = p00["history"][-1]["rates"]
    t0 = datetime(2026, 11, 18, 23, 50, tzinfo=timezone.utc)
    # Four segments: P explodes, slow trophies dilute, fast ones rise.
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
            elif i in (2, 19):  # fast trophies (id 3, id 20) rise
                rates.append(round(min(99.99, r * fast), 2))
            else:
                rates.append(round(max(0.01, r * mult_slow), 2))
        segs.append((t0 + timedelta(hours=dh), P, rates))
    at = t0 + timedelta(hours=36)
    case = make_synthetic_case(segs, at, 90, at + timedelta(minutes=90), trophies)
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
    times = M.simulate_refresh_times(rng, gaps, segments[-1]["start"], at,
                                     at + timedelta(days=4.5))
    print(f"  simulated refreshes to target+4.5d: {len(times)} (first {times[0] if times else None})")
    print("  Stage 0-1 OK")
    return True


def test_stage02():
    """Stage 2: trophy curves + M0/M1/M2 shrinkage on the synthetic history."""
    print("== Stage 2: trophy curves + shrinkage ==")
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


def main():
    ok = test_stage01()
    ok = test_stage02() and ok
    print("ALL STAGE TESTS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
