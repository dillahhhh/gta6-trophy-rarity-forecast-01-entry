"""Final validation for the LOCKED config (dev tool, NOT model code).

Ting finalization checklist items 3-5, run against the locked
model_forecast (M0 + lambda 1.25, no stress floor):
  3. Stress scenarios: fast refresh (gaps x0.5), slow refresh (gaps x2.0),
     NO refresh until target, player growth x1.5 / x0.5 — output must stay
     valid (55 rows, q monotone, in [0, 100]).
  4. Runtime, repeated, on the longest 20-segment case: target max < 6.5 s
     (limit 8 s). In-process model_forecast x5 + standalone CLI x3.
  5. Final validation: 55 trophies + 5 quantiles valid, monotone, in range,
     on p00/p01 (fallback) AND the long case; two runs byte-identical;
     fallback starter valid for frozen history / failed fit.

Usage: python3 tools/final_validation.py
"""
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(TOOLS))
import main as M
import backtest as BT


def valid(fc, label):
    assert len(fc["trophies"]) == 55, f"{label}: {len(fc['trophies'])} rows"
    for row in fc["trophies"]:
        qs = row["q"]
        assert len(qs) == 5, f"{label}: {row}"
        assert all(0.0 <= q <= 100.0 for q in qs), f"{label}: out of range {row}"
        assert all(qs[k] <= qs[k + 1] for k in range(4)), f"{label}: inverted {row}"
    return True


def long_case(seed=7):
    case, segments, truth = BT._model_truth_case(seed=seed)
    t_target = M.parse_time(case["at"]) + timedelta(days=4.5)
    case["target"] = t_target.strftime("%Y-%m-%dT%H:%M:%SZ")
    case["horizon_minutes"] = int(4.5 * 1440)
    return case, segments, truth, t_target


def stress_validity():
    print("== Stress scenarios (locked config, long 20-segment case) ==")
    case, segments, _, _ = long_case()
    ctx = M.build_context(case, segments)
    runs = [("fast refresh (gaps x0.5)", dict(gap_scale=0.5)),
            ("slow refresh (gaps x2.0)", dict(gap_scale=2.0)),
            ("NO refresh until target", dict(force_no_refresh=True)),
            ("player growth x1.5", dict(adoption_scale=1.5)),
            ("player growth x0.5", dict(adoption_scale=0.5))]
    for label, kw in runs:
        cols = M.run_scenarios(ctx, "M0", M.N_FINAL, M.SEED, **kw)
        fc = M._scale_quantiles_logit(M.quantiles_from_scenarios(ctx, cols), 1.25)
        valid(fc, label)
        print(f"  {label}: VALID (55 rows, q monotone, in [0,100])")


def determinism_and_runtime():
    print("== Determinism + runtime (locked model_forecast, long case) ==")
    case, segments, _, _ = long_case()
    fc1 = M.model_forecast(case, segments, "det1")
    fc2 = M.model_forecast(case, segments, "det2")
    assert json.dumps(fc1, sort_keys=True) == json.dumps(fc2, sort_keys=True), \
        "non-deterministic forecast"
    valid(fc1, "long-case")
    print("  two model_forecast runs byte-identical, valid")
    times = []
    for i in range(5):
        t0 = time.time()
        M.model_forecast(case, segments, f"runtime{i}")
        times.append(time.time() - t0)
    print(f"  in-process model_forecast x5: "
          + ", ".join(f"{t:.2f}s" for t in times)
          + f" -> max {max(times):.2f}s (target <6.5s, limit 8s)")
    assert max(times) < 6.5, f"runtime too slow: max {max(times):.2f}s"
    # Standalone CLI on the same long case as a .in file.
    with tempfile.TemporaryDirectory(prefix="trophy-cli-") as td:
        inp = Path(td) / "in"
        out = Path(td) / "out"
        inp.mkdir()
        (inp / "long.in").write_text(json.dumps(case), encoding="utf-8")
        cli_times = []
        for i in range(3):
            env = dict(__import__("os").environ)
            env["MAKEFUN_INPUT_DIR"] = str(inp)
            env["MAKEFUN_OUTPUT_DIR"] = str(out)
            t0 = time.time()
            r = subprocess.run([sys.executable, "-P", "-s", str(REPO / "main.py")],
                               env=env, capture_output=True, text=True, timeout=120)
            cli_times.append(time.time() - t0)
            assert r.returncode == 0, f"CLI run {i} failed: {r.stdout}{r.stderr}"
        raw = (out / "long.out").read_bytes()
        assert raw == (out / "long.out").read_bytes()
        print(f"  standalone CLI x3: "
              + ", ".join(f"{t:.2f}s" for t in cli_times)
              + f" -> max {max(cli_times):.2f}s")
        assert max(cli_times) < 6.5, f"CLI too slow: max {max(cli_times):.2f}s"
    return max(times), max(cli_times)


def fallback_validity():
    print("== Fallbacks (locked code path) ==")
    p00 = BT.load_case(BT.KIT / "input" / "p00.in")
    trophies = p00["trophies"]
    base = p00["history"][-1]["rates"]
    t0 = datetime(2026, 11, 18, 23, 50, tzinfo=timezone.utc)
    one = BT.make_synthetic_case([(t0, 638, base)], t0 + timedelta(hours=2),
                                 90, t0 + timedelta(hours=3, minutes=30), trophies)
    fc = M.forecast_case(one, "one-segment")
    valid(fc, "one-segment")
    print("  one-segment case -> starter fallback, valid")
    bad = BT.make_synthetic_case([(t0, 638, base), (t0 + timedelta(hours=5), 0, base)],
                                 t0 + timedelta(hours=6), 90,
                                 t0 + timedelta(hours=7, minutes=30), trophies)
    fc = M.forecast_case(bad, "fit-failure")
    valid(fc, "fit-failure")
    print("  fit-failure case (P=0) -> starter fallback, valid")
    # Practice cases through forecast_case (frozen -> starter).
    for name in ("p00", "p01"):
        c = BT.load_case(BT.KIT / "input" / f"{name}.in")
        fc = M.forecast_case(c, name)
        valid(fc, name)
        print(f"  practice {name} -> valid (55 rows)")


def main():
    stress_validity()
    determinism_and_runtime()
    fallback_validity()
    print("FINAL VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
