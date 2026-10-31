#!/usr/bin/env python3
"""Аудит аниме-расширений стендом: инвентарь -> список источников -> Frida.

Устанавливает/подгружает APK, гоняет stand_batch.js батчами и складывает
отчёт в /tmp/audit_anime.json.

Usage:
  python3 audit_anime.py [--batch 4] [--only anidub,anistar] [--rebuild-apks]
"""

import argparse
import json
import os
import subprocess
import sys
import time

DEVICE = os.environ.get("STAND_DEVICE", "172.17.0.2:5555")
PKG = "com.tadami.aurora"
ROOT = "/home/work/repos/aniyomi-ru-anime-repo"
# Пакет стенда лежит внутри репозитория генератора.
STAND_DIR = os.path.dirname(os.path.abspath(__file__))
FORGE_ROOT = os.path.dirname(STAND_DIR)
WORKDIR = FORGE_ROOT
# Репозиторий расширений, который проверяем (переопределяется STAND_REPO).
_REPO_CANDIDATES = [
    os.path.expanduser("~/repos/aniyomi-ru-anime-repo"),
    "/home/work/repos/aniyomi-ru-anime-repo",
]
REPO_DIR = os.environ.get("STAND_REPO") or next(
    (c for c in _REPO_CANDIDATES if os.path.isdir(c)),
    _REPO_CANDIDATES[0],
)
TMP = "/data/local/tmp"


def sh(*args, check=False):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r.stdout.strip()


def adb(*args, check=False):
    return sh("adb", "-s", DEVICE, *args, check=check)


# Локальный индекс: raw.githubusercontent отдаёт закешированную копию, и стенд
# проверял предыдущую сборку. Перед прогоном делаем git pull в репозитории.
INDEX_URL = os.path.join(REPO_DIR, "index.min.json")


def _fresh(url):
    """raw.githubusercontent кеширует ответы: добавляем метку времени, иначе
    стенд проверяет предыдущую сборку."""
    import time

    sep = "&" if "?" in url else "?"
    return url + sep + "t=" + str(int(time.time()))


