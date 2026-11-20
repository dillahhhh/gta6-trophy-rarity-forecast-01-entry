"""Score a forecast against the §4 refreshed page rates (practice truth).

Pinball loss on the logit scale, §1 formula (with the x2 factor):
  loss = mean over trophies and levels of 2*max(tau*d, (tau-1)*d),
  d = logit(y) - logit(q), rates clamped to 0.01..99.99.

Usage: python3 tools/score_practice.py [--main FILE]
Runs main.py on the kit's p00/p01 inputs, scores each output against the
§4 page rates, and reports loss, median |q50 - y| in pp, and 90% coverage.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIT = Path.home() / "workspace/makefun/gta6-trophy-rarity-forecast-01-kit" / "gta6-trophy-rarity-forecast-01-kit"
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]

PAGE_RATES = [
    0.10, 100.0, 0.49, 0.23, 0.23, 0.23, 0.23, 0.23, 0.23, 0.23,
    0.23, 0.23, 0.23, 0.10, 0.10, 0.13, 0.10, 0.10, 0.13, 1.16,
    0.39, 0.13, 0.13, 0.16, 0.10, 0.10, 0.13, 0.10, 0.13, 0.10,
    0.10, 0.10, 0.18, 0.16, 0.39, 0.13, 0.23, 0.31, 0.18, 0.13,
    0.16, 0.16, 0.13, 0.21, 0.13, 0.16, 0.16, 0.18, 0.16, 0.18,
    0.18, 0.13, 0.18, 0.16, 0.16,
]


def logit(pct):
    p = min(max(float(pct), 0.01), 99.99) / 100.0
    return math.log(p / (1.0 - p))


def score(out_path):
    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    rows = {r["id"]: r["q"] for r in data["trophies"]}
    total, n, misses, inside = 0.0, 0, [], 0
    for i, y in enumerate(PAGE_RATES):
        q = rows[i + 1]
        misses.append(abs(q[2] - y))
        if q[0] <= y <= q[4]:
            inside += 1
        for tau, qv in zip(LEVELS, q):
            d = logit(y) - logit(qv)
            total += 2.0 * max(tau * d, (tau - 1.0) * d)
            n += 1
    misses.sort()
    return total / n, misses[len(misses) // 2], inside / len(PAGE_RATES)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default=str(REPO / "main.py"))
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="trophy-score-") as td:
        env = dict(os.environ)
        env["MAKEFUN_INPUT_DIR"] = str(KIT / "input")
        env["MAKEFUN_OUTPUT_DIR"] = td
        import time
        t0 = time.time()
        r = subprocess.run([sys.executable, args.main], env=env,
                           capture_output=True, text=True, timeout=120)
        dt = time.time() - t0
        if r.returncode != 0:
            print(f"FAIL: main.py exited {r.returncode}\n{r.stdout}{r.stderr}")
            return 1
        print(f"run time for 2 cases: {dt:.2f}s")
        for name in ("p00", "p01"):
            loss, med_miss, cov = score(Path(td) / f"{name}.out")
            print(f"  {name}: loss={loss:.6f} median|q50-y|={med_miss:.4f}pp "
                  f"coverage90={cov:.3f}")
    print("target: loss ~= 0.00059, median miss 0.00pp, coverage 1.000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
