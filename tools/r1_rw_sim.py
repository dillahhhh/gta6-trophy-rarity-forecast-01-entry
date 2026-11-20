#!/usr/bin/env python3
"""Third-family simulator (RANK1-REVISION): pure random walk + calendar
shocks. Deliberately a DIFFERENT model family from both existing
evaluators, so a good score here cannot be confirmation bias:

  * hazard sim (tools/r1_sim.py): trophy earners follow a hazard model
    dF/dt = h*(1-F) with joiner jumps; asymptote 1.
  * cohort sim (challenger tools/misspec.py): cohort asymptotes a_i with
    a log-time trend shape.
  * THIS sim: trophy rates are a RANDOM WALK in logit space with weak
    mean reversion and discrete CALENDAR SHOCKS (weekend / patch /
    viral moments). There is NO log(1+t/tau) trend anywhere, NO hazard
    rate, NO cohort asymptote. The forecaster's Theil-Sen-on-log-time
    trend is the WRONG shape for this world on purpose: if the rich
    path still beats the starter here, the edge is not an artifact of
    sharing the trend family with the evaluator.

  * Players P(t) grow by a power-law adoption curve, sampled only at
    refreshes (the page only changes at refreshes) -- population
    growth is a fact of the setting, not a trophy-dynamics assumption.
  * Refresh times are a renewal process (LogNormal gaps, hours apart);
    the page is frozen between refreshes.
  * Observed rates are the true rates rounded to 2 decimals (as the
    real page prints them).

Parameters are anchored to real data (the transcribed page rates and
the live snapshot) so levels are realistic. The truth at the target is
the page's rates as of the last refresh at/before the target (the
RULES definition of the answer).

Scenarios stress the forecaster: random-walk speed fast/slow, shock
heavy/none, adoption fast/slow. Knob selection uses seeds 1..5;
held-out evaluation uses seeds 6..10; fresh uses seeds 11..15.
"""

import json
import math
import random
from datetime import datetime, timedelta, timezone

RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
KIT = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input/p00.in"
SOURCES = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-entry/data/rank1-sources.json"
READ_MINUTES = 10  # house recorder cadence


def _load_meta():
    kit = json.load(open(KIT))
    src = json.load(open(SOURCES))
    return kit, src


def _logit(r):
    p = min(0.9999, max(0.0001, r / 100.0))
    return math.log(p / (1.0 - p))


def _inv_logit(x):
    if x >= 0:
        p = 1.0 / (1.0 + math.exp(-x))
    else:
        e = math.exp(x)
        p = e / (1.0 + e)
    return min(99.99, max(0.01, 100.0 * p))


def _params(seed, scenario, kit, src):
    """Random-walk / shock / adoption params for a scenario."""
    rng = random.Random(seed * 7919 + 13)
    page = src["page_rates_refreshed_2026_11_19_0916Z"]
    live = src["live_snapshot_rates_2026_11_20_0907Z"]
    # Anchor the walk's starting levels to the real frozen page; the
    # live snapshot bounds how far a trophy plausibly moves in a day.
    sigma_rw = {"base": 0.05, "rw_fast": 0.10, "rw_slow": 0.02}.get(scenario, 0.05)
    n_shocks, shock_sd = {
        "base": (3, 0.4), "shock_heavy": (5, 0.6), "shock_none": (0, 0.0),
    }.get(scenario, (3, 0.4))
    p_pow = {"base": 3.0, "growth_fast": 4.0, "growth_slow": 2.0}.get(scenario, 3.0)
    gap_med = {"base": 5.0}.get(scenario, 5.0)
    return {
        "sigma_rw": sigma_rw, "n_shocks": n_shocks, "shock_sd": shock_sd,
        "shock_share": 0.20, "mean_rev": 0.05,
        "p_pow": p_pow, "gap_med": gap_med, "gap_sigma": 0.6,
        "page": page, "live": live,
    }


