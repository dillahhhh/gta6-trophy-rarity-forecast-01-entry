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

## Verdict

- **Winning variant: M1** (tier shrinkage), chosen by Leg A on the
  model-truth simulator — but M0 is within 0.007, so the choice is weak
  evidence; on the final case the Leg A machinery re-selects from the
  real history.
- **Lambda = 1.25** (Leg A, mean pinball-logit objective).
- **Does the model beat the starter?** On the only evaluation where the
  model can run (Leg C, synthetic model-truth, 4.5 d): YES, 0.222 vs
  0.259 pinball-logit. On real data there is NO comparison available:
  the practice history is frozen (the model falls back to the starter),
  and Leg B is the starter's number. The final case (f00) will have a
  real multi-segment history; the model will run there, but whether it
  beats the best house entry on the real final case is UNVERIFIED until
  the final case arrives. Do not claim otherwise.
- **Runtime:** ~5 s on a 20-segment 4.5-day case (limit 8 s). Deterministic
  (two runs byte-identical). Format valid (harness PASS on p00/p01).
