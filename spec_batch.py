"""Прогон 30 сайтов из дампов AniLabX через нашу спеку v1."""

from __future__ import annotations

import glob
import json
import re
import sys
import time
from pathlib import Path

import recon
import spec_build
import spec_run

TYPES = {"anime", "manga", "dorama", "book", "light_novel", "comics"}


def sites() -> list[dict]:
    files = sorted(glob.glob("/home/work/redroid/anilabx-parsers/pd/*.js")
                   + glob.glob("/home/work/redroid/anilabx-parsers/pd2/*.js"))
    result, seen = [], set()
    for path in files:
        try:
            data = json.load(open(path))
        except Exception:
            continue
        values = [value for value in data.values() if isinstance(value, str)]
        kind = next((value for value in values if value in TYPES), "")
        hosts = [value for value in values
                 if value.startswith("http") and "anilabx" not in value and "supp@gmail" not in value]
        if not hosts:
            continue
        base = hosts[0].rstrip("/")
        host = re.sub(r"^https?://", "", base).split("/")[0]
        if host in seen:
            continue
        seen.add(host)
        result.append({"host": host, "base": base, "kind": kind, "file": Path(path).name})
    return result


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    targets = sites()[:limit]
    print(f"сайтов: {len(targets)}\n")

    reports = []
    for index, site in enumerate(targets, 1):
        print(f"[{index}/{len(targets)}] {site['base']} ({site['kind']})", flush=True)
        try:
            spec = spec_build.build(site["base"])
        except Exception as exc:  # noqa: BLE001
            print(f"    сборка спеки упала: {str(exc)[:70]}")
            reports.append({"base": site["base"], "kind": site["kind"], "score": -1,
                            "error": str(exc)[:120]})
            continue
        if spec.get("error"):
            print(f"    {spec['error']}")
            reports.append({"base": site["base"], "kind": site["kind"], "score": -1,
                            "error": spec["error"]})
            continue
        try:
            report = spec_run.check(spec)
        except Exception as exc:  # noqa: BLE001
            print(f"    проверка упала: {str(exc)[:70]}")
            reports.append({"base": site["base"], "kind": site["kind"], "score": -1,
                            "error": str(exc)[:120]})
            continue
        report.update({"kind": site["kind"], "engine": spec.get("engine")})
        failed = [name for name, step in report["steps"].items() if not step.get("ok")]
        print(f"    {report['score']}/4, движок: {spec.get('engine')}, провал: {', '.join(failed) or '-'}")
        reports.append(report)
        time.sleep(2)

    Path("/tmp/spec_report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=1))
    good = [r for r in reports if r.get("score", -1) >= 3]
    print(f"\n=== итог: {len(good)}/{len(reports)} сайтов дали 3-4 из 4")
    for report in reports:
        mark = "OK  " if report.get("score", -1) >= 3 else "FAIL"
        print(f"  {mark} {report.get('score', -1)}/4  {report['base'][:52]}  {report.get('error', '')[:40]}")


if __name__ == "__main__":
    main()
