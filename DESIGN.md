# DESIGN (revised) — gta6-trophy-rarity-forecast-01

Status: REVISION for Ting's review. This stage is design + live data only.
No model code, no forecast, no submit. Ting must give GO on this design first.

Competition: forecast, for each of GTA VI's 55 PS5 trophies, its earn rate on
Exophase at 2026-11-26T00:00:00Z, as five quantiles (levels 0.05, 0.25, 0.5,
0.75, 0.95). Final case f00: cut at the deadline 2026-11-21T11:30:00Z,
horizon 6,510 minutes = 4.5208 days. Score: mean pinball loss on the logit
scale over every trophy and quantile (rates clamped to 0.01–99.99 for the
logit). Prize: $250, 20 places; a prize needs a loss strictly below the best
house entry (house baseline and house curve baseline both score ~1.51 on the
practice). Program: `python3 /bot/main.py`, stdlib only, no network,
<= 8 s, 1 CPU, deterministic (two runs byte-identical).

Sources: kit RULES.txt, ANALYSIS.md (practice p00/p01),
data/live-snapshot-2026-11-20.json (live Exophase crawl 2026-11-20 09:07 UTC).

## 0. What Ting's REVISI changed (vs the pre-review design)

1. JOINT forecast of earners and players. The old idea — forecast a median
   for earners E and a median for players P and divide — is wrong:
   Q0.50(E/P) != Q0.50(E)/Q0.50(P), and E and P are strongly dependent (both
   jump at a refresh). The design below simulates joint scenarios of (P(t),
   E_i(t)) and takes quantiles of the RATIO inside each scenario.
2. REFRESH-AWARE model. An Exophase refresh is its own process: one refresh
   moves players AND earners for all 55 trophies at once. The 10-minute
   reads are not independent observations; the frozen stretches between
   refreshes carry no new information. The model estimates when the next
   refresh happens and, crucially, when the LAST refresh before the target
   happens — that refresh's rates are the answer.
3. LONG-HORIZON rolling backtest for model selection and calibration
   (horizons 1, 2, 3, 4, ~4.5 days, same pinball-logit metric). The 90-minute
   practice must NOT select the model: its history is frozen and its loss is
   dominated by a single refresh event.

## 1. Facts the design must respect

- Practice history is FROZEN (ANALYSIS.md): 638 players, 943 trophies earned,
  0 of 55 trophies moved across all 7–8 reads. The only real event in the
  practice data is the refresh RULES.txt documents: at 04:30:00Z the page
  refreshed to 3,868 players, every rate but the 100% one lower.
- The final's history will NOT be frozen: by the deadline it spans
  2026-11-18T23:50Z → 2026-11-21T11:30:00Z (~2.7 days), 30-min reads early,
  10-min reads on the final recorder, and several refreshes. The program
  must FIT every parameter from that history at runtime. Nothing about the
  player level, refresh times, or trophy levels may be hardcoded from the
  practice values or from the live snapshot — the snapshot is design
  evidence, not constants in the code. The program must not read it.
- Live snapshot (2026-11-20 09:07 UTC): sum of the 55 rates 126.53 (practice
  last read: 147.78); median rate 0.02% (practice: 0.78%); 19 trophies at the
  0.01% floor, 8 at 0.00%; platinum 0.63% -> 0.00%; id 2 still 100%.
  players_tracked was NOT readable from the fetch (header not extracted);
  a derived lower bound is P > 80,000 (from the platinum's >= 4 earners and
  its 0.00% print — see the snapshot file). The player base grew at least
  ~125x since launch night. Dilution, not progression, is the dominant
  effect so far.
- The answer is the first valid read in [2026-11-26T00:00:00Z, 01:00:00Z):
  the rates as of Exophase's last refresh before that read. So the forecast
  is a forecast of a REFRESHED page, not of the true underlying play.
- Rates are printed to 2 decimals; earners are integers on a 1/P grid.
  The 2-decimal rounding is observation noise the fit must carry, not exact
  values.

## 2. Architecture (five stages)

### Stage 0 — Compress the history into refresh segments

Group consecutive reads into segments split at detected refreshes. A refresh
is detected when players_tracked changes OR any rate changes beyond 2-decimal
rounding. Within a segment every read is identical: it is one observation,
not N. The effective data is the list of segments:
(segment start, segment end, P, E_i implied per trophy, trophies_earned).
E_i is recovered as round(rate_i * P / 100) and kept with its rounding
interval (±0.005 pp) as observation noise. If the history has only one
segment (as the practice does), the fit has no time variation — see §4 for
what that does and does not allow.

### Stage 1 — Player model: adoption + refresh process

Two parts, fit on segments only.

