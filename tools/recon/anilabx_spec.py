#!/usr/bin/env python3
"""Cheap AniLabX -> extension handoff tool.

Two modes, both light on the VM:

  inventory
      Lists every parser the app has on disk (type + name). adb only, ~1 s.

  capture --seconds N --out spec.json [--tap X,Y]
      Snapshot the mitm capture log, optionally tap a point on screen, wait N
      seconds and summarise every request the app made: host, path, query
      keys, content type. This is the "handle" for writing a source: base URL,
      catalogue/search/detail/player endpoints, without decrypting anything.

Typical flow for one parser:
    anilabx_spec.py inventory
    anilabx_spec.py capture --seconds 45 --out /tmp/spec-anidub.json
    # open the parser in the app while it captures
"""

import argparse
import collections
import json
import os
import subprocess
import sys
import time

ADB = ["adb", "-s", os.environ.get("ADB_DEVICE", "emulator-5554")]
CAPTURE = os.environ.get("ANILABX_CAPTURE", "/home/work/redroid/capture/requests.jsonl")
SPIDERS = "/data/data/xyz.anilabx.app/spiders"


def adb(*args):
    return subprocess.run(ADB + list(args), capture_output=True, text=True).stdout


def cmd_inventory(_):
    out = adb("shell", f'for d in {SPIDERS}/*; do echo "$(basename $d):$(ls $d 2>/dev/null | wc -l)"; done')
    counts = {}
    for line in out.splitlines():
        if ":" in line:
            name, n = line.rsplit(":", 1)
            counts[name.strip()] = int(n.strip() or 0)
    names = adb("shell", f'find {SPIDERS} -name "*.nxt"')
    by_type = collections.defaultdict(list)
    for path in names.splitlines():
        parts = path.strip().split("/")
        if len(parts) >= 2 and parts[-1].endswith(".nxt"):
            by_type[parts[-2]].append(parts[-1][:-4])
    report = {
        "counts": counts,
        "total": sum(counts.values()),
        "parsers": {k: sorted(v) for k, v in sorted(by_type.items())},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def read_capture():
    rows = []
    if not os.path.exists(CAPTURE):
        return rows
    with open(CAPTURE, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def cmd_capture(args):
    start = len(read_capture())
    if args.tap:
        x, y = args.tap.split(",")
        adb("shell", f"input tap {x.strip()} {y.strip()}")
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(5)

    rows = read_capture()[start:]
    hosts = collections.defaultdict(lambda: {"count": 0, "paths": collections.Counter(), "params": set()})

    for row in rows:
        if "method" not in row:
            continue
        host = row.get("sni") or row.get("host") or "?"
        entry = hosts[host]
        entry["count"] += 1
        path = row.get("path", "").split("?")[0]
        entry["paths"][path] += 1
        query = row.get("path", "").partition("?")[2]
        for part in query.split("&"):
            key = part.split("=")[0]
            if key:
                entry["params"].add(key)

    spec = {
        "requests": len(rows),
        "hosts": {
            host: {
                "count": data["count"],
                "paths": [p for p, _ in data["paths"].most_common(12)],
                "queryKeys": sorted(data["params"])[:14],
            }
            for host, data in sorted(hosts.items(), key=lambda kv: -kv[1]["count"])
        },
    }
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(spec, fh, ensure_ascii=False, indent=2)
        print(f"written {args.out}")
    print(json.dumps(spec, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inventory").set_defaults(func=cmd_inventory)

    cap = sub.add_parser("capture")
    cap.add_argument("--seconds", type=int, default=40)
    cap.add_argument("--out")
    cap.add_argument("--tap", help="X,Y — optional tap before capturing")
    cap.set_defaults(func=cmd_capture)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
