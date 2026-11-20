"""Local harness for gta6-trophy-rarity-forecast-01 (stdlib only).

Runs main.py on the kit's practice inputs and validates the outputs'
format only. It does NOT score anything: the practice answers are not
in the kit (the server scores them).

Checks per case file <id>.in -> <id>.out:
  - output file exists, valid JSON, size <= 65536 bytes
  - top level is {"trophies": [...]}, no other key
  - one row per trophy of the case, each id once, ids == case trophy ids
  - each row is exactly {"id", "q"}; q is 5 finite numbers in [0, 100],
    non-decreasing
  - two runs are byte-identical (determinism)

Usage:
  python3 tools/harness.py [--kit-input DIR] [--main FILE]
Defaults: kit input is the kit's input/ next to this repo's sibling
kit dir; main is the repo root's main.py.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIT = Path(os.environ.get(
    "TROPHY_KIT",
    str(Path.home() / "workspace/makefun/gta6-trophy-rarity-forecast-01-kit"
        / "gta6-trophy-rarity-forecast-01-kit")))
DEFAULT_INPUT = KIT / "input"
MAX_BYTES = 65536


def run_once(main_py: Path, input_dir: Path, output_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = str(input_dir)
    env["MAKEFUN_OUTPUT_DIR"] = str(output_dir)
    return subprocess.run(
        [sys.executable, str(main_py)], env=env,
        capture_output=True, text=True, timeout=120)


def validate_case(case: dict, out_path: Path) -> list[str]:
    errors = []
    raw = out_path.read_bytes()
    if len(raw) > MAX_BYTES:
        errors.append(f"{out_path.name}: {len(raw)} bytes > {MAX_BYTES}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return [f"{out_path.name}: not valid JSON: {e}"]
    if not isinstance(data, dict) or set(data) != {"trophies"}:
        errors.append(f"{out_path.name}: top level must be exactly {{'trophies'}}")
        return errors
    rows = data["trophies"]
    want_ids = [t["id"] for t in case["trophies"]]
    if not isinstance(rows, list) or len(rows) != len(want_ids):
        errors.append(f"{out_path.name}: {len(rows) if isinstance(rows, list) else '?'} rows, want {len(want_ids)}")
        return errors
    seen = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "q"}:
            errors.append(f"{out_path.name}: row keys must be exactly {{'id','q'}}: {row!r:.120}")
            continue
        seen.append(row["id"])
        q = row["q"]
        if not isinstance(q, list) or len(q) != 5:
            errors.append(f"{out_path.name}: id {row['id']}: q must have 5 values")
            continue
        for v in q:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                errors.append(f"{out_path.name}: id {row['id']}: q value not finite: {v!r}")
            elif not 0 <= v <= 100:
                errors.append(f"{out_path.name}: id {row['id']}: q value out of [0,100]: {v}")
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in q):
            if any(q[i] > q[i + 1] for i in range(4)):
                errors.append(f"{out_path.name}: id {row['id']}: q not non-decreasing: {q}")
    if sorted(seen) != sorted(want_ids):
        errors.append(f"{out_path.name}: ids do not match case trophies")
    if len(set(seen)) != len(seen):
        errors.append(f"{out_path.name}: duplicate ids")
    return errors


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit-input", default=str(DEFAULT_INPUT))
    ap.add_argument("--main", default=str(REPO / "main.py"))
    args = ap.parse_args()
    input_dir, main_py = Path(args.kit_input), Path(args.main)
    cases = sorted(input_dir.glob("*.in"))
    if not cases:
        print(f"FAIL: no *.in in {input_dir}")
        return 1
    errors = []
    with tempfile.TemporaryDirectory(prefix="trophy-harness-") as td:
        outs = []
        for i, tag in enumerate(("run1", "run2")):
            out_dir = Path(td) / tag
            r = run_once(main_py, input_dir, out_dir)
            if r.returncode != 0:
                print(f"FAIL: {tag} exited {r.returncode}\n{r.stdout}{r.stderr}")
                return 1
            outs.append(out_dir)
        for case_path in cases:
            case = json.loads(case_path.read_text(encoding="utf-8"))
            for out_dir in outs:
                errors += validate_case(case, out_dir / f"{case_path.stem}.out")
            b1 = (outs[0] / f"{case_path.stem}.out").read_bytes()
            b2 = (outs[1] / f"{case_path.stem}.out").read_bytes()
            if b1 != b2:
                errors.append(f"{case_path.stem}.out: runs differ (not deterministic)")
    if errors:
        print(f"FAIL ({len(errors)}):")
        for e in errors:
            print(" -", e)
        return 1
    print(f"PASS: {len(cases)} case(s) {[p.stem for p in cases]} — format OK, "
          f"55 rows each, q non-decreasing in [0,100], <=65536 B, two runs byte-identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