def load_index(source):
    """Читает index.min.json по URL или с диска — так же, как это делает приложение."""
    if source.startswith("http://") or source.startswith("https://"):
        import urllib.request

        with urllib.request.urlopen(_fresh(source), timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with open(source, encoding="utf-8") as fh:
        return json.load(fh)


def install_from_index(entry, module, base=""):
    """Скачивает APK по ссылке из индекса и ставит его — путь обновления приложения."""
    import urllib.request

    os.makedirs("/tmp/index_apk", exist_ok=True)
    local = f"/tmp/index_apk/{module}.apk"
    name = entry["apk"]
    # Приложение берёт APK из подпапки apk/ рядом с индексом; на всякий случай
    # пробуем и корень — вдруг репозиторий разложен иначе.
    candidates = []
    if name.startswith("http"):
        candidates.append(name)
    elif base:
        candidates.append(base.rstrip("/") + "/apk/" + name.lstrip("/"))
        candidates.append(base.rstrip("/") + "/" + name.lstrip("/"))
    last = None
    for url in candidates:
        try:
            if url.startswith("http"):
                with urllib.request.urlopen(_fresh(url), timeout=180) as resp, open(local, "wb") as fh:
                    fh.write(resp.read())
            else:
                import shutil

                shutil.copyfile(url, local)
            break
        except Exception as exc:
            last = exc
    else:
        raise last
    remote = f"{TMP}/{module}.apk"
    adb("push", local, remote)
    adb("shell", "su", "0", "chmod", "444", remote)
    # В этом форке расширения не ставятся как пакеты: приложение грузит APK
    # через DexClassLoader, поэтому проверяем сам файл, а не pm/dumpsys.
    import zipfile

    size = os.path.getsize(local)
    if not zipfile.is_zipfile(local):
        raise RuntimeError(f"{module}: скачанный файл не APK ({size} байт)")
    with zipfile.ZipFile(local) as zf:
        if "classes.dex" not in zf.namelist():
            raise RuntimeError(f"{module}: в APK нет classes.dex")
    print(f"    скачано {size // 1024} КБ, classes.dex на месте", flush=True)
    return remote


def installed_version(pkg):
    out = adb("shell", "dumpsys", "package", pkg)
    import re as _re

    code = _re.search(r"versionCode=(\d+)", out)
    name = _re.search(r"versionName=(\S+)", out)
    return (int(code.group(1)) if code else 0, name.group(1) if name else "")


def derive_fqcn(module, lang):
    """Разведка иногда не видит класс источника — определяем его по исходнику.

    Берём первый .kt с объявлением класса (не data class / object / interface)
    и склеиваем с package.
    """
    import re as _re

    candidates = [
        os.path.join(REPO_DIR, "src", lang, module, "src"),
    ]
    root = next((c for c in candidates if os.path.isdir(c)), "")
    if not root:
        return ""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if not name.endswith(".kt"):
                continue
            text = open(os.path.join(dirpath, name), encoding="utf-8").read()
            pkg = _re.search(r"^\s*package\s+([\w.]+)", text, _re.M)
            cls = _re.search(r"^(?:open\s+|abstract\s+)?class\s+(\w+)", text, _re.M)
            if pkg and cls:
                return f"{pkg.group(1)}.{cls.group(1)}"
    return ""


def inventory():
    out = subprocess.run([sys.executable, os.path.join(STAND_DIR, "repo_inventory.py"), "anime", "--json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def apk_for(module, rows, pkg=""):
    local = rows.get(module, "")
    if local:
        remote = f"{TMP}/{module}.apk"
        adb("push", local, remote)
        adb("shell", "su", "0", "chmod", "444", remote)
        return remote
    installed = adb("shell", "pm", "path", pkg or f"eu.kanade.tachiyomi.animeextension.ru.{module}")
    if installed.startswith("package:"):
        return installed.split("package:")[1].splitlines()[0]
    return ""
    remote = f"{TMP}/{module}.apk"
    adb("push", local, remote)
    adb("shell", "su", "0", "chmod", "444", remote)
    return remote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="/tmp/audit_anime.json")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--from-index", action="store_true",
                    help="как приложение: тянуть APK из index.min.json, а не из локальной сборки")
    ap.add_argument("--index", default=INDEX_URL)
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--nsfw", action="store_true", help="включить NSFW-расширения из индекса")
    ap.add_argument("--no-retry", action="store_true", help="не повторять модули с сетевыми сбоями")
    ap.add_argument("--title-url", default="", help="проверить один тайтл по ссылке")
    args = ap.parse_args()

    def ensure_app():
        current = adb("shell", "pidof", PKG).split()
        if not current:
            adb("shell", "am", "start", "-n", f"{PKG}/eu.kanade.tachiyomi.ui.main.MainActivity")
            time.sleep(14)
            current = adb("shell", "pidof", PKG).split()
        return current[0] if current else ""

    pid = ensure_app()

    rows = {r["module"]: r["apk"] for r in inventory()}
    items = []
    for row in inventory():
        if not row["sources"]:
            derived = derive_fqcn(row["module"], row["lang"])
            if derived:
                row["sources"] = [{"fqcn": derived}]
                print(f"  класс найден по исходнику: {row['module']} → {derived}", flush=True)
        if row["sources"]:
            items.append(row)

    index_entries = {}
    index_base = ""
    if args.from_index:
        index_base = args.index.rsplit("/", 1)[0]
        for entry in load_index(args.index):
            if args.lang != "any" and entry.get("lang") != args.lang:
                continue
            if entry.get("nsfw") and not args.nsfw:
                continue
            index_entries[entry["pkg"].rsplit(".", 1)[-1]] = entry
        missing = [mod for mod in index_entries if mod not in {r["module"] for r in items}]
        print(f"индекс: {len(index_entries)} расширений (lang={args.lang}); "
              f"нет в локальной разведке: {', '.join(missing) or '—'}", flush=True)
        items = [r for r in items if r["module"] in index_entries]
    if args.only:
        wanted = set(args.only.split(","))
        items = [r for r in items if r["module"] in wanted]
    # Один модуль может встретиться в индексе дважды (разные записи) —
    # иначе стенд проверяет его повторно и путает шаги.
    seen_modules = set()
    unique = []
    for r in items:
        if r["module"] in seen_modules:
            continue
        seen_modules.add(r["module"])
        unique.append(r)
    items = unique

    results = []
    for start in range(0, len(items), args.batch):
        chunk = items[start:start + args.batch]
        specs = []
        for r in chunk:
            # основной класс источника — тот, что не DTO
            fqcns = [s["fqcn"] for s in r["sources"]]
            entry = index_entries.get(r["module"])
            if entry:
                print(f"  индекс {r['module']}: v{entry.get('version', '?')} "
                      f"(code {entry.get('code', '?')}) — качаю как приложение", flush=True)
                apk = install_from_index(entry, r["module"], index_base)
            else:
                pkg = f"eu.kanade.tachiyomi.animeextension.{r['lang']}.{r['module']}"
                apk = apk_for(r["module"], rows, pkg)
            specs.append({"fqcn": fqcns[0], "fqcns": fqcns, "apk": apk,
                          "module": r["module"], "video": not args.no_video,
                          "url": args.title_url})
        script = open(os.path.join(STAND_DIR, "stand_batch.js"), encoding="utf-8").read()
        script = script.replace("%SOURCES%", json.dumps(specs, ensure_ascii=False))
        path = f"/tmp/stand_batch_{start}.js"
        open(path, "w", encoding="utf-8").write(script)
        print(f"--- батч {start // args.batch + 1}: " + ", ".join(s["module"] for s in specs), flush=True)
        pid = ensure_app()
        if not pid:
            print("  приложение не поднялось, пропуск батча", flush=True)
            continue
        out = subprocess.run(["sudo", "-n", "/usr/bin/python3", os.path.join(STAND_DIR, "run_frida.py"),
                              pid, path, "240"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            try:
                msg = json.loads(line)
            except Exception:
                continue
            payload = msg.get("payload") or {}
            if payload.get("source") and "steps" in payload:
                payload["module"] = next((s["module"] for s in specs if payload["source"] in [s["fqcn"]] + s["fqcns"]), "?")
                results.append(payload)
                print(f"  {payload['module']}: {payload.get('passed')}/{len(payload['steps'])}", flush=True)
        open("/tmp/last_out.txt", "w", encoding="utf-8").write(out)
        answered = {r["module"] for r in results if r.get("module") in {x["module"] for x in specs}}
        for spec in specs:
            if spec["module"] in answered:
                continue
            # Стенд не прислал ни одного шага: расширение не загрузилось
            # или процесс приложения упал. Это тоже результат, а не тишина.
            results.append({
                "source": spec["fqcn"],
                "module": spec["module"],
                "steps": [{"name": "load", "ok": False,
                           "error": "стенд не уложился в лимит: шаги не завершились"}],
                "passed": 0,
                "failed": 1,
            })
            print(f"  {spec['module']}: нет ответа стенда", flush=True)
        time.sleep(2)

    NET = ("SSLHandshake", "SocketTimeout", "ConnectException", "UnknownHost",
           "connection closed", "Read timed out", "failed to connect")

    if not args.no_retry:
        def net_bad(row):
            steps = row.get("steps") or []
            if not steps or steps[0].get("ok"):
                return False
            text = str(steps[0].get("error") or "")
            # «Нет ответа стенда» тоже повторяем: часто это сбой первой загрузки APK.
            if "не вернул результат" in text:
                return True
            return any(mark in text for mark in NET)

        retry_modules = [r["module"] for r in results if net_bad(r)]
        if retry_modules:
            print(f"\n--- повтор сетевых сбоев: {', '.join(retry_modules)}", flush=True)
            retry_out = args.out + ".retry"
            cmd = [sys.executable, os.path.abspath(__file__), "--batch", str(args.batch),
                   "--only", ",".join(retry_modules), "--out", retry_out, "--no-retry"]
            if args.no_video:
                cmd.append("--no-video")
            subprocess.run(cmd, capture_output=True, text=True)
            if os.path.exists(retry_out):
                retried = {r["module"]: r for r in json.load(open(retry_out, encoding="utf-8"))}
                for i, row in enumerate(results):
                    fresh = retried.get(row["module"])
                    if not fresh:
                        continue
                    fresh["retried"] = True
                    if (fresh.get("passed") or 0) >= (row.get("passed") or 0):
                        results[i] = fresh

    json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [r for r in results if r.get("failed")]
    print(f"\nвсего проверено {len(results)}; с ошибками {len(bad)}")
    for r in bad:
        fails = [s["name"] for s in r["steps"] if not s["ok"]]
        print(f"  {r['module']}: {', '.join(fails)}")


if __name__ == "__main__":
    main()
