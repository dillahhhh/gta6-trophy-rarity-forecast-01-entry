#!/usr/bin/env python3
"""Independent simulator for the rich path (final-case forecaster).

A DIFFERENT model family from the cohort model (M0) and from the
Theil-Sen forecaster, so a good score here is not confirmation bias:

  * Players P(t) grow by a power-law adoption curve, sampled only at
    refreshes (the page only changes at refreshes).
  * Refresh times are a renewal process (LogNormal gaps, hours apart);
    the page is frozen between refreshes.
  * Trophy earners follow a HAZARD model: each trophy i has a per-hour
    hazard h_i (a player without it earns it at that rate) and a
    joiner rate j_i (a new player already has it with that probability).
    Between refreshes, the fraction F_i with the trophy evolves as
    dF/dt = h_i * (1 - F); at a refresh, new players join and F_i jumps
    toward j_i. The asymptote is 1 (everyone eventually earns it), not
    a fitted asymptote -- unlike the cohort model's a_i.
  * Observed rates are the true rates rounded to 2 decimals (as the
    real page prints them). No extra noise by default.

Parameters h_i / j_i are anchored to real data (the transcribed page
rates and the live snapshot in data/rank1-sources.json) so the
simulator's levels are realistic; the dynamics are hazard-based.

The truth at the target is the page's rates as of the last refresh
at/before the target (the RULES definition of the answer).

Scenarios stress the forecaster: refresh fast/slow, adoption
fast/slow, read noise, and a mid-history hazard jump. Knob selection
uses seeds 1..5; held-out evaluation uses seeds 6..10.
"""

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone

RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
KIT = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input/p00.in"
SOURCES = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-entry/data/rank1-sources.json"
READ_MINUTES = 10  # house recorder cadence


def _load_meta():
    kit = json.load(open(KIT))
    src = json.load(open(SOURCES))
    return kit, src


def _params(seed, scenario, kit, src):
    """Per-trophy (h_i, j_i) and process params for a scenario."""
    rng = random.Random(seed * 7919 + 13)
    page = src["page_rates_refreshed_2026_11_19_0916Z"]
    live = src["live_snapshot_rates_2026_11_20_0907Z"]
    med = sorted(page)[27]
    h = []
    j = []
    for i in range(55):
        # Hazard (existing players earning it): SLOW. The real pattern is
        # dilution-dominated -- most rates fall as new players join -- so
        # the hazard must not outrun the joiner inflow.
        hi = 0.0003 * (max(page[i], 0.05) / med) ** 0.5
        hi = min(0.003, max(0.0001, hi))
        # Jitter the hazard so trophies are not all identical.
        hi *= math.exp(rng.gauss(0.0, 0.35))
        hi = min(0.005, max(0.0001, hi))
        # Joiner rate: new players have it at the LIVE population rate.
        # The live snapshot (P > 80k) is dominated by new joiners, so its
        # rates are the best estimate of what a fresh player has. Early
        # story trophies (id 3, id 20) are high; late trophies are ~0.01%.
        ji = min(0.95, max(0.0001, live[i] / 100.0))
        h.append(hi)
        j.append(ji)
    p_pow = {"base": 3.0, "growth_fast": 4.0, "growth_slow": 2.0}.get(
        scenario, 3.0)
    gap_med = {"base": 5.0, "refresh_fast": 2.0, "refresh_slow": 12.0}.get(
        scenario, 5.0)
    return {
        "h": h, "j": j, "p_pow": p_pow, "gap_med": gap_med,
        "gap_sigma": 0.6, "noise_pp": 0.02 if scenario == "noise" else 0.0,
        "jump": scenario == "jump",
    }


