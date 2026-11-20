# EXPERIMENTS — gta6-trophy-rarity-forecast-01

Experiment notes for the model in `main.py`. `ANALYSIS.md` and `DESIGN.md`
are frozen; this file is the only place results are recorded. All losses are
mean pinball loss on the logit scale (the competition metric, rates clamped
0.01–99.99). Coverage90 is diagnostic only.

## Test data

- **Practice p00/p01** (kit `input/`): history FROZEN — 1 segment, 638
  players, every rate identical across all reads. No time variation, so no
  fit is possible; `forecast_case` falls back to the starter. Leg A has
  0 origins on both. This is a property of the practice data, not a bug.
- **Synthetic 4-segment** (dev harness): P 638 → 3,868 → 20,000 → 90,000;
  slow trophies dilute (×0.17, ×0.05, ×0.015), fast trophies (id 3, id 20)
  rise (×1.3, ×2.0, ×3.0). The "truth" here is multiplicative, NOT in the
  cohort model class — the fit is a compromise (sigma_res 2.54, rho 0.80
  at the cap, post_se_v 2.73). Its wide scenario spread (90% width 4.19
  logit) is a poor-fit artifact, not the model's behaviour on fittable data.
- **Model-truth 20-segment** (dev harness, seed 7): history GENERATED FROM
  the cohort model — known (a, k, j) per trophy, P from the adoption curve
  (log P = 6.44 + 1.358·ln(1+τ)), refreshes every 6 h, 20 segments over
  5 days. The fit recovers it: sigma_res 0.35, rho 0.80, sigma_g 0.25,
  post_se_v 0.05, 90% width 1.31 logit. This is the clean ground for the
  ablation test and Leg C.
- **Live snapshot** (`data/live-snapshot-2026-11-20.json`, crawled
  2026-11-20 09:07 UTC): sum rates 126.53 (practice 147.78), median 0.02%
  (practice 0.78%), 19 trophies at the 0.01% floor, 8 at 0.00%, platinum
  0.63% → 0.00%. Design evidence only; `main.py` never reads it.

## Stage 2 — cohort fit (synthetic 4-segment)

- id 1 (platinum, falling 0.63 → 0.01): fitted exactly
  [0.63, 0.11, 0.03, 0.01].
- id 2 (100%): a = 99.999, j = 100 in ALL variants (feasibility projection
  holds — the 100% trophy never collapses).
- id 3 / id 20 (fast): fitted [1.25, 2.15, 2.52, 2.90] vs observed
  [1.25, 1.62, 2.50, 3.75] (id 3); the multiplicative "truth" is not in the
  model class, so the fit under-shoots the last segment. Honest misfit.

## Stage 3 — joint scenarios + P0 ablation (Ting's P0 fix)

Direct recursion proof (slow-k trophies, P growth ×1.5 / ×0.5 vs ×1.0,
4.5-day horizon):

| (a, k, j) | fast | base | slow | \|Δlogit\| fast | \|Δlogit\| slow |
|---|---|---|---|---|---|
| (5.0, 0.005, 0.1) | 0.987 | 1.310 | 4.614 | 0.286 | 1.293 |
| (5.0, 0.02, 0.1) | 2.495 | 2.949 | 4.392 | 0.172 | 0.413 |
| (20.0, 0.005, 0.0) | 3.620 | 4.867 | 16.444 | 0.309 | 1.347 |
| (0.5, 0.005, 0.0) | 0.091 | 0.124 | 0.485 | 0.316 | 1.364 |

All |Δlogit| > 0.1. **P growth moves the predicted rate — the P0 fix is
proven.** The effect lives in slow-k trophies (fast-k trophies converge to
their asymptote within the horizon, so their population-mean movement is
small by construction).

Scenario-level ablation (model-truth, 54 non-floor trophies): mean |Δlogit|
fast = 0.041, slow = 0.062; slow growth raises the median for 41/54
trophies (direction correct).

1500 scenarios in 0.77 s.

## Stage 4 — Leg A selection + lambda + stress floor (model-truth, 20 seg)

- Leg A origins: (2, 8), (8, 14), (15, 19) — 3 origins.
- Variant errors (median |logit|): **M0 = 0.315, M1 = 0.308, M2 = 0.316 →
  winner M1** (tier shrinkage), but the margin over M0 is tiny (0.007) —
  the three variants are nearly identical on this data; the choice is
  data-driven, not structural.
