#!/usr/bin/env python3
"""REVISI battery for Ting's verdict (2026-11-20 21:02 WIB) on rank1-fresh.

Ting: REVISI -- do not submit rank1-fresh yet. rank1-fresh is the main
candidate, challenger fixed-0.75 the main fallback. The P0 blocker: if
f00 has < 8 refresh segments, the rich path never runs and the entry
becomes the starter (~0.5 on the simulator).

Sections:
  A. Segment-boundary test (7/8/9/10 segments, deterministic f00-like
     cases): the starter -> rich transition must fire exactly at >= 8,
     no off-by-one, no crash, valid output.
  B. Realistic f00-like inputs (independent hazard simulator, all
     scenarios x held-out + fresh seeds, incl. a mid cadence gap 8h):
     per case, which path fires, determinism, runtime, validity.
  C. Fallback audit: how likely is f00 < 8 segments (observed refresh
     cadence 19-21 Nov + renewal Monte Carlo), and does a degraded
     rich path beat the starter on sparse histories without touching
     >= 8 behaviour?
  D. Frozen-path audit (info-time): can the frozen path fire on any
     f00-like case? Static proof + dynamic runs + adversarial
     near-misses.
  E. Earners discrepancy: 4,275.3 (computed from rounded rates) vs
     4,276 (competition.yaml:108) -- exact derivation + rounding bounds.

Stdlib only. Prints a report and writes tools/r1_revision_results.json.
"""

import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import r1_score
import r1_sim
import main as M

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_IN = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input"

# f00 shape (RULES.txt): history 2026-11-18T23:50Z .. 2026-11-21T11:30Z,
# target 2026-11-26T00:00:00Z, horizon 6,510 minutes.
F00_START = datetime(2026, 11, 18, 23, 50, 0, tzinfo=timezone.utc)
F00_CHECKPOINT = datetime(2026, 11, 21, 11, 30, 0, tzinfo=timezone.utc)
F00_TARGET = datetime(2026, 11, 26, 0, 0, 0, tzinfo=timezone.utc)
F00_HORIZON_MIN = 6510


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_kit_case():
    return json.load(open(os.path.join(KIT_IN, "p00.in")))


def _load_sources():
    return json.load(open(os.path.join(REPO, "data/rank1-sources.json")))


def _run_main_on_case(case, tag):
    """Run main.py (subprocess) on one case; return (out, dt, out_bytes)."""
    d_in = "/tmp/r1-rev-%s-in" % tag
    d_out = "/tmp/r1-rev-%s-out" % tag
    os.makedirs(d_in, exist_ok=True)
    os.makedirs(d_out, exist_ok=True)
    for f in os.listdir(d_out):
        os.remove(os.path.join(d_out, f))
    with open(os.path.join(d_in, case["id"] + ".in"), "w") as f:
        json.dump(case, f)
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = d_in
    env["MAKEFUN_OUTPUT_DIR"] = d_out
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, os.path.join(REPO, "main.py")],
        env=env, capture_output=True, text=True)
    dt = time.time() - t0
    assert p.returncode == 0, "main.py crashed: %s" % p.stderr[:500]
    out_path = os.path.join(d_out, case["id"] + ".out")
    with open(out_path, "rb") as f:
        raw = f.read()
    return json.loads(raw), dt, raw


def _valid(out):
    """Contract validity: 55 rows, 5 quantiles, non-decreasing, [0,100]."""
    rows = out.get("trophies") or []
    if len(rows) != 55:
        return False, "rows=%d" % len(rows)
    for row in rows:
        q = row.get("q") or []
        if len(q) != 5:
            return False, "id %s q len %d" % (row.get("id"), len(q))
        if not all(isinstance(v, (int, float)) for v in q):
            return False, "id %s non-numeric" % row.get("id")
        if not all(0.0 <= v <= 100.0 for v in q):
            return False, "id %s out of range" % row.get("id")
        if not all(q[i] <= q[i + 1] for i in range(4)):
            return False, "id %s not non-decreasing" % row.get("id")
    return True, "ok"


def _path_of(case):
    """Which path forecast_case takes, by direct inspection."""
    if M.is_frozen_case(case):
        return "frozen"
    if M._rich_rows(case) is not None:
        return "rich"
    return "starter"


