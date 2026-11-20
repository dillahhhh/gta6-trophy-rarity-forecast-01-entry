"""gta6-trophy-rarity-forecast-01: joint refresh-aware forecast (Python 3.13, stdlib only).

Forecasts, for each of GTA VI's 55 PS5 trophies, its earn rate on Exophase at
the case's target, as five quantiles (levels 0.05, 0.25, 0.5, 0.75, 0.95).

Design (see DESIGN.md + EXPERIMENTS.md for the evidence and the deviations):
  Stage 0  compress the history into refresh segments (one observation per
           segment, not per read).
  Stage 1  player model: log-time adoption curve + renewal refresh process,
           both fit from the segments at runtime.
  Stage 2  trophy model: per-trophy saturating curve in EXPOSURE time
           X(t) = integral of tracked players / players (the mean age of
           the tracked set), hierarchical shrinkage M0/M1/M2 toward the
           population / tier / hierarchical means, chosen by rolling-origin
           validation (Leg A) at runtime. NO name/chapter grouping.
  Stage 3  joint predictive scenarios (fixed seed): one refresh path, one
           adoption draw and one shared progression shock per scenario;
           quantiles of the RATIO inside each scenario, never Q(E)/Q(P).
  Stage 4  rolling-origin backtest (Leg A) + spread calibration lambda
           (block bootstrap over origins) + Leg C stress floor:
           final spread = max(s_calibrated, s_stress).
  Stage 5  deterministic output; fallback to the starter's persistence
           form whenever the history has < 2 segments or any fit fails.

Rules the program obeys: stdlib only, no network, no clock (every time comes
from the case file), fixed seed, deterministic (two runs byte-identical),
runtime target < 5 s (limit 8 s). No practice/snapshot values are hardcoded:
player levels, refresh times/gaps, trophy rates and asymptotes are all fit
from the case's history at runtime. The only constants are structural
(levels, z-values, seed, scenario counts, clamp bounds, lambda bounds,
functional forms) plus regularisation chosen by the backtest itself.

Grader: `python3 -P -s /bot/main.py` (PYTHONHASHSEED=0) in python:3.13-slim.
Outside it, MAKEFUN_INPUT_DIR / MAKEFUN_OUTPUT_DIR stand in for /input and
/output: mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]
SIGMA = 0.5  # starter random-walk spread, logit units per sqrt day (fallback only)

SEED = 20261121
N_FINAL = 6000       # joint scenarios for the final forecast (tuned by timing)
N_BACKTEST = 800     # joint scenarios per (origin, variant) in the Leg A backtest
N_STRESS = 1500      # joint scenarios per stress variant
LAMBDA_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
LAMBDA_MIN, LAMBDA_MAX = 0.5, 3.0

# Rounding grid of the printed rates: 2 decimals, so a printed rate y means a
# true rate in [y - 0.005, y + 0.005). A change smaller than half the grid is
# invisible, so it cannot mark a refresh.
HALF_GRID = 0.005


def clamp(x: float) -> float:
    return min(99.99, max(0.01, x))


def logit(pct: float) -> float:
    return math.log(clamp(pct) / (100 - clamp(pct)))


def logistic(lg: float) -> float:
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x: float) -> float | int:
    """x rounded to 4 decimals, half up, as JavaScript's Math.round(x * 10000) / 10000.

    A whole number comes back as an int, so json.dumps writes 100 where
    JavaScript writes 100 (not 100.0).
    """
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value.is_integer() else value


def round2_half_up(x: float) -> float:
    """The page's print: 2 decimals, half up (what a scenario's true rate prints as)."""
    return math.floor(x * 100 + 0.5) / 100


def parse_time(s: str) -> datetime:
    """ISO 8601 (UTC 'Z' or a bare date) -> aware UTC datetime. Case data only."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


# ---------------------------------------------------------------- Stage 0 ---
# Compress the history into refresh segments.

