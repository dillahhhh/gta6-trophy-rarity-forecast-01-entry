# Analysis — gta6-trophy-rarity-forecast-01 (practice cases p00, p01)

Facts and numbers only — no model claims, no practice answers (the answers are not in the kit; the server scores them).
Sources: `input/p00.in`, `input/p01.in` from the kit. Computed with Python stdlib only, 20 Nov 2026.

## Case structure

| case | at (checkpoint) | horizon | target | trophies | history reads | slots |
|---|---|---|---|---|---|---|
| p00 | 2026-11-19T03:00:00Z | 90 min | 2026-11-19T04:30:00Z | 55 | 7 | 2026-11-18T23:50:00Z → 2026-11-19T02:30:00Z |
| p01 | 2026-11-19T03:30:00Z | 90 min | 2026-11-19T05:00:00Z | 55 | 8 | 2026-11-18T23:50:00Z → 2026-11-19T03:00:00Z |

Both cases list the same 55 trophies in the same order. p01's history is p00's history plus one more read (slot 2026-11-19T03:00:00Z).
Levels scored in both cases: [0.05, 0.25, 0.5, 0.75, 0.95].

## History is frozen — no within-case time variation

- `players_tracked` = 638 in every read of both cases (7 reads in p00, 8 in p01).
- `trophies_earned` = 943 in every read of both cases.
- Every trophy's rate is identical in every read: max |change| first→last read = 0.00 pp, 0 of 55 trophies moved.
- No null rates anywhere (0 nulls in both cases).
- So the history carries **no time variation at all** in the practice data; all variation is cross-sectional (across trophies).
  This matches RULES.txt: the page was unchanged from 2026-11-18T23:48:00Z through the 04:00:00Z read, then refreshed at 04:30:00Z.

## Players and earners

- Earners per trophy = rate × players / 100, with players = 638. Rates are printed to 2 decimals, so earners are integers on a 1/638 ≈ 0.157 pp grid.
- Sum of last-read rates = 147.78 → implied total earners 942.84, vs `trophies_earned` = 943 (consistent within 2-decimal rounding).
- Smallest non-zero rate 0.63% = 4.02 ≈ 4 earners (4/638 = 0.627% → 0.63%).
- Largest non-100% rate 2.04% = 13.02 ≈ 13 earners (13/638 = 2.038% → 2.04%).

## Trophy roster

| tier | n | points each | earners in last read (sum) | median rate |
|---|---|---|---|---|
| platinum | 1 | 300 | 4.0 | 0.63 |
| gold | 2 | 90 | 11.0 | 1.1 |
| silver | 6 | 30 | 25.1 | 0.63 |
| bronze | 46 | 15 | 902.7 | 0.78 |

- Secret trophies: 28 of 55 (e.g. id 2 'Just Like Paradise', id 3 'Paradise Lost').
- id 1 is the platinum, 'Vice Got a Price!' (300 pts).

## Rate distribution (last read, both cases identical)

| bucket | n trophies |
|---|---|
| 100% | 1 |
| 50–100% (excl. 100) | 0 |
| 25–50% | 0 |
| 10–25% | 0 |
| 5–10% | 0 |
| 1–5% | 14 |
| 0–1% (excl. 0) | 40 |
| 0% | 0 |

- Min 0.63%, p25 0.78%, median 0.78%, p75 1.10%, max 100%.
- 11 trophies sit at the floor 0.63% (4 earners); 22 at 0.78% (5 earners).
- The distribution is extremely compressed: 54 of 55 trophies are between 0.63% and 2.04%.

## Extremes (last read)

- 100%: id 2 'Just Like Paradise' — 638/638 earners. It is the only trophy above 5%.
- 0%: none — every trophy has at least 4 earners.
- Highest below 100%: id 20 'Baited My Tackle' 2.04% (13 earners), id 38 'Challenging Tank' 1.57% (10 earners), id 3 'Paradise Lost' and id 35 'Whip It Good' 1.25% (8 earners).
- Lowest: 0.63% (4 earners), incl. the platinum id 1 'Vice Got a Price!' and ids 14, 15, 17, 18.

## Targets vs the known refresh

- p00's target (2026-11-19T04:30:00Z) coincides with the refresh described in RULES.txt: the 04:30:00Z read showed 3,868 players tracked and every rate but the 100% one lower than before.
- p01's target (2026-11-19T05:00:00Z) is 30 minutes after that refresh.
- The practice answers themselves are not in the kit and are not claimed here.

## Final case (from RULES.txt, for context)

- One case, f00, cut at the deadline 2026-11-21T11:30:00Z (21 Nov 2026 18:30 WIB): every valid read before it, oldest first.
- Target 2026-11-26T00:00:00Z, 6,510 minutes after the deadline. House reads every 30 min early, every 10 min on the final recorder.
- Scored by mean pinball loss on the logit scale over every trophy and quantile.