def gen_case(seed, scenario="base", history_days=2.7, horizon_days=4.5208):
    """Generate a synthetic final-shaped case + the truth at the target.

    Returns (case_dict, truth_rates). The case's history spans
    history_days from release, reads every 10 minutes, rates frozen
    between refreshes. The checkpoint is the history end; the target is
    checkpoint + horizon_days.
    """
    kit, src = _load_meta()
    prm = _params(seed, scenario, kit, src)
    rng = random.Random(seed * 104729 + 7)
    h = prm["h"]
    j = prm["j"]
    # Refresh times (renewal, LogNormal gaps), from release onward.
    end = RELEASE + timedelta(days=history_days + horizon_days + 0.5)
    refreshes = []
    t = RELEASE + timedelta(hours=rng.uniform(1.0, 6.0))
    while t < end:
        refreshes.append(t)
        gap = rng.lognormvariate(math.log(prm["gap_med"]), prm["gap_sigma"])
        t = t + timedelta(hours=max(0.5, gap))
    # State at release: P0 = 638 (as the real frozen page), F from page.
    P0 = 638.0
    F = [min(0.99, max(0.001, r / 100.0)) for r in src["frozen_rates_last_read"]]
    # Special: trophy id 2 is at 100% from the start and stays there.
    F[1] = 1.0
    # Hazard jump scenario: trophy id 20's hazard doubles mid-history.
    jump_at = RELEASE + timedelta(days=history_days * 0.5)
    # Walk refreshes, evolving F and P between them.
    # P(t) = P0 * (1 + t_hours/24) ** p_pow, sampled at refresh times.
    states = []  # (time, P, [rates])
    prev_t = RELEASE
    prev_P = P0
    for rt in refreshes:
        dt_h = (rt - prev_t).total_seconds() / 3600.0
        # Hazard evolution between refreshes (exact for constant h).
        for i in range(55):
            if i == 1:
                continue
            hi = h[i]
            if prm["jump"] and rt >= jump_at and i == 19:
                hi *= 2.0
            F[i] = 1.0 - (1.0 - F[i]) * math.exp(-hi * dt_h)
        # New players join at this refresh; F jumps toward j_i.
        t_h = (rt - RELEASE).total_seconds() / 3600.0
        P = P0 * (1.0 + t_h / 24.0) ** prm["p_pow"]
        dP = max(0.0, P - prev_P)
        if P > 0:
            for i in range(55):
                if i == 1:
                    F[i] = 1.0
                    continue
                E = F[i] * prev_P + dP * j[i]
                F[i] = E / P
        states.append((rt, P, [100.0 * f for f in F]))
        prev_t, prev_P = rt, P
    # Checkpoint / target.
    checkpoint = RELEASE + timedelta(days=history_days)
    target = checkpoint + timedelta(days=horizon_days)
    # History: reads every 10 min from release to checkpoint, showing the
    # last refresh's state (frozen between refreshes).
    history = []
    read_t = RELEASE
    si = 0
    # Find the last refresh at/before each read.
    cur = None
    for s_time, s_P, s_rates in states:
        if s_time <= read_t:
            cur = (s_time, s_P, s_rates)
    # Walk reads.
    si = 0
    cur_rates = None
    cur_P = P0
    cur_earn = 0
    while read_t <= checkpoint:
        while si < len(states) and states[si][0] <= read_t:
            cur_P = states[si][1]
            cur_rates = states[si][2]
            si += 1
        if cur_rates is None:
            # Before the first refresh: use the release state.
            cur_rates = [100.0 * f for f in F]
            cur_rates[1] = 100.0
        rates = []
        for i, r in enumerate(cur_rates):
            if i == 1:
                rates.append(100.0)
                continue
            v = r
            if prm["noise_pp"]:
                v += rng.gauss(0.0, prm["noise_pp"])
            rates.append(round(max(0.0, min(100.0, v)), 2))
        earn = int(round(sum(r / 100.0 * cur_P for r in rates if r < 100.0)))
        history.append({
            "slot": read_t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "at": read_t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "players_tracked": int(round(cur_P)),
            "trophies_earned": earn,
            "rates": rates,
        })
        read_t += timedelta(minutes=READ_MINUTES)
    # Truth: the last refresh's rates at/before the target.
    truth = None
    for s_time, s_P, s_rates in states:
        if s_time <= target:
            truth = [100.0 if i == 1 else round(r, 2) for i, r in enumerate(s_rates)]
    if truth is None:
        truth = [100.0 if i == 1 else round(r, 2) for i, r in enumerate(states[-1][2])]
    case = {
        "id": "f00-sim-%s-%d" % (scenario, seed),
        "at": checkpoint.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_minutes": int(round(horizon_days * 1440)),
        "target": target.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "game": kit["game"],
        "trophies": kit["trophies"],
        "history": history,
        "notes": "synthetic (r1_sim, scenario %s, seed %d)" % (scenario, seed),
    }
    return case, truth


def n_segments(case):
    """Count refresh segments in a case's history (effective observations)."""
    segs = 1
    prev = None
    for read in case["history"]:
        key = (read["players_tracked"], tuple(read["rates"]))
        if prev is not None and key != prev:
            segs += 1
        prev = key
    return segs


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    scenario = sys.argv[2] if len(sys.argv) > 2 else "base"
    case, truth = gen_case(seed, scenario)
    print("case %s: %d reads, %d segments, P last %d, horizon %d min" % (
        case["id"], len(case["history"]), n_segments(case),
        case["history"][-1]["players_tracked"], case["horizon_minutes"]))
    print("truth (first 10):", truth[:10])
    print("last read rates (first 10):", case["history"][-1]["rates"][:10])
    print("truth sum %.2f, last-read sum %.2f" % (
        sum(truth), sum(case["history"][-1]["rates"])))
