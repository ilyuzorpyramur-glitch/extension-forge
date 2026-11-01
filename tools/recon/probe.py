#!/usr/bin/env python3
"""Minimal Tadami/Aniyomi UI probe over adb.

Primitives (dump/tap/swipe/shot/back) plus a catalog walker that reports
duplicate titles and per-scroll progress. Used for the per-source acceptance
checklist in redroid.

Usage:
  probe.py shot out.png
  probe.py dump [--grep TEXT]
  probe.py tap X Y
  probe.py tap-text "AniDub" [--index 0]
  probe.py swipe up|down [--times 1]
  probe.py back
  probe.py cat-walk --scrolls 4 --out shots/dir
  probe.py log [--grep ExoPlayer] [--lines 200]
"""

import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

DEVICE = os.environ.get("ADB_DEVICE", "emulator-5554")
PKG = os.environ.get("TADAMI_PKG", "com.tadami.aurora")


def adb(*args, binary=False, timeout=60):
    cmd = ["adb", "-s", DEVICE] + list(args)
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if binary:
        return out.stdout
    return out.stdout.decode("utf-8", "replace")


def sh(*args):
    return adb("shell", *args)


def dump_xml():
    sh("uiautomator dump /sdcard/probe_ui.xml >/dev/null 2>&1")
    return adb("shell", "cat /sdcard/probe_ui.xml")


def nodes(xml_text):
    """Yield (text, desc, cx, cy) for every node with visible bounds."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return []
    out = []
    for n in root.iter("node"):
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        if not text and not desc:
            continue
        bounds = n.get("bounds") or ""
        m = re.match(r"\[(\d+),(\d+)]\[(\d+),(\d+)]", bounds)
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        out.append((text, desc, (x1 + x2) // 2, (y1 + y2) // 2))
    return out


def screen_size():
    out = sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (720, 1280)


def tap(x, y):
    sh(f"input tap {int(x)} {int(y)}")


def tap_text(needle, index=0, wait=1.5):
    items = [n for n in nodes(dump_xml()) if needle.lower() in (n[0] or n[1]).lower()]
    if len(items) <= index:
        return None
    tap(items[index][2], items[index][3])
    time.sleep(wait)
    return items[index]


def swipe(direction="up", times=1, duration=300):
    w, h = screen_size()
    x = w // 2
    for _ in range(times):
        if direction == "up":
            sh(f"input swipe {x} {int(h * 0.75)} {x} {int(h * 0.3)} {duration}")
        else:
            sh(f"input swipe {x} {int(h * 0.3)} {x} {int(h * 0.75)} {duration}")
        time.sleep(1.2)


def shot(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = adb("exec-out", "screencap -p", binary=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def visible_titles():
    """Title-like labels: long text nodes in the upper card area."""
    items = nodes(dump_xml())
    out = []
    for text, desc, cx, cy in items:
        t = text or desc
        if len(t) < 3 or len(t) > 90:
            continue
        out.append(t)
    return out


def cmd_cat_walk(scrolls, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    seen = {}
    report = []
    for i in range(scrolls + 1):
        titles = [t for t in visible_titles()]
        new = [t for t in titles if t not in seen]
        dupes = [t for t in titles if t in seen]
        for t in titles:
            seen[t] = seen.get(t, 0) + 1
        shot(os.path.join(out_dir, f"scroll-{i}.png"))
        report.append({"step": i, "titles": len(titles), "new": len(new), "dupes": dupes[:5]})
        if i < scrolls:
            swipe("up", 1)
    repeated = {t: c for t, c in seen.items() if c > 1}
    print({"scrolls": scrolls, "unique": len(seen), "repeated": list(repeated.items())[:10]})
    for r in report:
        print(r)
    return seen


def cmd_log(grep, lines):
    out = sh(f"logcat -d -t {lines}")
    for line in out.splitlines():
        if not grep or grep.lower() in line.lower():
            print(line)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("shot")
    p.add_argument("path")

    p = sub.add_parser("dump")
    p.add_argument("--grep")

    p = sub.add_parser("tap")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)

    p = sub.add_parser("tap-text")
    p.add_argument("needle")
    p.add_argument("--index", type=int, default=0)

    p = sub.add_parser("swipe")
    p.add_argument("direction", choices=["up", "down"])
    p.add_argument("--times", type=int, default=1)

    sub.add_parser("back")
    sub.add_parser("launch")

    p = sub.add_parser("cat-walk")
    p.add_argument("--scrolls", type=int, default=3)
    p.add_argument("--out", default="/tmp/probe")

    p = sub.add_parser("log")
    p.add_argument("--grep")
    p.add_argument("--lines", type=int, default=200)

    a = ap.parse_args()
    if a.cmd == "shot":
        print(shot(a.path))
    elif a.cmd == "dump":
        xml = dump_xml()
        for text, desc, cx, cy in nodes(xml):
            label = text or desc
            if a.grep and a.grep.lower() not in label.lower():
                continue
            print(f"{label[:60]:62s} {cx},{cy}")
    elif a.cmd == "tap":
        tap(a.x, a.y)
    elif a.cmd == "tap-text":
        print(tap_text(a.needle, a.index))
    elif a.cmd == "swipe":
        swipe(a.direction, a.times)
    elif a.cmd == "back":
        sh("input keyevent 4")
        time.sleep(1.2)
    elif a.cmd == "launch":
        sh(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1")
        time.sleep(3)
    elif a.cmd == "cat-walk":
        cmd_cat_walk(a.scrolls, a.out)
    elif a.cmd == "log":
        cmd_log(a.grep, a.lines)


if __name__ == "__main__":
    main()
