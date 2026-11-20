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