(a) Adoption P(t). Fit log P(t) = a + b * ln(1 + (t - t_release)/1h) by least
squares on segment starts (t_release = 2026-11-19T00:00:00Z from the case's
game.release_date; the program reads it from the case, it does not hardcode
the date). The form is concave in log-time: it can keep growing but cannot
explode. Extrapolated daily growth is capped at the largest daily growth
observed between segments (no re-acceleration beyond what was seen). A
linear-in-time log P form is kept as a backtest variant (§4); the backtest
chooses, the designer does not.

(b) Refresh process. The gaps between detected refreshes give an empirical
gap distribution; fit a lognormal to the gaps (fallback: the empirical gaps
with replacement when there are too few to fit). The process is a renewal
process CONDITIONED on the age of the last observed refresh at the
checkpoint (known exactly: checkpoint minus last segment start). Each
scenario simulates refresh times forward from the checkpoint until past the
target and takes t* = the last refresh at or before the target. If no
refresh occurs before the target, t* is the last refresh before the
checkpoint — the rates are then the checkpoint's rates, and the scenarios
must include that branch (it is a real possibility, not an edge case to
ignore).

### Stage 2 — Trophy model: earners growth + saturation, hierarchical

For trophy i, model its rate as a saturating curve in clock time since
release:

    r_i(t) = a_i * (1 - exp(-k_i * max(0, t - t0))),  t0 = release

with per-trophy asymptote a_i in (0, 1] and speed k_i > 0, fit by least
squares on the logit scale over the trophy's segment rates. The fit is
HIERARCHICAL (partial pooling): log k_i and logit a_i are shrunk toward the
population means estimated from all 55 trophies, with the shrinkage
strength chosen by the backtest (§4). The shared population curve is the
mean trajectory; each trophy keeps its own parameters. One curve for all
trophies is explicitly rejected: the live snapshot shows per-trophy speeds
differ by ~10x (chapter 1 at 7.99% vs chapter 2 at 0.90% vs most trophies at
0.01–0.02%), and a single curve would be badly wrong for all of them.

- NO name-based priors. The fit uses only (segment time, rate) pairs.
  Trophy names, descriptions, secret flags and tiers are NOT inputs to the
  fit. In particular, nothing is hardcoded like "early-chapter trophies
  saturate fast" from a name; if chapter 1 saturates fast, the fit must
  discover it from its own rate history. Shrinkage is toward the single
  population mean, not toward tier/name groups, for the same reason.
- Residual correction. Each trophy's residual at the last segment (logit
  scale, observed minus fitted) is carried forward with decay rho^h, where
  h is the horizon in days and rho in [0, 1] is fit from segment-to-segment
  residual persistence. If residuals show no persistence, rho -> 0 and the
  correction vanishes. This handles trophies that sit systematically above
  or below their curve.
- The 100% trophy (id 2). Its fitted curve has a_i ~= 1: new players earn
  it almost immediately, so E_2(t) ~= P(t) in every scenario and its rate
  stays ~= 100%, with a narrow lower tail from scenarios where a refresh
  lands just before the target on a wave of brand-new players. It is NOT
  special-cased or pinned; the fit handles it. (logit(100) is handled by
  the same clamp to 99.99 the starter uses.)
- Earners are integers; the scenario math uses E_i = P * r_i and keeps the
  rounding interval as noise. The output quantiles are percents; fractions
  are allowed.

### Stage 3 — Joint predictive scenarios (the forecast itself)

Seeded Monte Carlo, fixed seed (e.g. 20261121), N scenarios (start N = 8000,
tune so the whole run stays < ~5 s on 1 CPU — see §5). One scenario draws:

  (i)   a refresh path from the fitted renewal process -> t* (last refresh
        at or before the target; shared by all trophies and by P);
  (ii)  adoption parameters (bootstrap over segment residuals) -> P(t*);
  (iii) a SHARED progression-speed shock g (multiplies all k_i), drawn from
        the fitted distribution of segment-to-segment speed variation;
  (iv)  per-trophy parameters (a_i, k_i) from their fitted approximate
        posteriors (normal on transformed scales), independent CONDITIONAL
        on g;
  (v)   per-trophy residual draws with the fitted decay.

Then E_{i} = P(t*) * r_i(t*) and the scenario rate is
R_i = 100 * E_i / P(t*). The P cancels algebraically inside a scenario,
but the JOINTNESS is real and is the whole point: t* is shared (a late
refresh raises all rates together, an early one lowers them), g is shared
(players progress faster/slower than expected across the board), and the
rate depends on the player-growth path (fast growth = young player base =
dilution), not on the clock alone. Simulating E and P marginally and
dividing their quantiles would erase exactly these couplings.