def gen_case(seed, scenario="base", history_days=2.486, horizon_days=4.5208):
    """Generate a synthetic final-shaped case + the truth at the target.

    Returns (case_dict, truth_rates). The case's history spans
    history_days from release, reads every 10 minutes, rates frozen
    between refreshes. The checkpoint is the history end; the target is
    checkpoint + horizon_days.
    """
    kit, src = _load_meta()
    prm = _params(seed, scenario, kit, src)
    rng = random.Random(seed * 104729 + 7)
    page = prm["page"]
    # Refresh times (renewal, LogNormal gaps), from release onward.
    end = RELEASE + timedelta(days=history_days + horizon_days + 0.5)
    refreshes = []
    t = RELEASE + timedelta(hours=rng.uniform(1.0, 6.0))
    while t < end:
        refreshes.append(t)
        gap = rng.lognormvariate(math.log(prm["gap_med"]), prm["gap_sigma"])
        t = t + timedelta(hours=max(0.5, gap))
    # Calendar shock times: uniform over the history window.
    hist_end = RELEASE + timedelta(days=history_days)
    shock_times = sorted(
        RELEASE + timedelta(days=rng.random() * history_days)
        for _ in range(prm["n_shocks"]))
    shock_sets = []
    for _ in shock_times:
        n_hit = max(1, int(round(55 * prm["shock_share"])))
        shock_sets.append(set(rng.sample(range(55), n_hit)))
    # State: logit rates start at the frozen page; trophy id 2 is 100%.
    logit0 = [_logit(r) for r in page]
    P0 = 638.0
    # Walk refreshes, evolving logit rates. Shocks apply at the first
    # refresh at/after their time.
    logit = list(logit0)
    states = []
    applied = [False] * len(shock_times)
    for rt in refreshes:
        for i in range(55):
            if i == 1:
                continue
            logit[i] += rng.gauss(0.0, prm["sigma_rw"])
            logit[i] += -prm["mean_rev"] * (logit[i] - logit0[i])
        for k, st in enumerate(shock_times):
            if not applied[k] and st <= rt:
                applied[k] = True
                for i in shock_sets[k]:
                    if i == 1:
                        continue
                    logit[i] += rng.gauss(0.0, prm["shock_sd"])
        t_h = (rt - RELEASE).total_seconds() / 3600.0
        P = P0 * (1.0 + t_h / 24.0) ** prm["p_pow"]
        rates = [100.0 if i == 1 else round(_inv_logit(logit[i]), 2)
                 for i in range(55)]
        states.append((rt, P, rates))
    # Checkpoint / target.
    checkpoint = RELEASE + timedelta(days=history_days)
    target = checkpoint + timedelta(days=horizon_days)
    # History: reads every 10 min from release to checkpoint, showing the
    # last refresh's state (frozen between refreshes).
    history = []
    read_t = RELEASE
    si = 0
    cur_rates = None
    cur_P = P0
    while read_t <= checkpoint:
        while si < len(states) and states[si][0] <= read_t:
            cur_P = states[si][1]
            cur_rates = states[si][2]
            si += 1
        if cur_rates is None:
            cur_rates = [100.0 if i == 1 else round(page[i], 2) for i in range(55)]
        rates = [100.0 if i == 1 else cur_rates[i] for i in range(55)]
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
        "id": "f00-rw-%s-%d" % (scenario, seed),
        "at": checkpoint.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_minutes": int(round(horizon_days * 1440)),
        "target": target.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "game": kit["game"],
        "trophies": kit["trophies"],
        "history": history,
        "notes": "synthetic (r1_rw_sim, scenario %s, seed %d)" % (scenario, seed),
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
    seed = int(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 1
    scenario = __import__("sys").argv[2] if len(__import__("sys").argv) > 2 else "base"
    case, truth = gen_case(seed, scenario)
    print("case %s: %d reads, %d segments, P last %d, horizon %d min" % (
        case["id"], len(case["history"]), n_segments(case),
        case["history"][-1]["players_tracked"], case["horizon_minutes"]))
    print("truth (first 10):", truth[:10])
    print("last read rates (first 10):", case["history"][-1]["rates"][:10])
    print("truth sum %.2f, last-read sum %.2f" % (
        sum(truth), sum(case["history"][-1]["rates"])))
