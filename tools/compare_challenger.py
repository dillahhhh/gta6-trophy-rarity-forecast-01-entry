"""Leg C head-to-head: challenger rich path vs M0 vs persistence.

Self-contained Leg C generator (same model-truth setup that gave M0 0.219
vs starter 0.259 on seed 7): cohort recursion with known (a, k, j) per
trophy, P on the adoption curve, 20 x 6h segments, target 4.5 days after
the checkpoint. The challenger runs in-process (module import, knobs
monkey-patched per variant); M0 runs as a black-box CLI subprocess from a
temp copy of main:main.py (never opened); persistence carries the last
rate forward with sigma 0.5.

Scoring is the §1 pinball loss on the logit scale (with the x2 factor).

Usage:
  python3 tools/compare_challenger.py --seeds 7,11 --sweep
  python3 tools/compare_challenger.py --seeds 7,11,13,23,29 --m0
  python3 tools/compare_challenger.py --seeds 7 --damping 0.3 --prior 5
"""
import argparse
import importlib.util
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIT = Path.home() / "workspace/makefun/gta6-trophy-rarity-forecast-01-kit" / "gta6-trophy-rarity-forecast-01-kit"
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CH = _load_module("challenger_main", REPO / "main.py")


def logit(pct):
    p = min(max(float(pct), 0.01), 99.99) / 100.0
    return math.log(p / (1.0 - p))


def score_forecast(forecast, truth_rates):
    rows = {r["id"]: r["q"] for r in forecast["trophies"]}
    total, n = 0.0, 0
    for i, y in enumerate(truth_rates):
        q = rows[i + 1]
        for tau, qv in zip(LEVELS, q):
            d = logit(y) - logit(qv)
            total += 2.0 * max(tau * d, (tau - 1.0) * d)
            n += 1
    return total / n


def _hours_between(a, b):
    return (b - a).total_seconds() / 3600.0


def _legc_case(seed, n_seg=20, seg_hours=6):
    """Model-truth case + true rates at the 4.5-day target."""
    p00 = json.loads((KIT / "input" / "p00.in").read_text(encoding="utf-8"))
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
        tau = _hours_between(t_rel, t)
        Pnew = math.exp(6.44 + 1.358 * math.log1p(tau))
        rates = []
        for i, (a, k, j) in enumerate(truth):
            E[i] = (a / 100) * P + (E[i] - (a / 100) * P) * math.exp(-k * seg_hours)
            E[i] += (j / 100) * (Pnew - P)
            rates.append(round(min(max(100 * E[i] / Pnew, 0.0), 100.0), 2))
        segs.append((t, int(round(Pnew)), rates))
        P = Pnew
    at = t0 + timedelta(hours=seg_hours * n_seg)
    # Expand segments into 30-min reads.
    hist = []
    for j, (slot, Pseg, rates) in enumerate(segs):
        end = segs[j + 1][0] if j + 1 < len(segs) else at
        t = slot
        while t < end:
            hist.append({"slot": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "players_tracked": Pseg,
                         "trophies_earned": int(sum(r * Pseg / 100 for r in rates)),
                         "rates": list(rates)})
            t += timedelta(minutes=30)
    t_target = at + timedelta(days=4.5)
    case = {"schema": "makefun/gta6-trophies-case/v1", "id": f"legc-s{seed}",
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_minutes": int(4.5 * 1440),
            "target": t_target.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "levels": list(LEVELS),
            "game": {"name": "Grand Theft Auto VI", "platform": "PS5",
                     "release_date": "2026-11-19",
                     "source": "https://www.exophase.com/game/grand-theft-auto-vi-psn/trophies/"},
            "trophies": trophies, "history": hist, "notes": "legc-model-truth"}
    # True rates at target: continue the cohort recursion.
    P = float(segs[-1][1])
    E = [P * r / 100.0 for r in segs[-1][2]]
    t = segs[-1][0]
    out = list(segs[-1][2])
    while t < t_target:
        t_next = min(t + timedelta(hours=seg_hours), t_target)
        tau = _hours_between(t_rel, t_next)
        Pnew = math.exp(6.44 + 1.358 * math.log1p(tau))
        dt = _hours_between(t, t_next)
        out = []
        for i, (a, k, j) in enumerate(truth):
            Ei = (a / 100) * P + (E[i] - (a / 100) * P) * math.exp(-k * dt)
            Ei += (j / 100) * (Pnew - P)
            out.append(round(min(max(100 * Ei / Pnew, 0.0), 100.0), 2))
        E = [Pnew * r / 100.0 for r in out]
        P = Pnew
        t = t_next
    return case, out