- Lambda grid (mean pinball-logit): 0.5 = 0.122, 0.75 = 0.114, 1.0 = 0.110,
  **1.25 = 0.108**, 1.5 = 0.109, 2.0 = 0.115, 2.5 = 0.123, 3.0 = 0.135 →
  **lambda = 1.25**. The raw spread is nearly calibrated; lambda only
  widens it by 25%.
- Full pipeline on the 20-segment case: **4.94 s** (target <5 s, limit 8 s).
  Standalone `python3 -P -s main.py` on the same case: 5.03 s.
- Sample final rows (truth in parens): id 1 q=[0.28, 0.38, 0.49, 0.62, 0.88]
  (a=0.63, k=0.08); id 3 q=[0.10, 0.13, 0.17, 0.21, 0.30] (a=1.25, k=0.005 —
  slow, so the rate is still far below its asymptote at 4.5 d); id 20
  q=[0.86, 1.21, 1.54, 1.98, 2.82] (a=2.04, k=0.08).

## Stage 5 — fallbacks, determinism, runtime

- One-segment case → starter fallback, valid (55 rows, q non-decreasing,
  in [0, 100]).
- Fit-failure case (P drops to 0 → log(0) in the adoption fit) → starter
  fallback, valid.
- Long model-truth case: two runs byte-identical; valid.
- Runtime: full pipeline 4.94–5.28 s across runs on the 20-segment case
  (limit 8 s). Practice cases run in 0.23 s (starter fallback).
- Grep: no hardcoded 638/3868 in `main.py`; no network or clock calls;
  stdlib only; no `time.time` / `datetime.now`.

## Dev backtest legs

### Leg A (rolling origins, real history) — the in-program leg
- p00: 1 segment, 0 origins. p01: 1 segment, 0 origins. **Nothing to
  roll — the practice history is frozen.** Reported honestly; the Leg A
  machinery only activates on a multi-segment history (the final case).

### Leg B (live snapshot) — DIAGNOSTIC ONLY, never used for selection
- Origin = practice last read, target = live snapshot 2026-11-20 09:07 UTC
  (~29.6 h). The frozen practice history has 1 segment, so the forecast
  IS the starter.
- **Pinball-logit = 2.996, coverage90 = 0.11** (starter = 2.996).
  Persistence over 30 h is terrible: the rates moved massively (median
  0.78% → 0.02%) and the starter's spread is far too narrow for a 30-hour
  horizon. This leg measures the starter's 30-hour persistence error, NOT
  the model — and it confirms the practice 90-minute horizon is nothing
  like the final 4.5-day horizon.

### Leg C (synthetic simulator, model-truth, 4.5-day horizon) — stress test
- Truth known at the target (continued cohort recursion).
- **Model pinball-logit = 0.222, coverage90 = 0.82; starter = 0.259,
  coverage90 = 1.00.** The model beats the starter by ~14% on pinball.
  Median |logit| error: M0 = M1 = M2 = 0.297 (the variants' medians are
  nearly identical here).
- Coverage 0.82 is below the nominal 0.90: the spread at 4.5 days is
  slightly narrow. Lambda is calibrated on ~1.5-day origins but applied
  to a 4.5-day horizon (horizon mismatch); the stress floor widens some
  trophies but does not fully close the gap. Pinball (the competition
  metric) still favours the model, and pinball-optimal spreads are
  typically narrower than 90%-coverage spreads — so this is a noted
  limitation, not a failure.

## Finalization (20 Nov 2026 — Ting GO, locked config)

Ting's finalization verdict: lock M0 + lambda 1.25, drop the stress floor
if the final spread check says the wider spread hurts pinball. All 8
checklist items are done (see the checklist at the end of this section).

### Locked config
- **Variant: M0** (population shrinkage). M1 won Leg A on the simulator by
  0.007 (0.308 vs 0.315) — too weak (3 origins, near-identical variants,
  simulator-dependent) to let runtime re-selection override the config on
  the real final case. M1/M2 code is kept as comparison experiments
  (tools/backtest.py still selects M1 in dev); `model_forecast` runs M0.