Quantiles: for each trophy, the empirical tau-quantiles of {R_i} over the
scenarios, at tau in {0.05, 0.25, 0.5, 0.75, 0.95}. Empirical quantiles
are monotone by construction; sort defensively before writing. This is the
joint forecast — never Q(E)/Q(P).

### Stage 4 — Rolling-origin backtest + empirical calibration

The program runs its own backtest on the case's history BEFORE forecasting
(inside the 8 s budget; keep it cheap — few origins, reuse the scenario
machinery with smaller N). Origins: every segment start that leaves >= 1 day
of history after it and has >= 2 segments before it for fitting. Horizons
tested: 1, 2, 3, 4, ~4.5 days, wherever the post-origin span allows.
Scored with the SAME pinball-logit metric, against the actual read at the
pseudo-target (first read at/after origin + horizon).

The honest problem: the practice history is frozen, so a backtest built
from practice alone has exactly one event and zero within-history variation.
It cannot select a model or calibrate a spread. The backtest therefore has
three legs, and the design says plainly which leg each claim rests on:

  Leg A (real, in-program, at runtime) — rolling origins over f00's own
  history (~2.7 days, several refreshes expected). Horizons 1–2.7 days
  scored directly on house reads. This is the PRIMARY leg and the only one
  that uses real house reads. Model selection among a SMALL set of variants
  (adoption form log-time vs linear; refresh gaps lognormal vs empirical;
  residual decay on/off) is by mean pinball-logit over Leg A origins.

  Leg B (real, one point, design-time) — origin = practice last read
  (2026-11-19T02:30:00Z, P = 638), pseudo-target = live snapshot
  (2026-11-20T09:07:00Z, ~30.6 h later). The 55 live rates ARE the answer
  for that pseudo-target, so it scores with pinball-logit on rates (players
  not needed for scoring). Caveats, stated in the snapshot file: the
  snapshot is a single crawl, not a house read; its refresh-as-of time is
  fuzzy (the page's last refresh before the crawl is unknown); players
  unknown (only the >80k bound). It is the only real long-horizon point
  available before the deadline and the only direct test of the dilution
  regime. It is reported as ONE point with a fuzzy target, used as a
  tiebreak/check on Leg A — never as the sole selector.

  Leg C (synthetic) — a refresh simulator calibrated on the observed
  refreshes (gap distribution and adoption curve fit from the history at
  runtime; per-trophy curves from the fitted hierarchical model). Generate
  pseudo-histories, run the forecaster on them, score 4–4.5-day horizons.
  This is the ONLY way to test the 4.5-day horizon before the deadline.
  Its assumptions are listed here, not hidden: refresh gaps follow the
  fitted distribution; adoption keeps its fitted decaying growth; no game
  patch, DLC, or trophy-list change alters trophy behaviour; no weekend /
  weekday play-pattern change. Results are reported as
  simulator-conditional, never as real-world evidence.

Calibration. From backtest residuals (per origin, per trophy: whether y
fell below q_tau for each tau, and the logit residuals), compute the
empirical coverage of the 90% band (q05 <= y <= q95) with a BLOCK BOOTSTRAP
over origins — resample whole origins, never individual trophy-items,
because trophies share the refresh shock and origins share time. Choose a
single global spread multiplier lambda (applied to the scenario spread on
the logit scale, widening/narrowing all quantiles around the median) that
brings block-bootstrap coverage to ~0.90 on the backtest; if coverage
differs systematically by horizon, use lambda(h) linear in log-horizon,
fit on the backtest. Lambda is FIT at runtime from the backtest, not a
hardcoded constant, and is bounded (e.g. [0.5, 3]) so a degenerate backtest
cannot produce an absurd band. If the backtest has < 2 usable origins,
lambda = 1 (uncalibrated scenario spread) and the program records that.

What the backtest can and cannot prove. It CAN prove the forecaster beats
persistence on refresh events and that its 90% band covers ~90% on the
horizons it was tested on. It CANNOT prove the 4.5-day extrapolation: no
4.5-day span exists in the data before the deadline, and Leg C is a
simulator, not the world.

### Stage 5 — Output, determinism, budget

- Deterministic: fixed seed, `random.Random(seed)`, fixed N, sorted
  iteration order, quantiles rounded to 4 decimals half-up (as the starter
  does), JSON written with fixed separators. Two runs byte-identical.
- No network, no clock: all times come from the case file (at, target,
  slot). No `time.time()`, no `datetime.now()`. No reading of data/,
  ANALYSIS.md, or the live snapshot at runtime — design evidence only.
- No hardcoded practice values: player levels, refresh times/gaps, trophy
  rates and asymptotes are all fit from the case's history at runtime. The
  only constants are structural (levels, z-values, seed, N, clamp bounds,
  lambda bounds, functional forms) plus regularization strengths chosen by
  the backtest procedure itself.
- Runtime: fit + backtest + simulation + calibration < ~5 s locally on
  1 CPU (budget 8 s), leaving margin for the grader's machine. If the
  backtest is too slow, reduce origins first, then N — never fit quality.
- Validity guards: 55 rows, ids match the case, q non-decreasing in
  [0, 100], <= 65,536 bytes. If the history has < 2 segments or any fit
  fails, fall back to the starter's form (persistence + random-walk spread,
  sigma 0.5/sqrt(day)) so the case is always valid — a valid honest
  forecast beats a crash (a crash scores as persistence anyway and takes
  no prize). Every fallback path must pass tools/harness.py.

