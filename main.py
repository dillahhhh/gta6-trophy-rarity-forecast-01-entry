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
  Stage 4  rolling-origin backtest (Leg A, dev only) + spread calibration
           lambda (block bootstrap over origins). PRODUCTION IS LOCKED:
           variant M0 + lambda 1.25, no stress floor (Ting finalization
           20 Nov 2026; see model_forecast and EXPERIMENTS.md). The Leg A
           machinery is kept for dev experiments, not run in production.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]
SIGMA = 0.5  # starter random-walk spread, logit units per sqrt day (fallback only)

SEED = 20261121
N_FINAL = 2000      # joint scenarios for the final forecast (tuned by timing)
N_BACKTEST = 250    # joint scenarios per Leg A origin (lambda calibration)
N_STRESS = 250      # joint scenarios per stress run
LAMBDA_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
MAX_ORIGINS = 3     # Leg A uses at most this many origins (cost-bounded)
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
    while True:
        t = t + timedelta(hours=gap)
        times.append(t)
        if t >= t_target:
            break
        gap = _draw_gap(rng, gap_model, 0.0)
        if len(times) > 1000:
            break
    return times


def _logit_raw(pct: float) -> float:
    """logit without the starter's clamp (for asymptotes and interval edges)."""
    p = min(99.999999, max(0.000001, pct))
    return math.log(p / (100.0 - p))


def _rate_interval(y):
    """Printed rate y -> (lo, hi) logit interval of the true rate, or None.
    A printed rate y means a true rate in [y - 0.005, y + 0.005): the
    2-decimal rounding is observation noise, carried explicitly — including
    0.00% prints (true rate < 0.005%) and 100% prints."""
    if y is None:
        return None
    lo = max(y - HALF_GRID, 0.0001)
    hi = min(y + HALF_GRID, 99.9999)
    return (_logit_raw(lo), _logit_raw(hi))


def _censored_resid(fit_logit: float, interval) -> float:
    lo, hi = interval
    if fit_logit < lo:
        return fit_logit - lo
    if fit_logit > hi:
        return fit_logit - hi
    return 0.0