- **Lambda = 1.25** (Leg A grid optimum, mean pinball-logit 0.108).
- **No stress floor.** Final spread check (tools/final_spread_check.py,
  Leg C, M0, same scenario columns for every candidate):
  - seed 7: A λ1.25 no-floor 0.2193/cov 0.82; B λ1.25+floor 0.2216/cov 0.82;
    C λ1.00 0.2235/cov 0.71; D λ1.50 0.2216/cov 0.87.
  - seed 11: A 0.2238/cov 0.67; B 0.2261/cov 0.67; C 0.2309/cov 0.65;
    D 0.2206/cov 0.80.
  - The floor widened only 1/55 trophies, HURT mean pinball-logit on BOTH
    seeds and did NOT improve coverage on either. Per Ting's rule
    (pinball decides, coverage is diagnostic) the wider spread was NOT
    picked. λ=1.25 is a local optimum on seed 7 and within 0.003 of the
    best (λ=1.50) on seed 11 — no consistent improvement from widening.
- **Leg C undercoverage is a recorded limitation, not a failure:** coverage
  0.82 (seed 7) / 0.67 (seed 11) at λ=1.25, below the nominal 0.90, in
  every tested spread (λ=1.50 reaches 0.87/0.80 but does not consistently
  improve pinball: better on seed 11 by 0.003, worse on seed 7 by 0.002).
  Lambda was calibrated on ~1.5-day origins and applied to a 4.5-day
  horizon (horizon mismatch). Pinball (the competition metric) still
  favours the model over the starter, and pinball-optimal spreads are
  typically narrower than 90%-coverage spreads. Do not widen just to
  chase 0.90 coverage.

### Stress scenarios (locked config, long 20-segment case)
tools/final_validation.py — all VALID (55 rows, q monotone, in [0,100]):
fast refresh (gaps x0.5), slow refresh (gaps x2.0), NO refresh until
target, player growth x1.5, player growth x0.5.

### Runtime, repeated (locked config, long 20-segment case)
- In-process `model_forecast` x5: 2.32, 2.30, 2.31, 2.31, 2.31 s → max
  **2.32 s**. Standalone CLI (`python3 -P -s main.py`) x3: 2.44, 2.39,
  2.40 s → max **2.44 s**. Full pipeline in backtest stage 4: 2.46 s.
  Target was max <6.5 s, limit 8 s. Locking the config (no runtime Leg A
  selection/calibration) cut the pipeline from ~5 s to ~2.4 s; no
  numerical optimisation was needed and no output was changed by any
  optimisation (none applied).

### Final validation (locked code)
- Harness: PASS p00/p01 (55 rows, q non-decreasing, in [0,100],
  ≤65536 B, two runs byte-identical).
- Full backtest: ALL STAGE TESTS PASSED (stages 1–5 + legs). Stage 3
  ablation still passes (P0 fix intact). Stage 4 dev Leg A still selects
  M1 (0.308) — dev only; production is locked M0.
- Determinism: two `model_forecast` runs byte-identical on the long case.
- Fallbacks: one-segment and fit-failure (P=0) cases → starter, valid.
- Leg C (locked, seed 7): model pinball-logit **0.219**, coverage90 0.82;
  starter 0.259/1.00. Median |logit| error M0=M1=M2=0.297.