## 3. What the live snapshot changed in the design

- P exploded (>80k bound vs 638 at launch, 3,868 at first refresh):
  adoption + dilution is the dominant effect. The model is built around E
  and P separately, not around rates alone.
- Rates compressed to 0.01–0.02% for most trophies: the logit scale is the
  whole game (0.01% vs 0.02% is ~0.69 logit). Quantile calibration must be
  honest on the logit scale; a spread that looks small in percentage
  points is enormous in logits.
- Platinum 0.00% (from 0.63%): no trophy is safe from dilution. No floor
  assumption anywhere (no "at least the launch-night rate").
- Chapter gradient persists (Ch.1 7.99% vs Ch.2 0.90% vs most 0.01–0.02%):
  per-trophy speeds differ by ~10x, so the hierarchical fit must recover
  this from data, and a single shared curve is rejected.
- EXP (the parenthetical number on the page) is NOT used: it is Exophase's
  rarity score, it is not in the case files, and the model must not depend
  on anything the grader does not give it.

## 4. Honest limitations — what still cannot be answered

1. The refresh schedule 21–26 Nov is unobserved (Exophase's internal
   schedule). It is modelled as a renewal process fit on 19–21 Nov gaps;
   if Exophase changes cadence after launch week, the last-refresh-time
   distribution is wrong, and that is the single largest error source.
2. Adoption extrapolation: the 4.5-day horizon runs ~1.8 days past the
   history's span. Weekend vs weekday play patterns (target Thu 26 Nov;
   horizon covers Sat–Thu) are not identifiable from the history.
3. players_tracked at the target is unknown (the live snapshot could not
   read it; only the >80k bound). The model forecasts it; the bound is a
   sanity check, not an input.
4. The 4–4.5-day backtest is synthetic-only (Leg C). Nothing before the
   deadline can verify it against the world.
5. Trophy list is fixed at f00's 55. If Exophase adds/removes trophies
   before the target, the answer read must still list all 55 (per RULES,
   else there is no answer and no prize is paid).
6. Cross-trophy correlation beyond the shared shocks (t*, g) is not
   modelled: per-trophy residuals are independent conditional on the
   shared shocks. The block bootstrap accounts for that correlation in
   calibration uncertainty, not in the point forecast.
7. The model does not know WHY a refresh happens (Exophase's internal
   triggers) and does not model player churn in the tracked set (players
   whose profiles stop syncing). Both are second-order against the
   refresh-timing and adoption uncertainties above.

## 5. Test plan for the model stage ("done" looks like this)

- tools/harness.py PASS on p00/p01 (format + determinism), and on a
  synthetic f00-like history (many reads, several refreshes) built by the
  Leg C simulator.
- Backtest report (dev-only script, not shipped in main.py): Leg A mean
  pinball-logit per horizon + per variant, Leg B single-point score on the
  live snapshot, Leg C 4.5-day score; block-bootstrap 90% coverage before
  and after lambda calibration (target ~0.90).
- Timing: main.py < 5 s on p00/p01 and on the synthetic long history,
  1 CPU.
- No network/clock calls in main.py (grep for socket/urllib/time/datetime
  use beyond parsing case timestamps).
- Frozen-history test: a one-segment history produces a valid output via
  the fallback path, no crash.
- No hardcoded practice values in main.py (grep for 638, 3868, and the
  practice rates; the only 638 allowed is in a comment explaining why it
  is NOT used).

## 6. Open questions for Ting

1. Is Leg C (synthetic simulator) acceptable as the ONLY 4.5-day evidence,
   or should the 4.5-day spread be set more conservatively than the
   simulator suggests?
2. Is population-mean shrinkage (no tier/name groups) the right call, or
   should tier be allowed as a shrinkage group in a backtest variant?
3. Is the Leg B point (live snapshot, fuzzy target time) acceptable as a
   scored backtest point, or should it stay a qualitative check only?
