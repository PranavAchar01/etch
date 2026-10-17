"""Offline validation of every device in engine/parts/devices.json: compose its blocks (agent/blocks.py) and run the
assembly loop's pre-build checks (blocks.gate: library, lonely nets, fix-up chain unchanged, lint, firmware build and
test, benchmarks, spec loads). No LLM, no KiCad. Writes results.json and coverage.md next to this file.

    PYTHONPATH=. python qa/devices-1017/validate.py [workers]
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent


def one(item):
    did, name, blocks_, wireless = item
    from agent import blocks

    t = time.time()
    try:
        d = blocks.compose(blocks_, name, name, did, wireless=wireless or None)
        err = blocks.gate(d, name)
        parts = len(d["parts"])
    except Exception as e:  # a block that raises is a failure to report, not a crash of the sweep
        err, parts = f"{type(e).__name__}: {e}", 0
    return did, err, parts, round(time.time() - t, 2)


def main() -> None:
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    db = json.loads(Path("engine/parts/devices.json").read_text())
    devs = db["devices"]
    # identical compositions give identical results; ask text only matters for the radio swap, which follows the flag
    key = lambda d: (json.dumps(d["blocks"], sort_keys=True), bool(d["wireless"]))  # noqa: E731
    uniq: dict = {}
    for d in devs:
        uniq.setdefault(key(d), d)
    items = [(d["id"], d["name"], d["blocks"], d["wireless"]) for d in uniq.values()]
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        res = {r[0]: r for r in ex.map(one, items, chunksize=8)}
    by_key = {key(d): res[d["id"]] for d in uniq.values()}
    rows = []
    for d in devs:
        _, err, parts, secs = by_key[key(d)]
        rows.append({"id": d["id"], "category": d["category"], "pass": err is None, "error": err, "parts": parts})
    (HERE / "results.json").write_text(json.dumps(rows, indent=0) + "\n")
    cat = defaultdict(lambda: [0, 0])
    for r in rows:
        cat[r["category"]][0] += r["pass"]
        cat[r["category"]][1] += 1
    passed = sum(r["pass"] for r in rows)
    errs = Counter((r["error"] or "")[:90] for r in rows if not r["pass"])
    parts = [r["parts"] for r in rows if r["pass"]]
    lines = [
        "# Device catalog coverage (10-17)",
        "",
        f"Every device in engine/parts/devices.json composed from blocks (agent/blocks.py) and run through the "
        f"assembly loop's pre-build gate offline (no LLM, no KiCad): {passed}/{len(rows)} pass "
        f"({100 * passed / len(rows):.1f}%). {db['bases']} hand-written base devices, {len(rows) - db['bases']} "
        f"modifier variants; {len(items)} distinct compositions checked in {time.time() - t0:.0f} s.",
        "",
        "Gate per device: every piece in the library and the piece index; no lonely net as composed; the live "
        "fix-up chain (_normalize_pins through _analog) adds or drops no part; no LED stand-ins, ref mismatches, "
        "lonely nets, missing sound source or wiring lint after it; the generated MicroPython builds and passes its "
        "firmware test; firmware benchmarks generate; to_spec loads through etchlib (parts, pins). Rails are GND, "
        "3V3 and VBUS only (Pico VSYS never used). Not run offline: KiCad place/route, DRC, ERC, ngspice.",
        "",
        f"Parts per passing board: min {min(parts, default=0)}, median {sorted(parts)[len(parts) // 2] if parts else 0}, "
        f"max {max(parts, default=0)}.",
        "",
        "| Category | Pass | Devices | Rate |",
        "|---|---|---|---|",
    ]
    for c, (p, n) in sorted(cat.items(), key=lambda x: -x[1][1]):
        lines.append(f"| {c} | {p} | {n} | {100 * p / n:.0f}% |")
    if errs:
        lines += ["", "## Failures", "", "| Count | Error |", "|---|---|"]
        lines += [f"| {n} | {e} |" for e, n in errs.most_common(20)]
    (HERE / "coverage.md").write_text("\n".join(lines) + "\n")
    print(f"{passed}/{len(rows)} pass; {len(items)} compositions; {time.time() - t0:.0f}s")
    for e, n in errs.most_common(15):
        print(n, e)


if __name__ == "__main__":
    main()