def run_challenger(case, damping, prior, tau_grid, mult_grid):
    CH.RICH_TAU_GRID = tuple(tau_grid)
    CH.RICH_MULT_GRID = tuple(mult_grid)
    t0 = time.time()
    # NOTE: _rich_forecast binds its damping/prior defaults at def time, so
    # pass the variant knobs explicitly (forecast_case would use the bound
    # spec defaults). Leg C cases never trigger the frozen gate.
    if CH._is_frozen_case(case):
        fc = CH.forecast_case(case, "compare")
    else:
        fc = CH._rich_forecast(case, damping, prior)
    return fc, time.time() - t0


def run_m0(case):
    """Black-box CLI run of the submitted M0 (temp copy, never opened)."""
    tmp = Path("/tmp/m0_main.py")
    if not tmp.exists():
        r = subprocess.run(["git", "show", "main:main.py"],
                           cwd=str(REPO), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("git show main:main.py failed")
        tmp.write_text(r.stdout, encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="m0-legc-") as td:
        in_dir, out_dir = Path(td) / "in", Path(td) / "out"
        in_dir.mkdir()
        (in_dir / f"{case['id']}.in").write_text(json.dumps(case), encoding="utf-8")
        env = dict(os.environ)
        env["MAKEFUN_INPUT_DIR"] = str(in_dir)
        env["MAKEFUN_OUTPUT_DIR"] = str(out_dir)
        t0 = time.time()
        r = subprocess.run([sys.executable, str(tmp)], env=env,
                           capture_output=True, text=True, timeout=120)
        dt = time.time() - t0
        if r.returncode != 0:
            raise RuntimeError(f"M0 exited {r.returncode}: {r.stdout}{r.stderr}")
        return json.loads((out_dir / f"{case['id']}.out").read_text(encoding="utf-8")), dt


def run_persistence(case):
    last = case["history"][-1]["rates"]
    rows = []
    for t, r in zip(case["trophies"], last):
        if r >= 99.99:
            rows.append({"id": t["id"], "q": CH._quantiles_from(99.99, 0.3)})
        else:
            rows.append({"id": t["id"], "q": CH._quantiles_from(r, 0.5)})
    return {"trophies": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="7")
    ap.add_argument("--damping", type=float, default=0.4)
    ap.add_argument("--prior", type=int, default=10)
    ap.add_argument("--tau-grid", default="6,12,24,48")
    ap.add_argument("--mult-grid", default="0.75,1.0,1.5")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--m0", action="store_true")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    tau_grid = [int(x) for x in args.tau_grid.split(",")]
    mult_grid = [float(x) for x in args.mult_grid.split(",")]
    cases = {}
    for s in seeds:
        cases[s] = _legc_case(s)

    if args.sweep:
        print("== Variant sweep (challenger only) ==")
        print("damping prior | " + " | ".join(f"seed {s}" for s in seeds) + " | mean")
        results = []
        for damp in (0.3, 0.4, 0.5, 0.6, 0.7, 1.0):
            for prior in (2, 5, 10, 20):
                losses = []
                for s in seeds:
                    fc, _ = run_challenger(cases[s][0], damp, prior, tau_grid, mult_grid)
                    losses.append(score_forecast(fc, cases[s][1]))
                mean = sum(losses) / len(losses)
                results.append((mean, damp, prior, losses))
                print(f"  {damp}  {prior:>5} | " +
                      " | ".join(f"{l:.4f}" for l in losses) + f" | {mean:.4f}")
        results.sort()
        print(f"  BEST: damping={results[0][1]} prior={results[0][2]} mean={results[0][0]:.4f}")
        return 0

    print("== Head-to-head (Leg C, 4.5d horizon) ==")
    print("seed | challenger (s) | M0 (s) | persistence | notes")
    ch_losses, m0_losses, pe_losses = [], [], []
    for s in seeds:
        case, truth = cases[s]
        fc, dt = run_challenger(case, args.damping, args.prior, tau_grid, mult_grid)
        cl = score_forecast(fc, truth)
        ch_losses.append(cl)
        line = f"  {s} | {cl:.4f} ({dt:.1f}s)"
        if args.m0:
            fm, dtm = run_m0(case)
            ml = score_forecast(fm, truth)
            m0_losses.append(ml)
            line += f" | {ml:.4f} ({dtm:.1f}s)"
            fp = run_persistence(case)
            pl = score_forecast(fp, truth)
            pe_losses.append(pl)
            line += f" | {pl:.4f}"
        print(line)
    print(f"MEAN challenger: {sum(ch_losses)/len(ch_losses):.4f} "
          f"(damping={args.damping} prior={args.prior} tau={tau_grid} mult={mult_grid})")
    if m0_losses:
        print(f"MEAN M0:         {sum(m0_losses)/len(m0_losses):.4f}")
        print(f"MEAN persistence:{sum(pe_losses)/len(pe_losses):.4f}")
        d = sum(ch_losses)/len(ch_losses) - sum(m0_losses)/len(m0_losses)
        print(f"challenger - M0: {d:+.4f} ({'challenger BETTER' if d < 0 else 'M0 BETTER'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
