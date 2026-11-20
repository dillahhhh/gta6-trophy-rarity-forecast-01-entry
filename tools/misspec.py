"""Leg C generator with refresh misspecification scenarios (Ting REVISI, Test 2).

A parameterized copy of the model-truth generator in
tools/compare_challenger.py (_legc_case). Same cohort recursion with known
(a, k, j) per trophy, P on the adoption curve, target 4.5 days after the
checkpoint — plus knobs that BREAK the model-truth assumptions, so the
test measures robustness instead of rewarding simulator overfitting:

  seg_hours / n_seg : refresh cadence (6h baseline; 3h fast, 12h slow)
  growth            : multiplier on the P-adoption exponent (1.0 baseline;
                      1.5 fast growth, 0.5 slow growth) — used in BOTH the
                      history and the truth, so the world itself changes
  jump_at / jump_factor : at segment jump_at, multiply every earner count
                      E by jump_factor (a large refresh off the prior
                      trend); the recursion continues from the jumped
                      state, so history and truth stay consistent
  freeze_last_h     : measurement staleness — the last freeze_last_h hours
                      of history reads are frozen at the rates at the
                      freeze point, while the truth keeps evolving
                      (Exophase stops refreshing near the target)
  noise_sigma       : per-read Gaussian noise (percentage points) on the
                      history rates, clamped and re-rounded to 2dp;
                      trophies_earned recomputed. The truth stays clean.

The truth is always the continued cohort recursion from the (possibly
jumped, growth-changed) final state — never the frozen or noised reads.
"""
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

KIT = Path.home() / "workspace/makefun/gta6-trophy-rarity-forecast-01-kit" / "gta6-trophy-rarity-forecast-01-kit"
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]


def _hours_between(a, b):
    return (b - a).total_seconds() / 3600.0


def legc_case_misspec(seed, n_seg=20, seg_hours=6, growth=1.0,
                      jump_at=None, jump_factor=1.0,
                      freeze_last_h=0.0, noise_sigma=0.0,
                      t0=None, target_h=108.0):
    """Model-truth case + true rates at target, with misspecification knobs."""
    p00 = json.loads((KIT / "input" / "p00.in").read_text(encoding="utf-8"))
    trophies = p00["trophies"]
    base = p00["history"][-1]["rates"]
    rng = random.Random(seed)
    truth_params = []
    for r in base:
        truth_params.append((max(r, 0.005),
                             rng.choice([0.005, 0.02, 0.08, 0.3, 1.0]),
                             rng.choice([0.0, 0.001, 0.01, 0.05])))
    t_rel = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
    if t0 is None:
        t0 = datetime(2026, 11, 18, 23, 50, tzinfo=timezone.utc)
    segs = []
    P = 638.0
    E = [P * r / 100.0 for r in base]
    for s in range(n_seg):
        t = t0 + timedelta(hours=seg_hours * s)
        tau = _hours_between(t_rel, t)
        Pnew = math.exp(6.44 + growth * 1.358 * math.log1p(tau))
        for i, (a, k, j) in enumerate(truth_params):
            E[i] = (a / 100) * P + (E[i] - (a / 100) * P) * math.exp(-k * seg_hours)
            E[i] += (j / 100) * (Pnew - P)
        if jump_at is not None and s == jump_at:
            E = [e * jump_factor for e in E]
        rates = [round(min(max(100 * e / Pnew, 0.0), 100.0), 2) for e in E]
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
    # Misspecification as measurement effects on the history only.
    if freeze_last_h and freeze_last_h > 0:
        freeze_point = at - timedelta(hours=freeze_last_h)
        frozen_rates = None
        for h in hist:
            ht = datetime.fromisoformat(h["at"].replace("Z", "+00:00"))
            if ht <= freeze_point:
                frozen_rates = h["rates"]
        if frozen_rates is not None:
            for h in hist:
                ht = datetime.fromisoformat(h["at"].replace("Z", "+00:00"))
                if ht > freeze_point:
                    h["rates"] = list(frozen_rates)
                    h["trophies_earned"] = int(sum(
                        r * h["players_tracked"] / 100 for r in frozen_rates))
    if noise_sigma and noise_sigma > 0:
        nrng = random.Random(seed * 1000 + 7)
        for h in hist:
            noised = []
            for r in h["rates"]:
                v = round(min(max(r + nrng.gauss(0.0, noise_sigma), 0.0), 100.0), 2)
                noised.append(v)
            h["rates"] = noised
            h["trophies_earned"] = int(sum(
                r * h["players_tracked"] / 100 for r in noised))
    t_target = at + timedelta(hours=target_h)
    case = {"schema": "makefun/gta6-trophies-case/v1", "id": f"legc-s{seed}",
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_minutes": int(target_h * 60),
            "target": t_target.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "levels": list(LEVELS),
            "game": {"name": "Grand Theft Auto VI", "platform": "PS5",
                     "release_date": "2026-11-19",
                     "source": "https://www.exophase.com/game/grand-theft-auto-vi-psn/trophies/"},
            "trophies": trophies, "history": hist, "notes": "legc-misspec"}
    # True rates at target: continue the cohort recursion from the final
    # (possibly jumped / growth-changed) state. The truth never sees the
    # freeze or the read noise.
    P = float(segs[-1][1])
    E = [P * r / 100.0 for r in segs[-1][2]]
    t = segs[-1][0]
    out = list(segs[-1][2])
    while t < t_target:
        t_next = min(t + timedelta(hours=seg_hours), t_target)
        tau = _hours_between(t_rel, t_next)
        Pnew = math.exp(6.44 + growth * 1.358 * math.log1p(tau))
        dt = _hours_between(t, t_next)
        out = []
        for i, (a, k, j) in enumerate(truth_params):
            Ei = (a / 100) * P + (E[i] - (a / 100) * P) * math.exp(-k * dt)
            Ei += (j / 100) * (Pnew - P)
            out.append(round(min(max(100 * Ei / Pnew, 0.0), 100.0), 2))
        E = [Pnew * r / 100.0 for r in out]
        P = Pnew
        t = t_next
    return case, out