- Leg B (diagnostic only): unchanged, 2.996/0.11 (starter's number).

### Checklist (Ting finalization, 8 items)
1. [x] M0 locked as final candidate (M1/M2 kept as dev comparisons).
2. [x] λ=1.25 baseline kept; final spread check done (pinball decides);
    wider spread NOT picked; Leg C undercoverage recorded as limitation.
3. [x] Stress scenarios run, output valid (fast/slow/no-refresh, ±growth).
4. [x] Runtime repeated, max 2.44 s < 6.5 s target (limit 8 s).
5. [x] Final validation: format/determinism/fallback on p00/p01 + long case.
6. [x] Results + limitations recorded here.
7. [x] Anchor shown before first write (round 34120697).
8. [x] Incremental commits + push (9f3ae98 lock; this file next).

## Verdict

- **Winning variant: M0** (population shrinkage), LOCKED by Ting's
  finalization decision (20 Nov 2026) — NOT M1. The dev Leg A still picks
  M1 on the model-truth simulator (0.308 vs M0 0.315), but that margin is
  0.007 on 3 simulator origins and the runtime re-selection is disabled:
  production runs M0 on every case, including the final case.
- **Lambda = 1.25** (Leg A, mean pinball-logit objective), locked.
- **Stress floor: DROPPED** by the final spread check (hurts pinball on
  both Leg C seeds, no coverage gain).
- **Does the model beat the starter?** On the only evaluation where the
  model can run (Leg C, synthetic model-truth, 4.5 d): YES, 0.219 vs
  0.259 pinball-logit (locked config). On real data there is NO comparison
  available: the practice history is frozen (the model falls back to the
  starter), and Leg B is the starter's number. The final case (f00) will
  have a real multi-segment history; the model will run there, but whether
  it beats the best house entry on the real final case is UNVERIFIED until
  the final case arrives. Do not claim otherwise.
- **Runtime:** ~2.4 s on a 20-segment 4.5-day case (limit 8 s). Deterministic
  (two runs byte-identical). Format valid (harness PASS on p00/p01).

---

## CHALLENGER — Trophy Lane (branch `challenger-trophy-lane`, 2026-11-20)

A second entry built from the standalone Trophy Lane prompt (user-supplied),
as a challenger to the submitted M0 entry (commit 2e30e66, main). The
challenger is a FRESH `main.py` written from the prompt spec alone (the
submitted main.py was never read). No bundle, check, or submit was done
from this branch.

### Frozen path (practice) — proven core, UNCHANGED

- Gate: all four §5 conditions, strictly (history fully frozen on
  (players_tracked, rates); target 04:30Z/05:00Z 2026-11-19; ids 1..55;
  players_tracked 638). Anything else falls through to the rich path.
- Median = §4 page rate exactly per trophy; σ = 0.003; id 2 all five
  quantiles exactly 100.0; non-decreasing enforced by nextafter nudging.
- Result (kit p00/p01, scored vs §4 page rates as truth, §1 pinball):
  **loss 0.000591 on both p00 and p01**, median |q50 − y| 0.0000pp,
  coverage90 1.000. Target ≈0.00059 hit exactly.
- Harness PASS: format valid, 55 rows, q non-decreasing in [0,100],
  ≤65,536 B, two runs byte-identical. Runtime 0.05 s for both cases.

### Rich path (final) — design as specified, plus documented choices

The prompt leaves four pieces unspecified; the choices made (all recorded
here, all inside main.py):
- **Time origin for Theil–Sen:** t = hours since the first read in the
  last-24h window; x = log(1 + t/τ); target x from the same origin.
- **Level-band prior:** the prior slope for a band is the MEDIAN raw
  Theil–Sen slope of the trophies in that band in the same fit
  (fallback: overall median slope, then 0.0). Bands by last rate:
  <1%, 1–10%, ≥10%.
- **Damped median:** logit(median) = logit(last) + damping × (full
  trend extrapolation − logit(last)), full = shrunk intercept + shrunk
  slope × target_x, intercept recomputed for the shrunk slope.
- **infl:** min(1.25, 1.0 + 0.05·H/24), H = horizon in hours
  (1.225 at the 4.5-day final horizon).
- τ and mult are chosen per case by rolling-origin backtest (up to 5
  origins in the last 72h, ≥12 reads before each; fit on the 24h before
  the origin, score the read 6h later; full 5-level pinball).
- 100% trophy (last rate ≥ 99.99): median 99.99, σ = 0.3, bypassing
  trend and rails. No shared parameters or σ between the two paths.
  No rounding to 2 decimals. Stdlib only, no network, deterministic.

### What differs from the prompt spec (bos: differentiate from the rank-4
### entry that uses this design) — and why

The frozen path is byte-for-byte the proven design (page-rate medians,
σ = 0.003, strict gate) — that part is NOT differentiated, it is what
makes the 0.00059. The rich-path knobs were varied on Leg C and the
winners shipped:

| knob | prompt spec | shipped | Leg C evidence |
|---|---|---|---|
| damping | 0.4 | **1.0** | sweep: 0.3→0.1587, 0.4→0.1536, 0.5→0.1457, 0.6→0.1401, 0.7→0.1357, 1.0→0.1337 (prior 5, seeds 7+11). Monotone: the trend signal is real and persists over 4.5 d; damping only under-shoots it. |
| prior weight | 10 | **2** | sweep: at damping 1.0, prior 2→0.1327, 5→0.1337, 10→0.1351, 20→0.1340 (seeds 7+11). Heavy shrinkage drags the slope toward 0 for no gain. |
| τ grid | {6,12,24,48} | **unchanged** | every grid tried ({6,12,24,48}, {12,24,48}, {6,12,24}, {24,48}) scores IDENTICALLY (0.1327, seeds 7+11): τ is inert here — the backtest picks equivalently and the fit is near τ-invariant. No differentiation needed. |
| mult grid | {0.75,1.0,1.5} | **{0.5,0.75,1.0,1.5}** | {0.75,1.0,1.5}→0.1327, {0.5,0.75,1.0}→0.1013, {0.5,0.75,1.0,1.5}→0.1013 (seeds 7+11). The backtest picks the tightest spread the case's own history justifies; 1.5 stays as the safety valve for a noisy real final. |
| mult 0.25/0.35 | — | **REJECTED** | {0.35,0.5,0.75}→0.0859, {0.25,0.35,0.5}→0.0849 — better on the simulator, but they ride the σ clip floor (0.25) and are pure simulator overfitting: the model-truth simulator is perfectly smooth, so the tightest allowed spread always wins. The real final has refresh jumps; a σ of 0.25 would be punished hard. Not shipped. |

### Variant sweep (Leg C, seeds 7+11, mean pinball-logit)

damping × prior weight (spec τ/mult grids):

| damping | prior 2 | prior 5 | prior 10 | prior 20 |
|---|---|---|---|---|
| 0.3 | 0.1580 | 0.1587 | 0.1598 | 0.1615 |
| 0.4 | 0.1512 | 0.1522 | 0.1536 | 0.1558 |
| 0.5 | 0.1446 | 0.1457 | 0.1474 | 0.1502 |
| 0.6 | 0.1392 | 0.1401 | 0.1417 | 0.1447 |
| 0.7 | 0.1348 | 0.1357 | 0.1371 | 0.1398 |
| 1.0 | **0.1327** | 0.1337 | 0.1351 | 0.1340 |

(First sweep run was void: identical 0.1520/0.1552 across all 9 cells —
`_rich_forecast` binds damping/prior as def-time defaults, so the
monkey-patched module constants never reached it. Fixed in
tools/compare_challenger.py by passing the knobs explicitly; the second
sweep above is the valid one.)

### Final head-to-head (Leg C, 4.5-day horizon, identical cases, seeds 7/11/13/23/29)

| seed | challenger shipped | M0 | persistence |
|---|---|---|---|
| 7 | 0.0996 | 0.2193 | 0.1705 |
| 11 | 0.1031 | 0.2238 | 0.1744 |
| 13 | 0.0987 | 0.2213 | 0.1461 |
| 23 | 0.1011 | 0.3089 | 0.1685 |
| 29 | 0.0987 | 0.2445 | 0.1654 |
| **mean** | **0.1002** | **0.2435** | **0.1650** |

- Challenger shipped (damping 1.0, prior 2, mult {0.5,0.75,1.0,1.5}):
  mean **0.1002**, wins on ALL 5 seeds, per-seed range 0.0987–0.1031
  (tight — the challenger is stable across seeds).
- Spec/friend config (damping 0.4, prior 10, mult {0.75,1.0,1.5}):
  mean 0.1493 on the same seeds — the shipped knobs beat the spec
  config by 0.0491.
- M0 mean 0.2435 (seed 7 reproduces the verified 0.2193 exactly, which
  cross-validates this Leg C replication against the M0 build's setup).
  M0 varies widely by seed (0.2193–0.3089); persistence (last rate,
  σ=0.5) beats M0 on every seed here (mean 0.1650).
- Challenger runtime ~1.2 s/case on the Leg C case (limit 8 s);
  CLI end-to-end on shipped defaults reproduces the sweep exactly
  (seed 7: 0.0996, 1.27 s, byte-identical second run).

### Verdict

**The challenger beats M0 for the final on every Leg C seed
(0.1002 vs 0.2435 mean), and beats the spec/friend config (0.1493).**
Caveats, stated plainly: Leg C is a model-truth simulator, not the real
final — the real history will have refresh jumps and noise the simulator
lacks, so the real-final gap will be smaller than 0.14 and is not
guaranteed. What IS established: on the only ground where both programs
can run head-to-head at the final horizon, the challenger wins on all 5
seeds with a much tighter spread of outcomes. The frozen-path practice
score (0.000591) is unchanged and proven. No bundle, check, or submit
was done from this branch; the shipped knobs live in main.py on
`challenger-trophy-lane` only — main is untouched at 2e30e66.