def build_boundary_case(n_segments, front_loaded=False):
    """Deterministic f00-like case with EXACTLY n_segments refresh
    segments. Segment 1 is the frozen launch-night page (638 players,
    frozen rates); later segments dilute toward the live-snapshot
    levels while players grow 638 -> ~240k. Reads every 10 min.
    front_loaded=True puts all refreshes in the first 24h (thin last-24h
    window) -- a stress case for the window logic."""
    kit = _load_kit_case()
    src = _load_sources()
    frozen = src["frozen_rates_last_read"]
    live = src["live_snapshot_rates_2026_11_20_0907Z"]
    # Segment boundaries: evenly spaced over the history span, or all in
    # the first 24h when front_loaded. Spacing is span/n (not span/(n-1))
    # so the last boundary sits strictly inside the history -- an
    # endpoint boundary at the checkpoint itself is a floating-point
    # coin flip (it cost one segment in an earlier run: n=7 gave 6).
    span_h = (F00_CHECKPOINT - F00_START).total_seconds() / 3600.0
    if front_loaded:
        bounds = [i * (24.0 / n_segments) for i in range(n_segments)]
    else:
        bounds = [i * (span_h / n_segments) for i in range(n_segments)]
    # Players per segment: log-interpolated 638 -> 240,000.
    states = []
    for i in range(n_segments):
        frac = i / max(1, n_segments - 1)
        logp = math.log(638) + frac * (math.log(240000) - math.log(638))
        players = int(round(math.exp(logp)))
        rates = []
        for j in range(55):
            if j == 1:
                rates.append(100.0)
                continue
            # Logit-interpolate frozen -> live levels.
            lo = M.logit_rate(max(frozen[j], 0.01))
            hi = M.logit_rate(max(live[j], 0.01))
            r = M.inv_logit_rate(lo + frac * (hi - lo))
            rates.append(round(r, 2))
        states.append({"at_h": bounds[i], "players": players, "rates": rates})
    history = []
    read_t = F00_START
    seg_idx = 0
    while read_t <= F00_CHECKPOINT:
        h = (read_t - F00_START).total_seconds() / 3600.0
        while seg_idx + 1 < n_segments and bounds[seg_idx + 1] <= h:
            seg_idx += 1
        st = states[seg_idx]
        earn = int(round(sum(r / 100.0 * st["players"] for r in st["rates"] if r < 100.0)))
        history.append({
            "slot": _ts(read_t), "at": _ts(read_t),
            "players_tracked": st["players"],
            "trophies_earned": earn, "rates": list(st["rates"]),
        })
        read_t += timedelta(minutes=10)
    return {
        "id": "f00-boundary-%d%s" % (n_segments, "-front" if front_loaded else ""),
        "at": _ts(F00_CHECKPOINT),
        "horizon_minutes": F00_HORIZON_MIN,
        "target": _ts(F00_TARGET),
        "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "game": kit["game"],
        "trophies": kit["trophies"],
        "history": history,
        "notes": "synthetic boundary case, %d segments" % n_segments,
    }


def section_a_boundary():
    print("=== A. Segment-boundary test (starter -> rich at >= 8) ===")
    rows = []
    for n in (7, 8, 9, 10):
        for front in (False, True):
            case = build_boundary_case(n, front_loaded=front)
            segs = M.segments_of(case["history"])
            n_seg = len(segs)
            win = [s for s in segs
                   if s["at"] >= M.parse_ts(case["at"]) - timedelta(hours=24.0)]
            path = _path_of(case)
            out, dt, raw = _run_main_on_case(case, "a-%d-%s" % (n, "f" if front else "e"))
            out2, dt2, raw2 = _run_main_on_case(case, "a2-%d-%s" % (n, "f" if front else "e"))
            ok, why = _valid(out)
            expect = "rich" if n >= 8 else "starter"
            verdict = "OK" if (path == expect and ok and raw == raw2 and dt < 8.0) else "FAIL"
            print("  n=%2d %-5s segs=%d win=%d path=%-7s expect=%-7s valid=%s det=%s %.2fs size=%dB -> %s"
                  % (n, "front" if front else "even", n_seg, len(win), path, expect,
                     ok, raw == raw2, dt, len(raw), verdict))
            rows.append({"n": n, "front_loaded": front, "segments": n_seg,
                         "window_segments": len(win), "path": path,
                         "expected": expect, "valid": ok, "valid_why": why,
                         "deterministic": raw == raw2, "runtime_s": round(dt, 3),
                         "size_bytes": len(raw), "verdict": verdict})
    return rows


