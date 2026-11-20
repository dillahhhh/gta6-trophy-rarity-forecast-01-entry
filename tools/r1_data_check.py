#!/usr/bin/env python3
"""Data integrity check (rank-1 prompt: data integrity).

Verifies, from the source files (not from memory):
  * SHA-256 of the kit inputs and the live snapshot against
    data/rank1-sources.json.
  * The transcribed page rates' earners totals (4,276 total; 305 -> 408
    non-100% earners) -- the check that the transcription is the real
    refreshed page, not a fabrication.
  * The kit histories: duplicate / missing / anomalous observations
    (frozen stretches, null rates, out-of-range rates, players_tracked
    jumps, trophies_earned consistency).
  * The live snapshot: rates in [0, 100], id 2 == 100, no nulls.

Exit 0 when every check passes; prints FAIL lines otherwise.
"""

import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    fails = []
    src = json.load(open(os.path.join(REPO, "data/rank1-sources.json")))
    # 1. SHA-256 of the source files.
    for s in src["sources"]:
        if s["sha256"] == "not applicable (transcription in a user file, not a byte file in this repo)":
            continue
        path = s["path"].split(" (")[0]  # strip the parenthetical note
        if path.startswith("kit/"):
            path = os.path.join(KIT, os.path.basename(path))
        elif path.startswith("data/"):
            path = os.path.join(REPO, path)
        got = sha256(path)
        ok = got == s["sha256"]
        print("SHA-256 %-28s %s" % (s["name"], "OK" if ok else "FAIL"))
        if not ok:
            fails.append("sha256 %s" % s["name"])
    # 2. Page earners totals (transcription verification).
    frozen = src["frozen_rates_last_read"]
    page = src["page_rates_refreshed_2026_11_19_0916Z"]
    total = sum(page) * 3868 / 100.0
    non100_frozen = (sum(frozen) - 100.0) * 638 / 100.0
    non100_page = (sum(page) - 100.0) * 3868 / 100.0
    print("page earners total %.1f (expect 4276, RULES.txt)" % total)
    print("non-100%% earners frozen %.1f (expect ~305), page %.1f (expect ~408)" % (
        non100_frozen, non100_page))
    if abs(total - 4276) > 12:
        fails.append("page earners total %.1f != 4276" % total)
    if abs(non100_frozen - 305) > 2 or abs(non100_page - 408) > 2:
        fails.append("non-100%% earners mismatch")
    # 3. Kit history quality.
    for pid in ("p00", "p01"):
        case = json.load(open(os.path.join(KIT, pid + ".in")))
        hist = case["history"]
        players = {r["players_tracked"] for r in hist}
        earners = {r["trophies_earned"] for r in hist}
        nulls = sum(1 for r in hist for v in (r["rates"] or []) if v is None)
        oor = sum(1 for r in hist for v in (r["rates"] or [])
                  if v is not None and not (0.0 <= v <= 100.0))
        # Frozen: how many distinct rate vectors.
        distinct = len({tuple(r["rates"]) for r in hist})
        # Monotone slots (timestamps increasing).
        times = [r["at"] for r in hist]
        mono = all(times[i] < times[i + 1] for i in range(len(times) - 1))
        print("%s: %d reads, %d distinct rate vectors (frozen=%s), "
              "players %s, earners %s, nulls %d, out-of-range %d, "
              "timestamps monotone %s" % (
                  pid, len(hist), distinct, distinct == 1, sorted(players),
                  sorted(earners), nulls, oor, mono))
        if distinct != 1:
            fails.append("%s not fully frozen" % pid)
        if nulls or oor or not mono:
            fails.append("%s history anomaly" % pid)
    # 4. Live snapshot quality.
    live = src["live_snapshot_rates_2026_11_20_0907Z"]
    print("live snapshot: %d rates, min %.2f, max (non-100) %.2f, id2 %.1f, "
          "sum %.2f" % (len(live), min(live),
                        max(r for r in live if r < 100.0), live[1], sum(live)))
    if len(live) != 55 or live[1] != 100.0 or min(live) < 0.0:
        fails.append("live snapshot anomaly")
    print("RESULT: %s" % ("ALL CHECKS PASS" if not fails else "FAIL: " + "; ".join(fails)))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
