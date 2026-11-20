# RANK1-REVISION: Third-Family Simulator Report

Branch `rank1-revision` (from ec33def). Analyst work for the Codex
fix-design, priority 6 (simulator circularity). No change to `main.py`
in this report's experiment — the candidate under test is the revised
`main.py` (frozen-gate circuit breaker + docstring fix), whose outputs
were already proven byte-identical to ec33def on all 160 battery cases.

## The third family: random walk + calendar shocks (`tools/r1_rw_sim.py`)

A deliberately different model family from both existing evaluators:

| Family | Trophy dynamics | Trend shape |
|---|---|---|
| Hazard (`tools/r1_sim.py`) | earners follow dF/dt = h·(1−F), joiner jumps toward j_i, asymptote 1 | hazard-driven |
| Cohort (challenger `tools/misspec.py`) | cohort asymptotes a_i, log-time trend | log(1+t/τ) |
| **Random walk (NEW)** | rates are a **random walk in logit space** with weak mean reversion + discrete **calendar shocks** (weekend / patch / viral moments) | **none — no log(1+t/τ), no hazard, no asymptote** |

The forecaster's Theil–Sen-on-log-time trend is the WRONG shape for
this world on purpose: if the rich path still beats the starter here,
the edge is not an artifact of sharing the trend family with the
evaluator. Population P(t) still grows by power law and refreshes are
still a LogNormal renewal process (facts of the setting, not trophy
dynamics); observed rates are rounded to 2 decimals; truth is the last
refresh at/before the target (RULES definition). Levels are anchored to
the real frozen page + live snapshot. Protocol mirrors hazard: 7
scenarios × seeds 1–15 (train 1–5 / held-out 6–10 / fresh 11–15),
history 2.486d (the real f00 span), horizon 4.5208d.

Scenarios: base, rw_fast (σ 0.10/step), rw_slow (σ 0.02), shock_heavy
(5 shocks, σ 0.6), shock_none (0 shocks), growth_fast (P^4),
growth_slow (P^2).

## Results: rich path vs house starter (same scorer, pinball-logit ×2)

| Family | n | rich mean | starter mean | rich wins |
|---|---|---|---|---|
| H hazard | 105 | 0.2374 | 0.4363 | 100/105 (95%) |
| C cohort | 50 | 0.1503 | 0.2566 | 50/50 (100%) |
| R random-walk | 105 | 0.1573 | 0.2439 | 104/105 (99%) |

Per-group (hazard): train 0.2233 vs 0.4323 (33/35), held-out 0.2117
vs 0.4179 (34/35), fresh 0.2773 vs 0.4587 (33/35) — reproduces the
replication numbers exactly, a second confirmation the revised
`main.py` behaves identically to ec33def on the rich path.

Per-group (random-walk): train 0.1650 vs 0.2473 (34/35), held-out
0.1494 vs 0.2398 (35/35), fresh 0.1574 vs 0.2447 (35/35).

Per-scenario (random-walk): base 0.1487 vs 0.2406 (15/15), rw_fast
0.1880 vs 0.2640 (15/15), rw_slow 0.1377 vs 0.2296 (15/15),
shock_heavy 0.1906 vs 0.2577 (14/15), shock_none 0.1388 vs 0.2343
(15/15), growth_fast = growth_slow = base (see caveat below).

Per-scenario (hazard): weakest is refresh_slow 0.4838 vs 0.5387
(10/15); all others 15/15. Per-scenario (cohort): all 10 scenarios
5/5, weakest no-refresh-24h 0.2039 vs 0.2722.

**Verdict: rich beats starter on mean loss in 3/3 families.**
The Codex acceptance rule ("consistent in ≥ 2 independent families")
is met with margin. The circularity concern is mitigated, not erased:
all three families are still synthetic.

## Honest caveats

1. **Growth scenarios duplicate base in the RW family.** In this
   simulator, trophy rates evolve purely in logit space and never
   depend on P(t); growth_fast/growth_slow change only
   `players_tracked`, which does not enter the forecast. So the RW
   family really tests 5 distinct dynamics (base, rw_fast, rw_slow,
   shock_heavy, shock_none), not 7. All 5 still show rich winning.
2. **One RW loss, one weak hazard scenario.** shock_heavy loses 1/15
   cases to the starter; hazard refresh_slow wins only 10/15. The
   edge is broad, not universal.
3. **This does NOT address Leg B.** All three families are synthetic.
   The single real-data test (Leg B, ~2.99 for every model, none
   beats the starter) still stands, and no simulator can fix it.
   Final Rank-1 equivalence remains unproven.
4. The comparison ran on the revised `main.py`; because its outputs
   are byte-identical to ec33def on every battery case, these numbers
   apply equally to ec33def.

## Reproduction

```
python3 tools/r1_third_family_compare.py   # ~20 s, prints all tables
python3 tools/r1_rw_sim.py 1 base          # single RW case sanity check
```

Script: `tools/r1_rw_sim.py`. Comparison: `tools/r1_third_family_compare.py`.
Summary JSON: `/tmp/r1_third_family_summary.json`.