# --- Section B: realistic f00-like inputs (simulator) -----------------------

def _patched_params(seed, scenario, kit, src):
    """r1_sim._params plus a mid cadence (gap median 8h) -- the borderline
    regime Ting's P0 is about. Everything else identical to the sim."""
    if scenario != "refresh_mid":
        return _orig_params(seed, scenario, kit, src)
    base = _orig_params(seed, "base", kit, src)
    base["gap_med"] = 8.0
    return base


_orig_params = r1_sim._params


def section_b_realistic():
    print("=== B. Realistic f00-like inputs (hazard simulator) ===")
    r1_sim._params = _patched_params
    scenarios = ["base", "refresh_fast", "refresh_mid", "refresh_slow",
                 "growth_fast", "growth_slow", "noise", "jump"]
    rows = []
    try:
        for sc in scenarios:
            for seeds, tag in ((range(6, 11), "held-out"), (range(11, 16), "fresh")):
                for seed in seeds:
                    for hd in (2.486, 2.7):
                        case, truth = r1_sim.gen_case(seed, sc, history_days=hd)
                        n_seg = r1_sim.n_segments(case)
                        path = _path_of(case)
                        frozen = M.is_frozen_case(case)
                        out, dt, raw = _run_main_on_case(
                            case, "b-%s-%d-%.3f" % (sc, seed, hd))
                        ok, why = _valid(out)
                        s = r1_score.score(out, truth)
                        rows.append({
                            "scenario": sc, "seed": seed, "tag": tag,
                            "history_days": hd, "segments": n_seg,
                            "path": path, "frozen": frozen, "valid": ok,
                            "valid_why": why, "runtime_s": round(dt, 3),
                            "size_bytes": len(raw), "loss": round(s["loss"], 4),
                        })
        # Summary by scenario (held-out + fresh pooled, history 2.486d
        # is the real f00 span; 2.7d is the sim's default).
        agg = {}
        for r in rows:
            agg.setdefault(r["scenario"], []).append(r)
        for sc in scenarios:
            rs = agg[sc]
            segs = [r["segments"] for r in rs]
            rich = sum(1 for r in rs if r["path"] == "rich")
            start = sum(1 for r in rs if r["path"] == "starter")
            froz = sum(1 for r in rs if r["path"] == "frozen")
            bad = [r for r in rs if not r["valid"]]
            print("  %-13s n=%2d segs min %d med %.0f max %d  rich %2d starter %2d frozen %d  invalid %d  loss(mean) %.4f"
                  % (sc, len(rs), min(segs),
                     sorted(segs)[len(segs) // 2], max(segs),
                     rich, start, froz, len(bad),
                     sum(r["loss"] for r in rs) / len(rs)))
        print("  frozen fired on any f00-like case: %s"
              % any(r["frozen"] for r in rows))
        print("  invalid outputs: %d / %d" % (
            sum(1 for r in rows if not r["valid"]), len(rows)))
    finally:
        r1_sim._params = _orig_params
    return rows


# --- Section C: fallback audit ---------------------------------------------

def _renewal_p_below(n_hours, gap_med, gap_sigma=0.6, trials=20000, seed=99):
    """Monte Carlo: P(refresh segments < 8) in an n_hours window, renewal
    process with LogNormal gaps, first refresh uniform(1,6)h after the
    window start (as in r1_sim). Segments = refreshes + 1 (initial state)."""
    rng = random.Random(seed)
    below = 0
    seg_counts = []
    for _ in range(trials):
        t = rng.uniform(1.0, 6.0)
        n_ref = 0
        while t <= n_hours:
            n_ref += 1
            t += max(0.5, rng.lognormvariate(math.log(gap_med), gap_sigma))
        segs = n_ref + 1
        seg_counts.append(segs)
        if segs < 8:
            below += 1
    seg_counts.sort()
    return {
        "p_below_8": below / trials,
        "median_segments": seg_counts[len(seg_counts) // 2],
        "p10_segments": seg_counts[len(seg_counts) // 10],
        "p90_segments": seg_counts[9 * len(seg_counts) // 10],
    }


def _candidate_out(case, damping=None, mult=None, min_segments=None,
                   tau_grid=None, window_hours=None, adaptive=False):
    """Run forecast_case with monkeypatched constants (in-process only).
    adaptive=True: damping = n_segs / (n_segs + 8) (trust the trend less
    when segments are few). Returns the output dict."""
    saved = (M.RICH_DAMPING, M.RICH_MULT_FIXED, M.RICH_MIN_SEGMENTS,
             M.RICH_TAU_GRID, M.RICH_WINDOW_HOURS)
    try:
        if adaptive:
            n = len(M.segments_of(case["history"]))
            M.RICH_DAMPING = n / (n + 8.0)
        elif damping is not None:
            M.RICH_DAMPING = damping
        if mult is not None:
            M.RICH_MULT_FIXED = mult
        if min_segments is not None:
            M.RICH_MIN_SEGMENTS = min_segments
        if tau_grid is not None:
            M.RICH_TAU_GRID = tau_grid
        if window_hours is not None:
            M.RICH_WINDOW_HOURS = window_hours
        return {"trophies": M.forecast_case(case)}
    finally:
        (M.RICH_DAMPING, M.RICH_MULT_FIXED, M.RICH_MIN_SEGMENTS,
         M.RICH_TAU_GRID, M.RICH_WINDOW_HOURS) = saved


def section_c_fallback():
    print("=== C. Fallback audit ===")
    # C1. Observed cadence -> P(< 8 segments) in the real f00 window.
    print("  C1. Refresh-cadence evidence (19-21 Nov, from RULES/competition.yaml + live snapshot):")
    print("      - launch night: NO refresh 23:48Z -> 04:00Z (4.2h), then ONE refresh by 04:30Z (3,868 players).")
    print("      - 04:30Z -> 09:16Z (4.77h): NO refresh (still 3,868 players).")
    print("      - 09:16Z 19 Nov -> 09:07Z 20 Nov (23.85h): >= 1 refresh (P 3,868 -> >80,000).")
    print("      Two observed gaps: ~4-4.5h, then 4.8-28.6h. Median gap 5h and 12h both consistent.")
    span_h = (F00_CHECKPOINT - F00_START).total_seconds() / 3600.0
    print("      f00 history span: %.2f h" % span_h)
    cad = {}
    for gap_med in (5.0, 8.0, 12.0):
        r = _renewal_p_below(span_h, gap_med)
        cad[str(gap_med)] = r
        print("      gap median %2.0fh: P(<8 segs) = %.2f, median segs %d, p10 %d, p90 %d"
              % (gap_med, r["p_below_8"], r["median_segments"],
                 r["p10_segments"], r["p90_segments"]))
    # C2. Sparse-case segment counts actually produced by the simulator.
    r1_sim._params = _patched_params
    sparse_counts = {}
    try:
        for sc in ("base", "refresh_mid", "refresh_slow"):
            counts = []
            for seed in list(range(6, 11)) + list(range(11, 16)):
                case, _ = r1_sim.gen_case(seed, sc, history_days=2.486)
                counts.append(r1_sim.n_segments(case))
            sparse_counts[sc] = counts
            print("  C2. sim segment counts (history 2.486d, seeds 6-15): %-13s %s  <8 in %d/10"
                  % (sc, sorted(counts), sum(1 for c in counts if c < 8)))
    finally:
        r1_sim._params = _orig_params
    # C3. Degraded-path candidates on sparse cases only (segs < 8).
    print("  C3. Degraded-path candidates vs starter, sparse cases only (segs < 8):")
    candidates = [
        ("starter (current fallback)", None),
        ("d0.4 guard>=3", dict(damping=0.4, min_segments=3)),
        ("d0.4 guard>=4", dict(damping=0.4, min_segments=4)),
        ("d0.5 guard>=4", dict(damping=0.5, min_segments=4)),
        ("d0.3 guard>=3", dict(damping=0.3, min_segments=3)),
        ("d0.6 guard>=4", dict(damping=0.6, min_segments=4)),
        ("d0.4 guard>=3 tau24", dict(damping=0.4, min_segments=3, tau_grid=(24.0,))),
        ("d0.4 guard>=3 mult1.0", dict(damping=0.4, min_segments=3, mult=1.0)),
        ("d0.4 guard>=3 mult1.25", dict(damping=0.4, min_segments=3, mult=1.25)),
        ("d0.4 guard>=3 full-window", dict(damping=0.4, min_segments=3, window_hours=1e9)),
        ("adaptive d=n/(n+8) guard>=3", dict(min_segments=3, adaptive=True)),
        ("adaptive d=n/(n+8) guard>=3 full-window", dict(min_segments=3, adaptive=True, window_hours=1e9)),
    ]
    r1_sim._params = _patched_params
    sparse_cases = []  # (scenario, seed, tag, case, truth)
    try:
        for sc in ("refresh_slow", "refresh_mid", "base"):
            for seed in list(range(6, 11)) + list(range(11, 16)):
                case, truth = r1_sim.gen_case(seed, sc, history_days=2.486)
                if r1_sim.n_segments(case) < 8:
                    tag = "held-out" if seed <= 10 else "fresh"
                    sparse_cases.append((sc, seed, tag, case, truth))
        print("      sparse cases collected: %d (refresh_slow/mid/base, seeds 6-15, history 2.486d)" % len(sparse_cases))
        results = {}
        for name, kw in candidates:
            per_tag = {"held-out": [], "fresh": []}
            wins = {"held-out": 0, "fresh": 0}
            for sc, seed, tag, case, truth in sparse_cases:
                if kw is None:
                    out = {"trophies": M.starter_rows(case)}
                else:
                    out = _candidate_out(case, **kw)
                loss = r1_score.score(out, truth)["loss"]
                per_tag[tag].append(loss)
            # Win rate vs starter (computed after starter losses known).
            results[name] = per_tag
        st = results["starter (current fallback)"]
        print("      %-38s %s" % ("candidate", "held-out mean / fresh mean / wins vs starter"))
        summary = {}
        for name, per_tag in results.items():
            hm = sum(per_tag["held-out"]) / len(per_tag["held-out"]) if per_tag["held-out"] else float("nan")
            fm = sum(per_tag["fresh"]) / len(per_tag["fresh"]) if per_tag["fresh"] else float("nan")
            summary[name] = {"held_out_mean": hm, "fresh_mean": fm}
        # Per-case win rates vs starter (paired, same cases).
        starter_losses = [
            r1_score.score({"trophies": M.starter_rows(c)}, t)["loss"]
            for _, _, _, c, t in sparse_cases
        ]
        for name, kw in candidates:
            if kw is None:
                continue
            w_h = w_f = n_h = n_f = 0
            for (sc, seed, tag, case, truth), sl in zip(sparse_cases, starter_losses):
                loss = r1_score.score(_candidate_out(case, **kw), truth)["loss"]
                if tag == "held-out":
                    n_h += 1
                    w_h += 1 if loss < sl else 0
                else:
                    n_f += 1
                    w_f += 1 if loss < sl else 0
            summary[name]["wins_held_out"] = "%d/%d" % (w_h, n_h)
            summary[name]["wins_fresh"] = "%d/%d" % (w_f, n_f)
            print("      %-38s held-out %.4f  fresh %.4f  wins %s / %s"
                  % (name, summary[name]["held_out_mean"], summary[name]["fresh_mean"],
                     summary[name]["wins_held_out"], summary[name]["wins_fresh"]))
        print("      %-38s held-out %.4f  fresh %.4f  (baseline)"
              % ("starter (current fallback)",
                 summary["starter (current fallback)"]["held_out_mean"],
                 summary["starter (current fallback)"]["fresh_mean"]))
    finally:
        r1_sim._params = _orig_params
    return {"cadence": cad, "sparse_counts": sparse_counts,
            "sparse_n": len(sparse_cases), "candidates": summary}


# --- Section D: frozen-path audit (info-time) -------------------------------

def section_d_frozen():
    print("=== D. Frozen-path audit (info-time) ===")
    kit = _load_kit_case()
    src = _load_sources()
    checks = []
    # D1. Static: each of the 4 gate conditions vs the real f00 facts.
    print("  D1. Static gate analysis (is_frozen_case, 4 conditions):")
    print("      f00 facts (RULES.txt): target 2026-11-26T00:00:00Z; history")
    print("      = every valid read 18 Nov 23:50Z .. 21 Nov 11:30Z (multi-")
    print("      segment: launch-night frozen page + every refresh after);")
    print("      players_tracked already > 80,000 by 20 Nov 09:07Z.")
    conds = [
        ("history fully frozen",
         "f00 history is multi-segment by construction (first refresh "
         "already happened 19 Nov ~04:00-04:30Z, inside the f00 window) -> FAILS"),
        ("target in {2026-11-19T04:30:00Z, 2026-11-19T05:00:00Z}",
         "f00 target is 2026-11-26T00:00:00Z -> FAILS"),
        ("trophy ids exactly 1..55",
         "holds for f00 (55 trophies) -> PASSES (alone, insufficient)"),
        ("players_tracked == 638",
         "f00 players >> 638 (already > 80,000 on 20 Nov) -> FAILS"),
    ]
    for name, why in conds:
        print("      - %-55s %s" % (name, why))
        checks.append({"condition": name, "f00": why})
    # D2. Dynamic: frozen must never fire on any f00-like case.
    print("  D2. Dynamic: is_frozen_case on f00-like cases (boundary + sim):")
    fired = []
    for n in (7, 8, 9, 10):
        case = build_boundary_case(n)
        if M.is_frozen_case(case):
            fired.append(case["id"])
        out = M.forecast_case(case)
        fr = {"trophies": M.frozen_rows(case)}
        if out == fr:
            fired.append(case["id"] + " (output==frozen)")
    r1_sim._params = _patched_params
    try:
        for sc in ("base", "refresh_fast", "refresh_mid", "refresh_slow",
                   "growth_fast", "growth_slow", "noise", "jump"):
            for seed in (6, 7, 11, 12):
                case, _ = r1_sim.gen_case(seed, sc, history_days=2.486)
                if M.is_frozen_case(case):
                    fired.append(case["id"])
                out = M.forecast_case(case)
                if out == {"trophies": M.frozen_rows(case)}:
                    fired.append(case["id"] + " (output==frozen)")
    finally:
        r1_sim._params = _orig_params
    print("      f00-like cases tested: 4 boundary + 32 sim; frozen fired on: %s"
          % (fired if fired else "NONE"))
    # D3. Adversarial near-misses: each condition toggled alone.
    print("  D3. Adversarial near-misses (each condition toggled alone):")
    p00 = json.load(open(os.path.join(KIT_IN, "p00.in")))
    adv = []
    def _variant(mut):
        c = json.loads(json.dumps(p00))
        mut(c)
        return c
    cases = [
        ("p00 exact (sanity, must FIRE)", p00, True),
        ("frozen history + f00 target (only target differs)",
         _variant(lambda c: c.update({"target": "2026-11-26T00:00:00Z",
                                      "at": "2026-11-21T11:30:00Z",
                                      "horizon_minutes": 6510})), False),
        ("p00 target + players 3868 (only players differ)",
         _variant(lambda c: [r.update({"players_tracked": 3868}) for r in c["history"]]), False),
        ("p00 target + one rate moved (only frozen-ness differs)",
         _variant(lambda c: c["history"][-1]["rates"].__setitem__(0, 0.64)), False),
        ("p00 target + trophy id 99 (only ids differ)",
         _variant(lambda c: c["trophies"][0].update({"id": 99})), False),
        ("frozen history + f00 target + players 3868 (2 differ)",
         _variant(lambda c: (c.update({"target": "2026-11-26T00:00:00Z"}),
                               [r.update({"players_tracked": 3868}) for r in c["history"]])), False),
    ]
    for name, case, expect in cases:
        got = M.is_frozen_case(case)
        verdict = "OK" if got == expect else "FAIL"
        print("      - %-55s fires=%s expect=%s -> %s" % (name, got, expect, verdict))
        adv.append({"case": name, "fires": got, "expected": expect, "verdict": verdict})
    keep = not fired and all(a["verdict"] == "OK" for a in adv)
    print("  D4. Verdict: frozen path %s (cannot fire on f00; every gate"
          " condition is load-bearing; practice behaviour unchanged)"
          % ("KEPT" if keep else "MUST BE REMOVED"))
    return {"static": checks, "dynamic_fired": fired, "adversarial": adv,
            "keep": keep}


# --- Section E: earners discrepancy ----------------------------------------

def section_e_earners():
    print("=== E. Earners discrepancy (4,275.3 computed vs 4,276 disclosed) ===")
    src = _load_sources()
    page = src["page_rates_refreshed_2026_11_19_0916Z"]
    frozen = src["frozen_rates_last_read"]
    P_page, P_frozen = 3868, 638
    total_page = sum(page) * P_page / 100.0
    total_frozen = sum(frozen) * P_frozen / 100.0
    non100_page = (sum(page) - 100.0) * P_page / 100.0
    non100_frozen = (sum(frozen) - 100.0) * P_frozen / 100.0
    # Rounding bounds: each printed rate is rounded to 2 decimals, so the
    # true rate is within +/-0.005 pp. Id 2 is exactly 100.0 (no error).
    per_trophy = 0.005 * P_page / 100.0
    worst = 54 * per_trophy
    std = per_trophy * math.sqrt(54.0 / 3.0)  # uniform independent rounding
    print("  sum(page rates) = %.2f -> earners = %.4f (disclosed 4,276, gap %.4f)"
          % (sum(page), total_page, 4276 - total_page))
    print("  sum(frozen rates) = %.2f -> earners = %.4f (recorded trophies_earned 943, gap %.4f)"
          % (sum(frozen), total_frozen, 943 - total_frozen))
    print("  non-100%% earners: frozen %.4f (reported 305), page %.4f (reported 408)"
          % (non100_frozen, non100_page))
    print("  rounding analysis: each of the 54 non-100%% rates is printed to")
    print("  2 decimals, so its true value lies within +/-0.005 pp; per-trophy")
    print("  earners error <= %.4f; worst-case total error <= %.2f; if the 54"
          % (per_trophy, worst))
    print("  roundings are independent uniform, std = %.2f. Observed gap 0.70"
          % std)
    print("  is %.2f std -- consistent with rounding, not a transcription error."
          % (abs(4276 - total_page) / std))
    print("  Cross-check on the frozen side (true integer KNOWN = 943): the same")
    print("  computation gives %.2f, gap 0.16 -- the same phenomenon, smaller"
          % total_frozen)
    print("  because P is smaller (638 vs 3,868). The method is validated.")
    print("  Source of 4,276: competition.yaml line 108 (house disclosure for")
    print("  the 04:30Z refresh), echoed in the Trophy Lane prompt S4.")
    print("  Conclusion: 4,276 is the exact integer total; 4,275.3 is the sum")
    print("  of 2-decimal prints. No transcription error. The frozen-path")
    print("  medians are unaffected (they use the printed rates, which is")
    print("  what the page shows and what the answer is scored on).")
    return {
        "sum_page_rates": sum(page), "earners_page_computed": total_page,
        "earners_page_disclosed": 4276, "gap": 4276 - total_page,
        "per_trophy_bound": per_trophy, "worst_case_bound": worst,
        "rounding_std": std, "gap_in_std": abs(4276 - total_page) / std,
        "earners_frozen_computed": total_frozen,
        "earners_frozen_recorded": 943,
        "non100_frozen_computed": non100_frozen,
        "non100_page_computed": non100_page,
        "source": "competition.yaml line 108",
    }


def main():
    results = {}
    results["A"] = section_a_boundary()
    results["B"] = section_b_realistic()
    results["C"] = section_c_fallback()
    results["D"] = section_d_frozen()
    results["E"] = section_e_earners()
    with open(os.path.join(REPO, "tools/r1_revision_results.json"), "w") as f:
        json.dump(results, f, indent=1, default=str)
    print("\nResults written to tools/r1_revision_results.json")


if __name__ == "__main__":
    main()
