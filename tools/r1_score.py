#!/usr/bin/env python3
"""Pinball scorer for GTA6 trophy forecasts, per RULES.txt.

Score = mean pinball loss on the LOGIT scale, rates clamped to
[0.01, 99.99], times a factor of 2, averaged over every
(trophy, level) pair. Lower is better.
  pinball_logit(y, q, tau) = (ly - lq) * (tau - 1[ly < lq])
where ly = logit(y), lq = logit(q) (natural log of odds).

Also reports: typical miss = mean |y - q50| in percentage points;
coverage90 = share of trophies with q05 <= y <= q95 (information only);
per-trophy pinball, median absolute error, and per-trophy coverage.

Validation of this scorer: it reproduces the two known figures from
RULES.txt / prior work -- the house starter baseline scores ~1.51 on the
practice cases (vs the transcribed page rates as truth), and a frozen
path with the page rates as medians scores ~0.000591. See
tools/r1_validate.py, which checks both.
"""

import json
import math
import sys

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]


def logit_rate(r):
    p = min(99.99, max(0.01, r)) / 100.0
    return math.log(p / (1.0 - p))


def pinball_logit(y, q, tau):
    ly = logit_rate(y)
    lq = logit_rate(q)
    d = ly - lq
    return d * (tau - (1.0 if ly < lq else 0.0))


def score(out, truth_rates):
    """Score one output dict against a list of 55 truth rates (percent).

    Returns a dict: loss (mean pinball-logit x2), typical_miss_pp,
    coverage90, median_ae_pp, per_trophy (list of dicts with id,
    loss, ae_pp, covered, q50, truth)."""
    rows = out["trophies"]
    assert len(rows) == len(truth_rates) == 55
    total = 0.0
    count = 0
    miss_sum = 0.0
    covered = 0
    per_trophy = []
    aes = []
    for row, y in zip(rows, truth_rates):
        qs = row["q"]
        assert len(qs) == 5
        t_loss = 0.0
        for tau, q in zip(LEVELS, qs):
            t_loss += pinball_logit(y, q, tau)
            total += pinball_logit(y, q, tau)
            count += 1
        t_loss /= 5.0
        ae = abs(y - qs[2])
        aes.append(ae)
        miss_sum += ae
        cov = qs[0] <= y <= qs[4]
        covered += 1 if cov else 0
        per_trophy.append({
            "id": row["id"],
            "loss": t_loss,
            "ae_pp": ae,
            "covered": cov,
            "q50": qs[2],
            "truth": y,
            "q05": qs[0],
            "q95": qs[4],
        })
    aes.sort()
    median_ae = aes[len(aes) // 2] if len(aes) % 2 == 1 else (
        aes[len(aes) // 2 - 1] + aes[len(aes) // 2]) / 2.0
    return {
        "loss": 2.0 * total / count,
        "typical_miss_pp": miss_sum / 55.0,
        "coverage90": covered / 55.0,
        "median_ae_pp": median_ae,
        "per_trophy": per_trophy,
    }


def main():
    if len(sys.argv) != 3:
        print("usage: r1_score.py <out.json> <truth.json (list of 55 rates)>")
        sys.exit(2)
    out = json.load(open(sys.argv[1]))
    truth = json.load(open(sys.argv[2]))
    res = score(out, truth)
    print("loss (mean pinball-logit x2): %.6f" % res["loss"])
    print("typical miss (mean |y - q50|): %.4f pp" % res["typical_miss_pp"])
    print("median AE: %.4f pp" % res["median_ae_pp"])
    print("coverage90: %.3f" % res["coverage90"])
    # Worst trophies by loss.
    worst = sorted(res["per_trophy"], key=lambda t: -t["loss"])[:8]
    print("worst trophies (id, loss, ae_pp, q50, truth):")
    for t in worst:
        print("  id %d loss %.4f ae %.3f q50 %.2f truth %.2f covered=%s" % (
            t["id"], t["loss"], t["ae_pp"], t["q50"], t["truth"], t["covered"]))


if __name__ == "__main__":
    main()