def _golden_min(func, lo: float, hi: float, iters: int = 16) -> float:
    """Golden-section minimisation on [lo, hi]; deterministic."""
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    fc, fd = func(c), func(d)
    for _ in range(iters):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = func(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = func(d)
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- Stage 2 ---
# Trophy model: earners by cohort (CORRECTED 20 Nov, DESIGN §0.1/§2).
#
# Forecast EARNERS E_i(t) directly, never P(t) times a rate. Per trophy i,
# with r0_i its rate at the first segment, asymptote a_i (percent), speed
# k_i > 0 (per hour) and joiner rate j_i (percent):
#   (a) the initial cohort (P0 players) earns toward a_i at speed k_i;
#   (b) each ΔP new players tracked at a refresh arrives already holding
#       the trophy with probability j_i/100, then progresses like incumbents.
# Recursion over refresh intervals (P constant inside an interval, jumping
# at refreshes — exactly the step function the page shows):
#   interval Δt at P:  E'  = a/100*P + (E - a/100*P) * exp(-k*Δt)
#   refresh, ΔP joins: E'' = E' + j/100*ΔP
#   rate at the new segment: R = 100*E''/P_new.
# P now moves the rate materially (ablation test, EXPERIMENTS.md): fast
# growth pulls R toward j_i (dilution), slow growth lets R approach a_i.
# The fit is censored least squares on the logit scale over segment rates,
# hierarchical shrinkage M0/M1/M2 over (log k, logit a, logit j). NO
# name/chapter grouping; tier is a case field used only as a shrinkage
# group in M1/M2.

K_GRID = [10.0 ** (-3.0 + 0.3 * i) for i in range(14)]  # 0.001 .. ~8.0 / hour
J_GRID = [0.0, 0.01, 0.03, 0.1, 0.3, 1.0]  # joiner rate, percent


def _logistic_pct(la: float) -> float:
    """Logit (of a percent) -> percent, overflow-safe."""
    if la >= 0:
        return 100.0 / (1.0 + math.exp(-la))
    e = math.exp(la)
    return 100.0 * e / (1.0 + e)


def _cohort_coeffs(k: float, r0: float, segments: list[dict]):
    """For a FIXED speed k, the earner count at each segment is AFFINE in
    (a, j): E_m = C_m + a*A_m + j*B_m (the recursion is linear in a and j
    once k is fixed). Precompute the three coefficient lists in one pass
    (the only exp() calls); every (a, j) evaluation after that is cheap
    arithmetic. This is what makes the (k, j) x golden-section grid fast."""
    n = len(segments)
    A = [0.0] * n
    B = [0.0] * n
    C = [0.0] * n
    C[0] = r0 * segments[0]["P"] / 100.0
    for m in range(n - 1):
        dt = hours_between(segments[m]["start"], segments[m + 1]["start"])
        d = math.exp(-k * dt) if dt > 0 else 1.0
        P0 = segments[m]["P"]
        dP = segments[m + 1]["P"] - P0
        C[m + 1] = d * C[m]
        A[m + 1] = d * A[m] + P0 * (1.0 - d) / 100.0
        B[m + 1] = d * B[m] + dP / 100.0
    return A, B, C


def _rates_from_coeffs(A, B, C, a: float, j: float, segments: list[dict]):
    """Segment rates for (a, j) given the precomputed coefficients."""
    return [100.0 * (C[m] + a * A[m] + j * B[m]) / segments[m]["P"]
            for m in range(len(segments))]


def _recursion_rates(a: float, k: float, j: float, r0: float, segments: list[dict]) -> list[float]:
    """Rates (percent) at every segment start under (a, k, j). a, j, r0 in
    percent; k per hour. The first segment's rate is r0 by construction.
    Thin wrapper over the coefficient form."""
    A, B, C = _cohort_coeffs(k, r0, segments)
    return _rates_from_coeffs(A, B, C, a, j, segments)


def _cohort_loss_coeffs(A, B, C, a: float, j: float, segments: list[dict], intervals) -> float:
    """Censored squared loss on the logit scale over segments after the
    first, from precomputed coefficients (no exp() calls)."""
    total = 0.0
    for s in range(1, len(segments)):
        iv = intervals[s]
        if iv is None:
            continue
        rate = 100.0 * (C[s] + a * A[s] + j * B[s]) / segments[s]["P"]
        d = _censored_resid(_logit_raw(rate), iv)
        total += d * d
    return total


def _cohort_loss(a: float, k: float, j: float, r0: float, segments: list[dict], intervals) -> float:
    """Censored squared loss on the logit scale over segments after the first."""
    A, B, C = _cohort_coeffs(k, r0, segments)
    return _cohort_loss_coeffs(A, B, C, a, j, segments, intervals)


def _fit_one_trophy(segments: list[dict], intervals, r0: float):
    """Grid over (k, j) + golden-section over logit(a). Returns
    (a, k, j, rss, se_logk, se_logita, se_logitj) or None when there are no
    usable later reads. The coefficients for each k are computed once, so
    the (j, a) search is cheap arithmetic."""
    if not any(iv is not None for iv in intervals[1:]):
        return None
    la_lo, la_hi = _logit_raw(0.0001), _logit_raw(99.999)
    best = None
    for k in K_GRID:
        A, B, C = _cohort_coeffs(k, r0, segments)
        for j in J_GRID:
            def loss_la(la, A=A, B=B, C=C, j=j):
                return _cohort_loss_coeffs(A, B, C, _logistic_pct(la), j,
                                          segments, intervals)
            la = _golden_min(loss_la, la_lo, la_hi, iters=8)
            a = _logistic_pct(la)
            l = loss_la(la)
            if best is None or l < best[0]:
                best = (l, a, k, j)
    rss, a, k, j = best
    # Standard errors from the loss curvature at the optimum. A flat/censored
    # loss means no information -> large SE, so shrinkage dominates.
    n = sum(1 for iv in intervals[1:] if iv is not None)
    sig2 = rss / max(n - 3, 1)
    se_logk, se_logita, se_logitj = 5.0, 5.0, 5.0
    if sig2 > 1e-12 and rss > 1e-12:
        for param, delta in (("k", 0.25), ("a", 0.25), ("j", 1.0)):
            if param == "k":
                lp = _cohort_loss(a, k * math.exp(delta), j, r0, segments, intervals)
                lm = _cohort_loss(a, k * math.exp(-delta), j, r0, segments, intervals)
            elif param == "a":
                la = _logit_raw(a)
                lp = _cohort_loss(_logistic_pct(la + delta), k, j, r0, segments, intervals)
                lm = _cohort_loss(_logistic_pct(la - delta), k, j, r0, segments, intervals)
            else:
                lj = _logit_raw(max(j, 0.0001))
                jp = _logistic_pct(lj + delta)
                jm = _logistic_pct(lj - delta)
                lp = _cohort_loss(a, k, jp, r0, segments, intervals)
                lm = _cohort_loss(a, k, jm, r0, segments, intervals)
            curv = (lp + lm - 2.0 * rss) / (delta * delta)
            if curv > 1e-9:
                se = math.sqrt(2.0 * sig2 / curv)
                if param == "k":
                    se_logk = min(max(se, 0.05), 5.0)
                elif param == "a":
                    se_logita = min(max(se, 0.05), 5.0)
                else:
                    se_logitj = min(max(se, 0.05), 5.0)
    return (a, k, j, rss, se_logk, se_logita, se_logitj)


def _shrink_toward(values, ses, prior_mean, prior_var):
    """Empirical-Bayes posterior means: weight on the data is
    tau^2 / (tau^2 + se^2); a noisy fit is pulled to the prior."""
    out = []
    for v, se in zip(values, ses):
        w = prior_var / (prior_var + se * se)
        out.append(w * v + (1.0 - w) * prior_mean)
    return out


def _moment_var(values, ses):
    """Method-of-moments between-trophy variance, net of fit variance."""
    if len(values) < 2:
        return 0.01
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    noise = sum(se * se for se in ses) / len(ses)
    return max(var - noise, 0.01)


def _project_feasible(a: float, k: float, j: float, r0: float, segments: list[dict], intervals):
    """Pull (a, k, j) back inside every observation's rounding interval.
    A shrunk curve must still respect the data: a trophy observed at 100%
    cannot be forecast from parameters that put it below it, and one
    observed at 0.00% cannot sit above 0.005% at that read. Refit a (k, j
    fixed) with a tiny pull to the shrunk a; if still infeasible, raise k
    and move j toward the violated side and retry. This is a constraint on
    the fit, not a special case for any trophy."""
    def violated(aa, kk, jj):
        rates = _recursion_rates(aa, kk, jj, r0, segments)
        for s in range(1, len(segments)):
            iv = intervals[s]
            if iv is None:
                continue
            if _censored_resid(_logit_raw(rates[s]), iv) != 0.0:
                return True
        return False
    if not violated(a, k, j):
        return a, k, j
    la_shrunk = _logit_raw(a)
    kk, jj = k, j
    for _ in range(8):
        def loss_la(la, kk=kk, jj=jj):
            aa = _logistic_pct(la)
            return _cohort_loss(aa, kk, jj, r0, segments, intervals) + 1e-9 * (la - la_shrunk) ** 2
        la = _golden_min(loss_la, _logit_raw(0.0001), _logit_raw(99.999), iters=12)
        a = _logistic_pct(la)
        if not violated(a, kk, jj):
            return a, kk, jj
        kk = min(kk * 2.0, 50.0)
        rates = _recursion_rates(a, kk, jj, r0, segments)
        worst = 0.0
        for s in range(1, len(segments)):
            iv = intervals[s]
            if iv is None:
                continue
            d = _censored_resid(_logit_raw(rates[s]), iv)
            if abs(d) > abs(worst):
                worst = d
        if worst < 0:  # fitted too low: joiners should bring more
            jj = min(100.0, jj * 2.0 + 0.5)
        elif worst > 0:  # fitted too high: joiners should bring less
            jj = jj / 2.0
    return a, kk, jj


def fit_trophy_curves(case: dict, segments: list[dict]):
    """Fit every trophy's cohort parameters + M0/M1/M2 shrinkage variants.
    Returns a dict with per-trophy r0, base fits, shrunk (a, k, j) per
    variant, and residual diagnostics (rho, sigma_res, sigma_g)."""
    n_tr = len(case["trophies"])
    tiers = [t["tier"] for t in case["trophies"]]
    r0s = []
    for i in range(n_tr):
        r0 = None
        for seg in segments:
            if seg["rates"][i] is not None:
                r0 = seg["rates"][i]
                break
        r0s.append(0.0 if r0 is None else r0)
    ivs_all = [[_rate_interval(seg["rates"][i]) for seg in segments]
               for i in range(n_tr)]
    base = [_fit_one_trophy(segments, ivs_all[i], r0s[i]) for i in range(n_tr)]
    fitted = [b for b in base if b is not None]
    if fitted:
        med_a = sorted(b[0] for b in fitted)[len(fitted) // 2]
        med_k = sorted(b[1] for b in fitted)[len(fitted) // 2]
        med_j = sorted(b[2] for b in fitted)[len(fitted) // 2]
    else:
        med_a, med_k, med_j = 0.05, 0.05, 0.01    # Transformed params + SEs. Unidentified trophies take the population
    # medians with a huge SE, so shrinkage pulls them fully to the prior.
    us, vs, ws, seus, sevs, sews = [], [], [], [], [], []
    for b in base:
        if b is None:
            us.append(math.log(med_k))
            vs.append(_logit_raw(med_a))
            ws.append(_logit_raw(max(med_j, 0.0001)))
            seus.append(5.0)
            sevs.append(5.0)
            sews.append(5.0)
        else:
            us.append(math.log(max(b[1], 1e-6)))
            vs.append(_logit_raw(b[0]))
            ws.append(_logit_raw(max(b[2], 0.0001)))
            seus.append(b[4])
            sevs.append(b[5])
            sews.append(b[6])
    m_u = sum(us) / len(us)
    m_v = sum(vs) / len(vs)
    m_w = sum(ws) / len(ws)
    tau_u = _moment_var(us, seus)
    tau_v = _moment_var(vs, sevs)
    tau_w = _moment_var(ws, sews)
    # Tier groups (a field of the case, never a name/chapter guess).
    groups = {}
    for i, g in enumerate(tiers):
        groups.setdefault(g, []).append(i)
    g_mu, g_mv, g_mw = {}, {}, {}
    for g, idxs in groups.items():
        if len(idxs) >= 2:
            g_mu[g] = sum(us[i] for i in idxs) / len(idxs)
            g_mv[g] = sum(vs[i] for i in idxs) / len(idxs)
            g_mw[g] = sum(ws[i] for i in idxs) / len(idxs)
        else:
            g_mu[g] = m_u
            g_mv[g] = m_v
            g_mw[g] = m_w
    # M2: tier means pulled back to the population mean (pseudo-count 3).
    g2_mu, g2_mv, g2_mw = {}, {}, {}
    for g, idxs in groups.items():
        n_g = len(idxs)
        g2_mu[g] = (n_g * g_mu[g] + 3.0 * m_u) / (n_g + 3.0)
        g2_mv[g] = (n_g * g_mv[g] + 3.0 * m_v) / (n_g + 3.0)
        g2_mw[g] = (n_g * g_mw[g] + 3.0 * m_w) / (n_g + 3.0)
    variants = {}
    for name, pu, pv, pw in (
            ("M0", [m_u] * n_tr, [m_v] * n_tr, [m_w] * n_tr),
            ("M1", [g_mu[tiers[i]] for i in range(n_tr)],
             [g_mv[tiers[i]] for i in range(n_tr)],
             [g_mw[tiers[i]] for i in range(n_tr)]),
            ("M2", [g2_mu[tiers[i]] for i in range(n_tr)],
             [g2_mv[tiers[i]] for i in range(n_tr)],
             [g2_mw[tiers[i]] for i in range(n_tr)])):
        # Shrinkage is per-trophy toward its own prior mean.
        su, sv, sw = [], [], []
        for i in range(n_tr):
            wu = tau_u / (tau_u + seus[i] ** 2)
            wv = tau_v / (tau_v + sevs[i] ** 2)
            ww = tau_w / (tau_w + sews[i] ** 2)
            su.append(wu * us[i] + (1.0 - wu) * pu[i])
            sv.append(wv * vs[i] + (1.0 - wv) * pv[i])
            sw.append(ww * ws[i] + (1.0 - ww) * pw[i])
        params = []
        for i in range(n_tr):
            a = _logistic_pct(sv[i])
            k = math.exp(su[i])
            j = _logistic_pct(sw[i])
            a, k, j = _project_feasible(a, k, j, r0s[i], segments, ivs_all[i])
            params.append((a, k, j))
        variants[name] = params
    # Residual diagnostics on the M0 fit (shared across variants).
    series = []
    for i in range(n_tr):
        a, k, j = variants["M0"][i]
        rates = _recursion_rates(a, k, j, r0s[i], segments)
        row = []
        for s, seg in enumerate(segments):
            iv = ivs_all[i][s]
            if iv is None:
                row.append(None)
            else:
                mid = (iv[0] + iv[1]) / 2.0
                row.append(mid - _logit_raw(rates[s]))
        series.append(row)
    res_last = [row[-1] for row in series if row[-1] is not None]
    if res_last:
        var_r = sum(r * r for r in res_last) / len(res_last)
        hw2 = []
        for i in range(n_tr):
            iv = ivs_all[i][-1]
            if iv is not None:
                hw2.append(((iv[1] - iv[0]) / 2.0) ** 2 / 3.0)
        var_round = sum(hw2) / len(hw2) if hw2 else 0.0
        sigma_res = math.sqrt(max(var_r - var_round, 0.0))
    else:
        sigma_res = 0.0
    num, den1, den2 = 0.0, 0.0, 0.0
    for row in series:
        for s in range(1, len(row) - 1):
            if row[s] is not None and row[s + 1] is not None:
                num += row[s] * row[s + 1]
                den1 += row[s] ** 2
                den2 += row[s + 1] ** 2
    if den1 > 1e-12 and den2 > 1e-12:
        rho = max(0.0, min(0.8, num / math.sqrt(den1 * den2)))
    else:
        rho = 0.0
    seg_means = []
    for s in range(1, len(segments)):
        vals = [series[i][s] for i in range(n_tr) if series[i][s] is not None]
        if vals:
            seg_means.append(sum(vals) / len(vals))
    if len(seg_means) >= 3:
        m_sm = sum(seg_means) / len(seg_means)
        sigma_g = min(0.5, max(0.05, math.sqrt(
            sum((v - m_sm) ** 2 for v in seg_means) / len(seg_means))))
    else:
        sigma_g = 0.2
    # Posterior standard errors for the scenario draws: the shrunk estimate
    # is the posterior mean, and 1/se_post^2 = 1/se^2 + 1/tau^2.
    post_u = [math.sqrt((se * se * tau_u) / (se * se + tau_u)) for se in seus]
    post_v = [math.sqrt((se * se * tau_v) / (se * se + tau_v)) for se in sevs]
    post_w = [math.sqrt((se * se * tau_w) / (se * se + tau_w)) for se in sews]
    return {"r0": r0s, "base": base, "variants": variants,
            "rho": rho, "sigma_res": sigma_res, "sigma_g": sigma_g,
            "res_last": res_last,
            "post_se_u": post_u, "post_se_v": post_v, "post_se_w": post_w}


# ---------------------------------------------------------------- Stage 3 ---
# Joint predictive scenarios.
#
# One scenario draws, in a fixed order (so variants sharing a seed share
# the skeleton — common random numbers, which makes the M0/M1/M2 comparison
# low-variance):
#   (i)   a refresh path from the fitted renewal process -> t* (last refresh
#         at or before the target; shared by all trophies and by P);
#   (ii)  adoption parameters (a, b) from the LS normal approximation,
#         with the daily-growth cap -> P at every future refresh;
#   (iii) a SHARED progression-speed shock g (multiplies all k_i);
#   (iv)  per-trophy parameters (a_i, k_i, j_i) from their approximate
#         posteriors (normal on transformed scales), independent
#         CONDITIONAL on g;
#   (v)   per-trophy residual draws with the fitted decay.
# The cohort recursion then runs from the fitted earner state at the last
# observed segment along the scenario's refresh path, and the scenario rate
# is R_i = 100 * E_i / P(t*). The P(t*) does NOT cancel: the joiner terms
# and the incumbent evolution both depend on the player path. Quantiles are
# the empirical tau-quantiles of the scenario rates (rounded to 2 decimals,
# as the page prints them).

def build_context(case: dict, segments: list[dict]) -> dict:
    """Fit everything the scenarios need, once, from the case's history."""
    t_release = parse_time(case["game"]["release_date"])
    t_checkpoint = parse_time(case["at"])
    t_target = parse_time(case["target"])
    adoption = fit_adoption(segments, t_release)
    gaps = fit_gaps(segments)
    trophies = fit_trophy_curves(case, segments)
    horizon_days = (t_target - t_checkpoint).total_seconds() / 86400.0
    return {"case": case, "segments": segments, "t_release": t_release,
            "t_checkpoint": t_checkpoint, "t_target": t_target,
            "adoption": adoption, "gaps": gaps, "trophies": trophies,
            "horizon_days": horizon_days}


def _draw_adoption(rng: random.Random, fit: dict, tau_target: float):
    """(a, b) from the LS normal approximation (Cholesky), b capped at the
    largest observed daily growth and floored at 0 (players do not shrink
    in launch week). Returns (a, b_eff)."""
    a, b = fit["a"], fit["b"]
    va, vb, cov = fit["var_a"], fit["var_b"], fit["cov_ab"]
    if va > 1e-12 and vb > 1e-12:
        z1 = rng.gauss(0.0, 1.0)
        z2 = rng.gauss(0.0, 1.0)
        sa = math.sqrt(va)
        a_d = a + sa * z1
        b_d = b + (cov / sa) * z1 + math.sqrt(max(vb - cov * cov / va, 0.0)) * z2
    else:
        a_d, b_d = a, b
    b_cap = capped_b({"a": a_d, "b": b_d, "max_daily_growth": fit["max_daily_growth"]},
                     tau_target)
    return a_d, min(max(b_d, 0.05), b_cap) if b_cap > 0.05 else max(b_d, 0.05)


def _scenario_rates(ctx: dict, variant: str, rng: random.Random, E_last: list[float],
                    adoption_scale: float = 1.0, gap_scale: float = 1.0,
                    force_no_refresh: bool = False) -> list[float]:
    """One joint scenario -> the 55 scenario rates (percent, 2-decimal)."""
    case = ctx["case"]
    segments = ctx["segments"]
    trophies = ctx["trophies"]
    n_tr = len(case["trophies"])
    t_checkpoint = ctx["t_checkpoint"]
    t_target = ctx["t_target"]
    t_release = ctx["t_release"]
    # (i) Refresh path -> t* and the future (time, P) breakpoints.
    gap_model = ctx["gaps"]
    if gap_scale != 1.0 and gap_model[0] == "lognormal":
        _, ml, sl = gap_model
        gap_model = ("lognormal", ml + math.log(gap_scale), sl)
    elif gap_scale != 1.0:
        gap_model = ("empirical", [g * gap_scale for g in gap_model[1]])
    t_last = segments[-1]["start"]
    if force_no_refresh:
        refreshes = []
    else:
        refreshes = simulate_refresh_times(rng, gap_model, t_last,
                                           t_checkpoint, t_target)
    t_star = t_last
    for t in refreshes:
        if t <= t_target:
            t_star = t
    # (ii) Adoption draw -> P at every future refresh.
    tau_target = hours_between(t_release, t_target)
    a_ad, b_ad = _draw_adoption(rng, ctx["adoption"], tau_target)
    b_ad = b_ad * adoption_scale
    future_P = []
    for t in refreshes:
        tau = hours_between(t_release, t)
        future_P.append(math.exp(a_ad + b_ad * math.log1p(max(tau, -0.9))))
    # Breakpoints: observed segments, then future refreshes up to t*.
    # Precompute the FUTURE intervals once (dt, P at the interval start,
    # P jump at its end) so the per-trophy loop is a bare recursion.
    points = [(seg["start"], seg["P"]) for seg in segments]
    for t, p in zip(refreshes, future_P):
        if t <= t_star:
            # P is monotone by construction (b > 0); guard anyway.
            points.append((t, max(p, points[-1][1])))
    intervals = []
    for m in range(len(points) - 1):
        t0p, P0p = points[m]
        t1p, P1p = points[m + 1]
        if t1p <= segments[-1]["start"]:
            continue  # past is already in E_last
        dt = hours_between(t0p, t1p)
        if dt <= 0:
            continue
        intervals.append((dt, P0p, P1p - P0p))
    P_star = points[-1][1] if points else segments[-1]["P"]
    # (iii) Shared speed shock.
    g = math.exp(rng.gauss(0.0, trophies["sigma_g"])) if trophies["sigma_g"] > 0 else 1.0
    # (iv) Per-trophy parameters from their approximate posteriors.
    params = trophies["variants"][variant]
    res_last = trophies["res_last"]
    rho = trophies["rho"]
    h = ctx["horizon_days"]
    decay = rho ** h if rho > 0 else 0.0
    noise_sd = trophies["sigma_res"] * math.sqrt(max(1.0 - rho ** (2.0 * h), 0.0))
    out = []
    for i in range(n_tr):
        a, k, j = params[i]
        # Posterior draws on transformed scales.
        lk = math.log(max(k, 1e-6)) + rng.gauss(0.0, trophies["post_se_u"][i])
        la = _logit_raw(a) + rng.gauss(0.0, trophies["post_se_v"][i])
        lj = _logit_raw(max(j, 0.0001)) + rng.gauss(0.0, trophies["post_se_w"][i])
        k_d = min(max(math.exp(lk) * g, 1e-4), 50.0)
        a_d = _logistic_pct(la)
        j_d = _logistic_pct(lj)
        # Cohort recursion from the fitted state along the scenario path.
        E = E_last[i]
        a100 = a_d / 100.0
        j100 = j_d / 100.0
        for (dt, P0p, dP) in intervals:
            E = a100 * P0p + (E - a100 * P0p) * math.exp(-k_d * dt)
            E = E + j100 * dP
        R = 100.0 * E / P_star if P_star > 0 else 0.0
        # (v) Residual: decayed last residual + noise, on the logit scale.
        shift = decay * (res_last[i] if i < len(res_last) else 0.0)
        if noise_sd > 0:
            shift += rng.gauss(0.0, noise_sd)
        R = logistic(logit(R) + shift)
        out.append(round2_half_up(min(max(R, 0.0), 100.0)))
    return out


def _quantile(sorted_vals, tau: float) -> float:
    """Empirical quantile, linear interpolation on the sorted list."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = tau * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def run_scenarios(ctx: dict, variant: str, n_scen: int, seed: int,
                  adoption_scale: float = 1.0, gap_scale: float = 1.0,
                  force_no_refresh: bool = False) -> list[list[float]]:
    """n_scen joint scenarios -> per-trophy lists of scenario rates."""
    n_tr = len(ctx["case"]["trophies"])
    segments = ctx["segments"]
    trophies = ctx["trophies"]
    params = trophies["variants"][variant]
    r0s = trophies["r0"]
    # Fitted earner state at the last observed segment (scenario-independent).
    E_last = []
    for i in range(n_tr):
        a, k, j = params[i]
        rates = _recursion_rates(a, k, j, r0s[i], segments)
        E_last.append(segments[-1]["P"] * rates[-1] / 100.0)
    cols = [[] for _ in range(n_tr)]
    for s in range(n_scen):
        rng = random.Random(seed + s)
        rates = _scenario_rates(ctx, variant, rng, E_last,
                                adoption_scale=adoption_scale,
                                gap_scale=gap_scale,
                                force_no_refresh=force_no_refresh)
        for i, r in enumerate(rates):
            cols[i].append(r)
    return cols


def quantiles_from_scenarios(ctx: dict, cols: list[list[float]]) -> dict:
    rows = []
    for i, trophy in enumerate(ctx["case"]["trophies"]):
        vals = sorted(cols[i])
        rows.append({"id": trophy["id"],
                     "q": [round4(_quantile(vals, tau)) for tau in LEVELS]})
    return {"trophies": rows}


def median_forecast(ctx: dict, variant: str) -> list[float]:
    """Deterministic median path (no Monte Carlo): median refresh gaps,
    median adoption, median parameters, g = 1, decayed residual only.
    Used for the cheap Leg A variant comparison."""
    case = ctx["case"]
    segments = ctx["segments"]
    trophies = ctx["trophies"]
    n_tr = len(case["trophies"])
    t_checkpoint = ctx["t_checkpoint"]
    t_target = ctx["t_target"]
    t_release = ctx["t_release"]
    t_last = segments[-1]["start"]
    # Median refresh path.
    gap_model = ctx["gaps"]
    age = hours_between(t_last, t_checkpoint)
    times = []
    t = t_checkpoint
    if gap_model[0] == "lognormal":
        _, ml, sl = gap_model
        nd = statistics.NormalDist()
        p_age = nd.cdf((math.log(age) - ml) / sl) if age > 0 else 0.0
        med_first = math.exp(ml + sl * nd.inv_cdf(min(max((1.0 + p_age) / 2.0, 1e-9), 1 - 1e-9)))
        med_gap = math.exp(ml)
        gap = med_first
    else:
        gaps = sorted(gap_model[1]) if gap_model[1] else [24.0]
        above = [g for g in gaps if g > age]
        gap = above[len(above) // 2] if above else max(gaps) * 1.5
        med_gap = gaps[len(gaps) // 2]
    while True:
        t = t + timedelta(hours=gap)
        times.append(t)
        if t >= t_target or len(times) > 1000:
            break
        gap = med_gap
    t_star = t_last
    for tt in times:
        if tt <= t_target:
            t_star = tt
    # Median adoption.
    fit = ctx["adoption"]
    tau_target = hours_between(t_release, t_target)
    b_eff = capped_b(fit, tau_target)
    points = [(seg["start"], seg["P"]) for seg in segments]
    for tt in times:
        if tt <= t_star:
            tau = hours_between(t_release, tt)
            p = math.exp(fit["a"] + b_eff * math.log1p(max(tau, -0.9)))
            points.append((tt, max(p, points[-1][1])))
    params = trophies["variants"][variant]
    r0s = trophies["r0"]
    res_last = trophies["res_last"]
    rho = trophies["rho"]
    h = ctx["horizon_days"]
    decay = rho ** h if rho > 0 else 0.0
    intervals = []
    for m in range(len(points) - 1):
        t0p, P0p = points[m]
        t1p, P1p = points[m + 1]
        if t1p <= segments[-1]["start"]:
            continue
        dt = hours_between(t0p, t1p)
        if dt <= 0:
            continue
        intervals.append((dt, P0p, P1p - P0p))
    P_star = points[-1][1] if points else segments[-1]["P"]
    out = []
    for i in range(n_tr):
        a, k, j = params[i]
        rates = _recursion_rates(a, k, j, r0s[i], segments)
        E = segments[-1]["P"] * rates[-1] / 100.0
        a100 = a / 100.0
        j100 = j / 100.0
        for (dt, P0p, dP) in intervals:
            E = a100 * P0p + (E - a100 * P0p) * math.exp(-k * dt)
            E = E + j100 * dP
        R = 100.0 * E / P_star if P_star > 0 else 0.0
        shift = decay * (res_last[i] if i < len(res_last) else 0.0)
        R = logistic(logit(R) + shift)
        out.append(round2_half_up(min(max(R, 0.0), 100.0)))
    return out


# ---------------------------------------------------------------- Stage 4 ---
# Leg A (rolling-origin) variant selection, lambda calibration, stress floor.
#
# DEV-ONLY machinery. Production (model_forecast) is LOCKED to M0 + lambda
# 1.25 with no stress floor (Ting finalization, 20 Nov 2026): the runtime
# re-selection evidence was 3 simulator origins with a 0.007 margin, too
# weak to override the locked config, and the final spread check showed the
# stress floor hurts mean pinball-logit on both Leg C seeds without
# improving coverage. These functions remain for dev experiments
# (tools/backtest.py, tools/final_spread_check.py).
#
# Leg A is the ONLY in-program scoring leg. Origins are segment starts with
# >=2 segments before them and >=1 day of history after them; the
# pseudo-target is the segment start closest to origin + 36 h. The variant
# is chosen by median absolute logit error of the deterministic median
# forecast (cheap; the variants differ in their median, not in a shared
# spread). Lambda is chosen by MEAN PINBALL-LOGIT over all five quantiles
# (the calibration objective; 90% coverage is a diagnostic only) over the
# grid [0.5..3]. With <2 origins there is no calibration: M0, lambda = 1.
# The stress floor is three scenario groups (refresh faster than median;
# refresh slower including NO refresh until target; player growth much
# larger/smaller than projected); the final spread is
# s_final = max(s_calibrated, s_stress) per trophy, re-centred on the
# calibrated median. Cross-trophy residual correlation beyond the shared
# shocks (g and t*) is NOT modelled — no claim otherwise is made.

def pinball_logit(y: float, q: float, tau: float) -> float:
    """Pinball loss on the logit scale (the competition metric)."""
    ly = logit(max(min(y, 99.99), 0.01))
    lq = logit(max(min(q, 99.99), 0.01))
    return tau * (ly - lq) if ly >= lq else (1.0 - tau) * (lq - ly)


def leg_a_origins(case: dict, segments: list[dict]):
    """(origin_idx, target_idx) pairs: origin has >=2 segments before it
    and a segment >=24 h after it; target is the segment closest to
    origin + 36 h. At most MAX_ORIGINS, evenly spread (cost-bounded)."""
    pairs = []
    for o in range(2, len(segments)):
        t_o = segments[o]["start"]
        cands = [(abs(hours_between(t_o, segments[j]["start"]) - 36.0), j)
                 for j in range(o + 1, len(segments))
                 if hours_between(t_o, segments[j]["start"]) >= 24.0]
        if not cands:
            continue
        cands.sort()
        pairs.append((o, cands[0][1]))
    if len(pairs) > MAX_ORIGINS:
        idx = [round(i * (len(pairs) - 1) / (MAX_ORIGINS - 1))
               for i in range(MAX_ORIGINS)]
        pairs = [pairs[i] for i in sorted(set(idx))]
    return pairs


def _sub_case(case: dict, segments: list[dict], origin_idx: int, target_idx: int):
    """The case as seen from an origin: history up to the origin segment,
    checkpoint at the origin, pseudo-target at the later segment."""
    t_origin = segments[origin_idx]["start"]
    t_target = segments[target_idx]["start"]
    hist = [r for r in case["history"]
            if parse_time(r["at"]) <= t_origin]
    sub = {"game": case["game"], "trophies": case["trophies"],
           "levels": case["levels"], "history": hist,
           "at": t_origin.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "target": t_target.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "horizon_minutes": int((t_target - t_origin).total_seconds() // 60)}
    return sub, segments[target_idx]["rates"]


def _origin_contexts(case: dict, segments: list[dict], origins):
    """Build the (ctx, pseudo-target rates) pair for each origin once; the
    context is variant-independent, so selection and calibration share it
    instead of refitting per variant."""
    out = []
    for (o, t) in origins:
        sub, y = _sub_case(case, segments, o, t)
        segs = segment_history(sub)
        if len(segs) < 2:
            continue
        out.append((build_context(sub, segs), y))
    return out


def select_variant(octxs):
    """Winner by mean absolute logit error of the median forecast
    (deterministic, cheap) over the prebuilt origin contexts.
    Returns (variant, per-variant errors)."""
    errs = {v: 0.0 for v in ("M0", "M1", "M2")}
    cnts = {v: 0 for v in ("M0", "M1", "M2")}
    for (ctx, y) in octxs:
        for variant in ("M0", "M1", "M2"):
            med = median_forecast(ctx, variant)
            for i, yi in enumerate(y):
                errs[variant] += abs(logit(max(med[i], 0.01)) - logit(max(min(yi, 99.99), 0.01)))
                cnts[variant] += 1
    for v in errs:
        errs[v] = errs[v] / cnts[v] if cnts[v] else float("inf")
    winner = min(("M0", "M1", "M2"), key=lambda v: (errs[v], v))
    return winner, errs


def calibrate_lambda(octxs, variant: str):
    """Mean pinball-logit (all five quantiles, all trophies, all origins)
    for each lambda on the grid; the winner minimises it. The quantiles are
    scaled in logit space around the median (the median is unchanged)."""
    scores = {lam: 0.0 for lam in LAMBDA_GRID}
    cnt = 0
    for (ctx, y) in octxs:
        cols = run_scenarios(ctx, variant, N_BACKTEST, SEED)
        fc = quantiles_from_scenarios(ctx, cols)
        for i, yi in enumerate(y):
            qs = fc["trophies"][i]["q"]
            med_l = logit(max(min(qs[2], 99.99), 0.01))
            for tau, q in zip(LEVELS, qs):
                lq = logit(max(min(q, 99.99), 0.01))
                for lam in LAMBDA_GRID:
                    ql = logistic(med_l + lam * (lq - med_l))
                    scores[lam] += pinball_logit(yi, ql, tau)
                cnt += 1
    if cnt == 0:
        return 1.0, {}
    mean = {lam: scores[lam] / cnt for lam in LAMBDA_GRID}
    return min(LAMBDA_GRID, key=lambda lam: (mean[lam], lam)), mean


def stress_halfwidths(ctx: dict, variant: str) -> list[float]:
    """Per-trophy 90% half-width (logit) of the WORST stress run.
    Stress groups: (S1) refresh faster than median (gaps x0.5);
    (S2) refresh slower incl. NO refresh until target; (S3) player growth
    much larger (x1.5) and much smaller (x0.5) than projected."""
    n_tr = len(ctx["case"]["trophies"])
    worst = [0.0] * n_tr
    runs = [dict(gap_scale=0.5),
            dict(force_no_refresh=True),
            dict(adoption_scale=1.5),
            dict(adoption_scale=0.5)]
    for kw in runs:
        cols = run_scenarios(ctx, variant, N_STRESS, SEED, **kw)
        for i in range(n_tr):
            vals = sorted(cols[i])
            lo = _quantile(vals, 0.05)
            hi = _quantile(vals, 0.95)
            hw = (logit(max(hi, 0.01)) - logit(max(lo, 0.01))) / 2.0
            worst[i] = max(worst[i], hw)
    return worst


def _scale_quantiles_logit(forecast: dict, lam: float) -> dict:
    """Scale every quantile in logit space around the median."""
    rows = []
    for row in forecast["trophies"]:
        med_l = logit(max(min(row["q"][2], 99.99), 0.01))
        qs = []
        for q in row["q"]:
            lq = logit(max(min(q, 99.99), 0.01))
            qs.append(round4(logistic(med_l + lam * (lq - med_l))))
        rows.append({"id": row["id"], "q": qs})
    return {"trophies": rows}


def _apply_stress_floor(forecast: dict, s_stress: list[float]) -> dict:
    """s_final = max(s_cal, s_stress) per trophy, re-centred on the median:
    widen the quantiles proportionally (in logit) when the stress spread
    exceeds the calibrated one; never narrow."""
    rows = []
    for k, row in enumerate(forecast["trophies"]):
        qs = row["q"]
        med_l = logit(max(min(qs[2], 99.99), 0.01))
        lo_l = logit(max(min(qs[0], 99.99), 0.01))
        hi_l = logit(max(min(qs[4], 99.99), 0.01))
        s_cal = (hi_l - lo_l) / 2.0
        s_fin = max(s_cal, s_stress[k])
        factor = (s_fin / s_cal) if s_cal > 1e-9 else (1.0 if s_fin <= 1e-9 else 99.0)
        out = []
        for q in qs:
            lq = logit(max(min(q, 99.99), 0.01))
            out.append(round4(logistic(med_l + factor * (lq - med_l))))
        rows.append({"id": row["id"], "q": out})
    return {"trophies": rows}


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
    # Full pipeline, LOCKED configuration (Ting finalization, 20 Nov 2026):
    # variant M0 (population shrinkage) + lambda 1.25, NO stress floor.
    # The runtime Leg A re-selection is disabled: its evidence was 3
    # simulator origins with a 0.007 margin (M1 0.308 vs M0 0.315), too weak
    # to override the locked config on the real final case. The Leg A
    # machinery (select_variant / calibrate_lambda) is kept for dev
    # experiments (tools/backtest.py) but is not run in production.
    # The stress floor was dropped by the final spread check
    # (tools/final_spread_check.py, Leg C seeds 7+11): it widened only 1/55
    # trophies, hurt mean pinball-logit on both seeds (0.2193->0.2216;
    # 0.2238->0.2261) and did not improve coverage (0.82->0.82; 0.67->0.67),
    # so the wider spread was NOT picked (pinball decides, coverage is
    # diagnostic). stress_halfwidths is kept for dev/stress analysis.
    ctx = build_context(case, segments)
    cols = run_scenarios(ctx, "M0", N_FINAL, SEED)
    fc = quantiles_from_scenarios(ctx, cols)
    return _scale_quantiles_logit(fc, 1.25)


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