def _rate_equal(a, b) -> bool:
    """Two printed rates are the same observation if both are missing, or both
    present and within half the print grid (a smaller true move is invisible)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= HALF_GRID


def segment_history(case: dict) -> list[dict]:
    """Group consecutive reads into segments split at detected refreshes.

    A refresh is detected when players_tracked changes OR any rate changes
    beyond 2-decimal rounding. Within a segment every read is identical: it
    is one observation, not N. Returns segments oldest first, each with the
    segment's start/end datetimes, players P, rates (first read's) and
    trophies_earned. The first segment starts at the first read's slot.
    """
    hist = case["history"]
    segments = []
    cur = None
    for read in hist:
        slot = parse_time(read["slot"])
        if cur is None:
            cur = {"start": slot, "end": slot, "P": read["players_tracked"],
                   "rates": list(read["rates"]), "earned": read["trophies_earned"]}
            continue
        changed = read["players_tracked"] != cur["P"]
        if not changed:
            for a, b in zip(read["rates"], cur["rates"]):
                if not _rate_equal(a, b):
                    changed = True
                    break
        if changed:
            segments.append(cur)
            cur = {"start": slot, "end": slot, "P": read["players_tracked"],
                   "rates": list(read["rates"]), "earned": read["trophies_earned"]}
        else:
            cur["end"] = slot
    if cur is not None:
        segments.append(cur)
    return segments


def exposure_at(segments: list[dict], when: datetime) -> float:
    """X(t) = integral of tracked players from the first segment start to t,
    divided by the players at t (hours). The mean age of the tracked set if
    every player joined at or after the first read; the exposure clock the
    trophy curves run on. Uses the observed step function (P constant inside
    a segment). At the first segment start X = 0 by construction."""
    if not segments:
        return 0.0
    t0 = segments[0]["start"]
    if when <= t0:
        return 0.0
    total = 0.0
    for i, seg in enumerate(segments):
        seg_start = seg["start"]
        seg_end = segments[i + 1]["start"] if i + 1 < len(segments) else when
        if seg_end > when:
            seg_end = when
        if seg_end > seg_start:
            total += seg["P"] * hours_between(seg_start, seg_end)
        if seg_end >= when:
            break
    p_now = segments[-1]["P"]
    # P at `when` is the last segment's P (when is inside/after the last segment).
    return total / p_now if p_now > 0 else 0.0


# ---------------------------------------------------------------- Stage 1 ---
# Player model: adoption + refresh process, fit on segments only.

def fit_adoption(segments: list[dict], t_release: datetime):
    """Fit log P(t) = a + b * ln(1 + (t - t_release)/1h) by least squares on
    segment starts. Returns dict(a, b, var_a, var_b, cov_ab, sigma, max_daily_growth)
    or None when fewer than 2 segments have distinct times. The form is
    concave in log-time: it can keep growing but cannot explode."""
    if len(segments) < 2:
        return None
    xs, ys = [], []
    for seg in segments:
        tau = max(hours_between(t_release, seg["start"]), -0.9)
        xs.append(math.log1p(tau))
        ys.append(math.log(seg["P"]))
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    dof = max(n - 2, 1)
    sigma2 = sum(r * r for r in resid) / dof
    var_b = sigma2 / sxx
    var_a = sigma2 * (1.0 / n + mx * mx / sxx)
    cov_ab = -sigma2 * mx / sxx
    # Largest daily growth observed between segments (cap for extrapolation:
    # no re-acceleration beyond what was seen). Pairs must be >= 6 h apart:
    # extrapolating a 1-2 h jump to a full day would fabricate a cap in the
    # thousands at launch, when the growth really was explosive.
    max_daily = 0.0
    for i in range(len(segments) - 1):
        dh = hours_between(segments[i]["start"], segments[i + 1]["start"])
        if dh >= 6.0 and segments[i]["P"] > 0:
            g = (segments[i + 1]["P"] / segments[i]["P"]) ** (24.0 / dh)
            if g > max_daily:
                max_daily = g
    return {"a": a, "b": b, "var_a": max(var_a, 0.0), "var_b": max(var_b, 0.0),
            "cov_ab": cov_ab, "sigma": math.sqrt(max(sigma2, 0.0)),
            "max_daily_growth": max_daily}


def adoption_p(fit: dict, tau_hours: float) -> float:
    """P at tau hours after release under the fitted adoption curve."""
    tau = max(tau_hours, -0.9)
    return math.exp(fit["a"] + fit["b"] * math.log1p(tau))


def capped_b(fit: dict, tau_target: float) -> float:
    """Adoption slope capped so the extrapolated daily growth at the target
    never exceeds the largest daily growth observed between segments."""
    b = fit["b"]
    cap = fit["max_daily_growth"]
    if cap <= 1.0 or b <= 0:
        return b
    # Daily growth of the log-time form at tau: ((1+tau+24)/(1+tau))^b.
    base = math.log((1.0 + tau_target + 24.0) / (1.0 + tau_target))
    if base <= 1e-12:
        return b
    b_cap = math.log(cap) / base
    return min(b, b_cap)


def fit_gaps(segments: list[dict]):
    """Fit the refresh-gap distribution on gaps between segment starts (hours).
    Returns ('lognormal', meanlog, sdlog) when >= 3 gaps with real spread,
    else ('empirical', [gaps])."""
    gaps = [hours_between(segments[i]["start"], segments[i + 1]["start"])
            for i in range(len(segments) - 1)]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) >= 3:
        logs = [math.log(g) for g in gaps]
        meanlog = sum(logs) / len(logs)
        var = sum((x - meanlog) ** 2 for x in logs) / len(logs)
        sdlog = math.sqrt(var)
        if sdlog > 0.05:
            return ("lognormal", meanlog, sdlog)
    return ("empirical", gaps)


def _draw_gap(rng: random.Random, gap_model, min_hours: float) -> float:
    """One refresh gap (hours), conditioned to exceed min_hours (the age of
    the last refresh at the checkpoint for the first draw; 0 afterwards)."""
    kind = gap_model[0]
    if kind == "lognormal":
        _, meanlog, sdlog = gap_model
        nd = statistics.NormalDist()
        # P(gap <= min) under the lognormal; draw uniformly above it.
        if min_hours > 0:
            p_lo = nd.cdf((math.log(min_hours) - meanlog) / sdlog) if min_hours > 0 else 0.0
        else:
            p_lo = 0.0
        if p_lo >= 0.999999:
            return min_hours * 1.5 + 1.0
        u = p_lo + rng.random() * (1.0 - p_lo)
        return math.exp(meanlog + sdlog * nd.inv_cdf(min(max(u, 1e-12), 1 - 1e-12)))
    gaps = gap_model[1]
    if not gaps:
        return 24.0
    # Empirical: resample until the gap exceeds the required minimum.
    for _ in range(50):
        g = rng.choice(gaps)
        if g > min_hours:
            return g
    return max(gaps) * 1.5 + 1.0


def simulate_refresh_times(rng: random.Random, gap_model, t_last: datetime,
                           t_checkpoint: datetime, t_target: datetime) -> list[datetime]:
    """Refresh times after the checkpoint from the fitted renewal process,
    conditioned on the age of the last refresh. Returns every refresh up to
    (and including the first one after) the target; may be empty."""
    age = hours_between(t_last, t_checkpoint)
    times = []
    t = t_checkpoint
    gap = _draw_gap(rng, gap_model, age)
    from datetime import timedelta
    while True:
        t = t + timedelta(hours=gap)
        times.append(t)
        if t >= t_target:
            break
        gap = _draw_gap(rng, gap_model, 0.0)
        if len(times) > 1000:
            break
    return times


# ------------------------------------------------------- starter (fallback) ---

def starter_forecast(case: dict) -> dict:
    """The house baseline, number for number: persistence + random-walk spread.
    Used whenever the history has < 2 segments or any fit fails, so the case
    is always valid."""
    if case["levels"] != LEVELS:
        raise ValueError(f"levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError("no history")
    last = case["history"][-1]
    spread = SIGMA * math.sqrt(case["horizon_minutes"] / 1440)
    rows = []
    for i, trophy in enumerate(case["trophies"]):
        rate = last["rates"][i]
        lg = logit(rate if rate is not None else 0)
        rows.append({"id": trophy["id"],
                     "q": [round4(100 / (1 + math.exp(-(lg + z * spread)))) for z in Z]})
    return {"trophies": rows}


def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError(f"{name} has no history")
    # Stage 0: segment the history. One segment (frozen history, as in the
    # practice) carries no time variation, so no fit is possible: fall back.
    segments = segment_history(case)
    if len(segments) < 2:
        return starter_forecast(case)
    try:
        return model_forecast(case, segments, name)
    except Exception:
        # Any fit failure falls back to the starter form: a valid honest
        # forecast beats a crash (a crash scores as persistence anyway).
        return starter_forecast(case)


def model_forecast(case: dict, segments: list[dict], name: str) -> dict:
    # Stages 2-4 land in the next commits; until then the starter form.
    return starter_forecast(case)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
