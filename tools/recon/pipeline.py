#!/usr/bin/env python3
"""Конвейер: extension-forge -> иконки -> сборка в CI -> стенд.

Запускается по одному сайту за раз и сам следит за нагрузкой, чтобы не убить VPS.

  python3 tools/pipeline.py --kind anime https://animejoy.ru https://sovetromantica.com
  python3 tools/pipeline.py --kind anime --from-file /tmp/sites.txt --limit 3
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

FORGE = Path("/home/work/repos/extension-forge/forge.py")
REPO = Path("/home/work/repos/aniyomi-ru-anime-repo")
MANGA_REPO = Path("/home/work/repos/mihon-ru-manga-repo")
WORK = Path("/home/work")

MAX_LOAD_FACTOR = 0.75      # доля от числа ядер
MIN_FREE_MB = 1500
PAUSE_BETWEEN = 20          # секунд между сайтами


def sh(cmd, timeout=600, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)



BLOCK_CODES = {403, 404, 410, 451, 503, 522, 523, 524, 526}


def reachable(url, timeout=15):
    """Быстрая проверка: живой сайт или блокер. Возвращает (ok, причина)."""
    import ssl
    import urllib.error
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as resp:
            body = resp.read(4000)
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            if len(body) < 400:
                return False, "пустой ответ"
            return True, ""
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)[:70]


def existing_module(repo, module):
    return (repo / "src" / "ru" / module).exists()

def load_ok():
    cores = os.cpu_count() or 2
    load1 = os.getloadavg()[0]
    with open("/proc/meminfo") as fh:
        free = 0
        for line in fh:
            if line.startswith("MemAvailable:"):
                free = int(line.split()[1]) // 1024
                break
    return load1 < cores * MAX_LOAD_FACTOR and free > MIN_FREE_MB, load1, free


def wait_for_room():
    while True:
        ok, load1, free = load_ok()
        if ok:
            return
        print(f"  ⏸ нагрузка {load1:.2f}, свободно {free} МБ — жду", flush=True)
        time.sleep(30)


def module_of(url):
    host = re.sub(r"^https?://", "", url).split("/")[0]
    host = host.split(":")[0]
    labels = [x for x in host.split(".") if x and x != "www"]
    # отбрасываем только домен верхнего уровня, остальное — часть имени
    if len(labels) > 1:
        labels = labels[:-1]
    return re.sub(r"[^a-z0-9]", "", "".join(labels))


def force_kind(site_id, kind):
    """recon угадывает категорию по разметке и часто ошибается — фиксируем явно."""
    spec = Path("/home/work/repos/extension-forge/sites") / f"{site_id}.yaml"
    if not spec.exists():
        return
    lines = spec.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith("kind:"):
            lines[index] = f"kind: {kind}"
            break
    else:
        lines.insert(0, f"kind: {kind}")
    spec.write_text("\n".join(lines) + "\n")


def generate(url, kind="anime", site_id=None):
    """Разведка и генерация. Возвращает (состояние, сообщение).

    Состояния: ready | skeleton | failed — скелет в репозиторий не идёт.
    """
    r = sh([sys.executable, str(FORGE), "add", url], timeout=300)
    out = (r.stdout + r.stderr).strip()

    # recon мог записать категорию неверно — переписываем и генерируем заново.
    if site_id:
        # kind в декларации — это aniyomi/mihon/lnreader, а не anime/manga
        force_kind(site_id, {"anime": "aniyomi", "manga": "mihon"}.get(kind, kind))
        spec = Path("/home/work/repos/extension-forge/sites") / f"{site_id}.yaml"
        if spec.exists():
            again = sh([sys.executable, str(FORGE), "generate", str(spec)], timeout=300)
            out = (again.stdout + again.stderr).strip()
            r = again

    if r.returncode == 4 or "СКЕЛЕТ" in out:
        return "skeleton", out[-900:]
    if r.returncode != 0:
        return "failed", out[-900:]

    # Проверяем, что рендер действительно создал модуль в нужном репозитории.
    module = site_id or ""
    target = (REPO if kind == "anime" else MANGA_REPO) / "src" / "ru" / module
    if module and not (target / "build.gradle").exists():
        return "failed", f"рендер не создал модуль {target}"
    return "ready", out[-900:]


def ensure_icon(module):
    """Добавляет модуль в make-icons.py и рисует иконку, если её нет."""
    script = REPO / ".github/scripts/make-icons.py"
    if not script.exists():
        return False
    text = script.read_text()
    if f'"{module}":' in text:
        return True
    if 'ICONS = {' not in text:
        return False
    mono = (module[:3]).upper()
    entry = f'    "{module}": ("{mono}", (120, 170, 235), (18, 24, 38)),\n'
    text = text.replace("ICONS = {\n", "ICONS = {\n" + entry, 1)
    script.write_text(text)
    sh([sys.executable, str(script), module], cwd=REPO, timeout=120)
    return True


def build_and_wait(repo, limit_min=12, module=""):
    """Коммит, пуш и ожидание сборки в CI."""
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "-c", "user.name=Ilyuzor",
        "-c", "user.email=346533302+ilyuzorpyramur-glitch@users.noreply.github.com",
        "commit", "-q", "-m", f"feat({module or 'pipeline'}): расширение из конвейера"], cwd=repo)
    sh(["git", "fetch", "-q", "origin"], cwd=repo)
    sh(["git", "-c", "user.name=Ilyuzor",
        "-c", "user.email=346533302+ilyuzorpyramur-glitch@users.noreply.github.com",
        "pull", "--rebase", "-q", "origin", "main"], cwd=repo)
    if sh(["git", "push", "-q", "origin", "main"], cwd=repo).returncode != 0:
        return "push-failed"
    time.sleep(20)
    deadline = time.time() + limit_min * 60
    while time.time() < deadline:
        r = sh(["gh", "api", f"repos/ilyuzorpyramur-glitch/{repo.name}/actions/runs?per_page=1",
                "--jq", '.workflow_runs[0] | "\\(.status) \\(.conclusion // "-")"'], cwd=repo, timeout=60)
        state = r.stdout.strip()
        if state.startswith("completed"):
            return state.split()[-1]
        time.sleep(30)
    return "timeout"


def audit(module, kind="anime"):
    script = WORK / "tools" / ("audit_anime.py" if kind == "anime" else "audit_manga.py")
    out = Path(f"/tmp/pipe_{module}.json")
    r = sh([sys.executable, str(script), "--batch", "1", "--no-video",
            "--only", module, "--out", str(out)], cwd=WORK, timeout=600)
    if not out.exists():
        return {"module": module, "error": (r.stdout + r.stderr)[-300:]}
    try:
        rows = json.loads(out.read_text())
    except Exception as exc:
        return {"module": module, "error": str(exc)}
    if not rows:
        return {"module": module, "error": "нет результатов"}
    row = rows[0]
    return {
        "module": module,
        "passed": row.get("passed"),
        "total": len(row.get("steps", [])),
        "failed": [s["name"] for s in row.get("steps", []) if not s["ok"]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--from-file")
    ap.add_argument("--kind", default="anime", choices=["anime", "manga"])
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--no-build", action="store_true", help="только разведка и генерация")
    ap.add_argument("--force", action="store_true", help="перегенерировать существующие модули")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.from_file:
        urls += [l.strip() for l in Path(args.from_file).read_text().splitlines() if l.strip()]
    urls = urls[: args.limit]
    if not urls:
        sys.exit("нечего делать: передай ссылки")

    repo = REPO if args.kind == "anime" else MANGA_REPO
    report = []

    for url in urls:
        wait_for_room()
        module = module_of(url)
        print(f"\n=== {url} (модуль {module})", flush=True)

        if not args.force and existing_module(repo, module):
            print("  ⏭ модуль уже есть — пропуск", flush=True)
            report.append({"url": url, "module": module, "stage": "exists"})
            continue

        alive, reason = reachable(url)
        if not alive:
            print(f"  ⛔ недоступен: {reason}", flush=True)
            report.append({"url": url, "module": module, "stage": "blocked", "error": reason})
            continue

        state, msg = generate(url, args.kind, module)
        if state == "skeleton":
            print("  ⛔ скелет: нужен HTML-рендерер — убираю модуль", flush=True)
            sh(["rm", "-rf", str(repo / "src" / "ru" / module)])
            report.append({"url": url, "module": module, "stage": "skeleton",
                           "error": "нужен рендерер по селекторам"})
            time.sleep(PAUSE_BETWEEN)
            continue
        if state == "failed":
            print("  ✖ генерация", flush=True)
            report.append({"url": url, "module": module, "stage": "generate", "error": msg[-300:]})
            sh(["rm", "-rf", str(repo / "src" / "ru" / module)])
            time.sleep(PAUSE_BETWEEN)
            continue
        print("  ✔ генерация", flush=True)

        ensure_icon(module)

        if args.no_build:
            report.append({"url": url, "module": module, "stage": "generated"})
            continue

        state = build_and_wait(repo, module=module)
        print(f"  сборка: {state}", flush=True)
        if state != "success":
            report.append({"url": url, "module": module, "stage": "build", "error": state})
            continue

        res = audit(module, args.kind)
        print(f"  стенд: {res.get('passed')}/{res.get('total')} "
              f"{res.get('failed') or ''}", flush=True)
        report.append({"url": url, **res, "stage": "audited"})
        time.sleep(PAUSE_BETWEEN)

    Path("/tmp/pipeline_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n=== итог:", json.dumps(report, ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()
