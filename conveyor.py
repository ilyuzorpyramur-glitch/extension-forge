#!/usr/bin/env python3
"""Конвейер: ссылка на сайт → расширение → сборка → стенд → исправления → знание.

Стадии:
  1. classify  — тип сайта (aniyomi/mihon/lnreader) и NSFW
  2. generate  — разведка, декларация, модуль, иконка
  3. build     — git push → GitHub Actions → починка упавшей сборки
  4. stand     — проверка собранного APK в приложении
  5. feedback  — что именно сломалось → правки → новая версия → 3–5
  6. learn     — ошибка и её лечение уходят в знания, в следующий раз бесплатно

Запуск:
  python3 forge.py pipe sites.txt
  python3 forge.py pipe https://example.ru --classify-only
  python3 forge.py pipe sites.txt --no-build     # без CI, только код и стенд
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(FORGE_ROOT))

import aihtml  # noqa: E402
import buildwatch  # noqa: E402
import gemini  # noqa: E402
import recon  # noqa: E402
import smartparse  # noqa: E402

REPOS = {
    "aniyomi": Path("/home/work/repos/aniyomi-ru-anime-repo"),
    "mihon": Path("/home/work/repos/mihon-ru-manga-repo"),
    "lnreader": Path("/home/work/repos/lnreader-ru-plugins"),
}
LESSONS = FORGE_ROOT / "knowledge" / "lessons.json"
BEST = FORGE_ROOT / "knowledge" / "best.json"
FAILS = FORGE_ROOT / "knowledge" / "fails.json"
QUARANTINE = FORGE_ROOT / "sites" / "quarantine.yaml"
REPLAY = FORGE_ROOT / "replay"
REPORTS = FORGE_ROOT / "reports"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

KIND_WORDS = {
    "aniyomi": [("смотреть онлайн", 4), ("озвучка", 4), ("озвучк", 3), ("эпизод", 3),
                ("серии", 2), ("серия", 2), ("плеер", 3), ("аниме", 2), ("дорама", 3),
                ("сериал", 2), ("фильмы", 1), ("трейлер", 1)],
    "mihon": [("манга", 5), ("манхва", 5), ("маньхуа", 4), ("читать мангу", 5),
              ("главы", 2), ("глава", 2), ("том", 2), ("webtoon", 3), ("manga", 3)],
    "lnreader": [("ранобэ", 6), ("новелл", 4), ("light novel", 6), ("книги", 2),
                 ("книга", 2), ("читать онлайн", 2), ("том", 3), ("перевод", 1)],
}
HOST_HINTS = (("ranobe", "lnreader"), ("novel", "lnreader"), ("book", "lnreader"),
              ("anime", "aniyomi"), ("serial", "aniyomi"), ("video", "aniyomi"),
              ("film", "aniyomi"), ("mang", "mihon"), ("manga", "mihon"))

STEP_HINTS = {
    "catalog": ("каталог не отдаёт тайтлы", "проверить base url, заголовки и путь списка"),
    "scroll": ("не листается", "пагинация: offset/page/per_page — сверить с сайтом"),
    "dup": ("дубли в списке", "dedupe по url и названию"),
    "details": ("детали не открываются", "DTO не совпадает с ответом: типы полей"),
    "status": ("статус не определяется", "взять из поля сайта (год окончания/статус)"),
    "thumb": ("постер не грузится", "относительный url → абсолютный, поле постера"),
    "thumbs": ("часть постеров битая", "разные поля постера, https, размер"),
    "episodes": ("серии не отдаются", "селектор/поле списка серий"),
    "order": ("порядок серий неверный", "сортировать по убыванию номера"),
    "epurls": ("у серии нет url", "поле url серии"),
    "filters": ("фильтры не работают", "мультивыбор в одном параметре + подключение к запросу"),
    "search": ("поиск не находит", "эндпоинт/параметр поиска, пустой запрос = каталог"),
    "video": ("видео не играет", "цепочка хост→плеер: id, а не content_id; заголовки"),
}


# ---------------------------------------------------------------- знания

def load_lessons() -> list[dict]:
    try:
        return json.loads(LESSONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_lessons(items: list[dict]) -> None:
    LESSONS.parent.mkdir(parents=True, exist_ok=True)
    LESSONS.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=1), encoding="utf-8")


def signature(step: str, message: str) -> str:
    text = re.sub(r"\d+", "#", message.lower())
    text = re.sub(r"[^a-zа-яё#: ]+", " ", text)
    return f"{step}: " + " ".join(text.split())[:120]


def recall(step: str, message: str) -> dict | None:
    target = signature(step, message)
    for item in reversed(load_lessons()):
        if item.get("sig") == target:
            return item
    return None


def learn(step: str, message: str, cause: str, fix: str, kind: str, site: str) -> None:
    items = load_lessons()
    sig = signature(step, message)
    for item in items:
        if item.get("sig") == sig:
            item["hits"] = item.get("hits", 1) + 1
            item["ts"] = int(time.time())
            save_lessons(items)
            return
    items.append({"sig": sig, "step": step, "cause": cause, "fix": fix,
                  "kind": kind, "site": site, "hits": 1, "ts": int(time.time())})
    save_lessons(items)


# ---------------------------------------------------------------- classify

def _fetch(url: str) -> str:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", "replace")
    except Exception:
        return ""


def classify(url: str) -> dict:
    """Тип сайта и NSFW. Правила решают почти всегда, модель — только при ничьей."""
    if not url.startswith("http"):
        url = "https://" + url
    html = _fetch(url)
    if not html:
        return {"url": url, "kind": "", "nsfw": False, "confidence": 0, "error": "страница не открылась"}

    text = html.lower()
    scores = {kind: 0 for kind in KIND_WORDS}
    for kind, words in KIND_WORDS.items():
        for word, weight in words:
            if word in text:
                scores[kind] += weight

    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    for hint, kind in HOST_HINTS:
        if hint in host:
            scores[kind] += 6

    for kind, marker in (("mihon", "/manga"), ("lnreader", "/ranobe"), ("aniyomi", "/anime")):
        scores[kind] += min(4, text.count('href="' + marker))

    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    top, top_score = ranked[0]
    margin = top_score - ranked[1][1]

    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if match:
        title = " ".join(match.group(1).split())[:120]

    nsfw = recon.detect_nsfw(html, url)
    source = "правила"
    plain = re.sub(r"<[^>]+>", " ", text)[:1200]

    # Уверены — не тратим модель.
    if top_score < 8 or margin < 4:
        answer = gemini.ask_json(
            "Определи тип сайта для расширения.\n"
            f"URL: {url}\nЗаголовок: {title}\n"
            f"Начало текста: {plain}\n\n"
            'Верни {"kind":"aniyomi|mihon|lnreader","nsfw":true|false}.\n'
            "aniyomi — видео/серии/озвучка, mihon — манга/главы/страницы, "
            "lnreader — текст/ранобэ/книги.",
            max_tokens=60,
        )
        if answer.get("kind") in KIND_WORDS:
            top = answer["kind"]
            nsfw = bool(answer.get("nsfw", nsfw))
            source = "gemini"

    return {"url": url, "kind": top, "nsfw": nsfw, "confidence": margin,
            "scores": scores, "source": source, "title": title}


# ---------------------------------------------------------------- модуль

def site_id_for(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0]
    parts = [p for p in host.split(".") if p]
    core = parts[-2] if len(parts) >= 2 else parts[0]
    return re.sub(r"[^a-z0-9]", "", core)



def _load_map(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_map(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def best_score(site: str) -> dict:
    return _load_map(BEST).get(site, {})


def set_best(site: str, passed: int, total: int) -> None:
    data = _load_map(BEST)
    data[site] = {"passed": passed, "total": total, "ts": int(time.time())}
    _save_map(BEST, data)


def register_fail(site: str) -> int:
    data = _load_map(FAILS)
    data[site] = data.get(site, 0) + 1
    _save_map(FAILS, data)
    if data[site] >= 3 and site not in quarantine():
        QUARANTINE.parent.mkdir(parents=True, exist_ok=True)
        with QUARANTINE.open("a", encoding="utf-8") as handle:
            handle.write(f"- {site}: три прогона без результата (снять вручную)\n")
    return data[site]


def quarantine() -> set:
    try:
        text = QUARANTINE.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {line.split(":", 1)[0].strip("- ").strip() for line in text.splitlines() if line.strip().startswith("-")}


def save_replay(site: str, data: dict) -> None:
    """Образцы страниц: починка идёт без повторных запросов к сайту."""
    REPLAY.mkdir(parents=True, exist_ok=True)
    (REPLAY / f"{site}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def module_dir(kind: str, site_id: str) -> Path | None:
    repo = REPOS[kind]
    base = repo / "src" / "ru" / site_id
    return base if base.is_dir() else None


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900,
        env: dict | None = None) -> tuple[int, str]:
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                            timeout=timeout, env=env)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def bump(kind: str, site_id: str) -> int:
    code, out = run([sys.executable, str(FORGE_ROOT / "forge.py"), "bump", kind, site_id])
    match = re.search(r"→ (\d+)", out)
    return int(match.group(1)) if match else 0


# ---------------------------------------------------------------- стенд

STAND_LOCK = threading.Lock()


def run_stand(kind: str, site_id: str, title_url: str = "") -> dict:
    with STAND_LOCK:
        return _stand_locked(kind, site_id, title_url)


def _stand_locked(kind: str, site_id: str, title_url: str = "") -> dict:
    repo = REPOS[kind]
    # Стенд читает локальный индекс: подтягиваем свежий.
    buildwatch.sh(["git", "-C", str(repo), "fetch", "-q", "origin"])
    buildwatch.sh(["git", "-C", str(repo), "pull", "--rebase", "-q", "origin", "main"])

    out_path = f"/tmp/pipe_stand_{site_id}_{int(time.time())}.json"
    cmd = [sys.executable, str(FORGE_ROOT / "forge.py"), "audit",
           "--from-index", "--lang", "any", "--batch", "1", "--only", site_id,
           "--no-retry", "--out", out_path]
    if title_url:
        cmd += ["--title-url", title_url]
    env = dict(os.environ, STAND_REPO=str(repo))
    code, out = run(cmd, timeout=900, env=env)
    try:
        rows = json.loads(Path(out_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"module": site_id, "passed": 0, "steps": [], "error": out[-400:]}
    return rows[0] if rows else {"module": site_id, "passed": 0, "steps": []}


def failures(row: dict) -> list[dict]:
    return [step for step in row.get("steps", []) if not step.get("ok")]


def feedback_text(row: dict) -> list[str]:
    """Что стенд просит исправить — словами, по шагам."""
    notes = []
    for step in failures(row):
        name = step.get("name", "")
        cause, fix = STEP_HINTS.get(name, ("неизвестный шаг", "разобрать вручную"))
        detail = str(step.get("error") or step.get("value") or "")[:200]
        known = recall(name, detail)
        tail = f" (уже встречалось: {known['fix']})" if known else ""
        notes.append(f"{name}: {cause} → {fix}{tail} :: {detail}")
    return notes


def ai_fix_module(kind: str, site_id: str, row: dict) -> list[str]:
    """Просит модель починить конкретный шаг. Только когда правила не помогли."""
    if not gemini.available():
        return []
    directory = module_dir(kind, site_id)
    if not directory:
        return []
    sources = sorted(directory.rglob("*.kt"))
    if not sources:
        return []
    chunks = []
    for source_path in sources[:2]:
        chunks.append("// " + source_path.name + "\n" + source_path.read_text(encoding="utf-8")[:4000])
    body = "\n\n".join(chunks)
    problems = "\n".join(
        f"- {s.get('name')}: {str(s.get('error') or s.get('value'))[:200]}" for s in failures(row)[:5]
    )
    answer = gemini.ask_json(
        "Расширение Aniyomi для русского сайта. Стенд нашёл ошибки.\n"
        f"Тип: {kind}\nОшибки:\n{problems}\n\nКод:\n{body[:9000]}\n\n"
        'Верни {"patches":[{"file":"Имя.kt","search":"точный фрагмент","replace":"замена"}],"cause":"..."}',
        max_tokens=2500,
    )
    applied = []
    for patch in answer.get("patches", [])[:4]:
        path = next((p for p in sources if p.name == Path(patch.get("file", "")).name), None)
        if not path:
            continue
        text = path.read_text(encoding="utf-8")
        search, replace = patch.get("search", ""), patch.get("replace", "")
        if search and search in text:
            path.write_text(text.replace(search, replace, 1), encoding="utf-8")
            applied.append(f"{path.name}: {answer.get('cause', 'правка ИИ')[:80]}")
    return applied


# ---------------------------------------------------------------- конвейер

def _safe_process(url: str, args) -> dict:
    try:
        return process_site(url, args)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # один сайт не должен валить прогон
        return {"url": url, "status": f"сбой: {exc}"}


def _file_issue(rows: list[dict], report: Path) -> None:
    """Итог прогона — в issue: видно, что и когда сломалось."""
    bad = [row for row in rows if row.get("status") != "готово"]
    if not bad:
        return
    lines = ["| Сайт | Тип | Статус | Что видел стенд |", "|---|---|---|---|"]
    for row in bad:
        steps = "; ".join(str(note)[:80] for note in (row.get("notes") or [])[:3])
        lines.append(f"| {row.get('url', '')} | {row.get('kind', '')} | {row.get('status', '')} | {steps} |")
    body = "\n".join(lines) + f"\n\nОтчёт: `{report.name}`"
    repo = "ilyuzorpyramur-glitch/aniyomi-ru-anime-repo"
    result = buildwatch.sh(["gh", "issue", "create", "-R", repo, "--title",
                            f"Конвейер: {len(bad)} сайтов без результата",
                            "--body", body, "--label", "pipeline"])
    print(f"  issue: {result.strip().splitlines()[-1][:100] if result.strip() else 'не создан'}")


def process_site(url: str, args) -> dict:
    started = time.time()
    result = {"url": url, "site_id": "", "kind": "", "nsfw": False, "status": "?", "notes": [], "steps": {}}

    info = classify(url)
    result.update({"kind": info.get("kind", ""), "nsfw": info.get("nsfw", False),
                   "confidence": info.get("confidence", 0), "classified_by": info.get("source", "")})
    print(f"\n=== {url}")
    print(f"  тип: {result['kind']} | NSFW: {'да' if result['nsfw'] else 'нет'} "
          f"| уверенность {result['confidence']} ({result['classified_by']})")
    if not result["kind"]:
        result["status"] = "классификация не удалась"
        return result
    if args.classify_only:
        result["status"] = "классификация"
        return result
    if args.only_kind and result["kind"] not in args.only_kind.split(","):
        result["status"] = f"пропуск (нужен {args.only_kind})"
        return result

    site_id = site_id_for(url)
    result["site_id"] = site_id
    if site_id in quarantine() and not args.force:
        result["status"] = "в карантине"
        print(f"  ↷ {site_id} в карантине — вручную снимите sites/quarantine.yaml")
        return result
    if module_dir(result["kind"], site_id) and not args.force:
        result["status"] = "уже есть в репозитории"
        print(f"  ↷ {site_id} уже есть — пропуск (--force, чтобы перегенерировать)")
        return result

    # --- 2. генерация
    code, out = run([sys.executable, str(FORGE_ROOT / "forge.py"), "add", url, "--force"])
    result["steps"]["generate"] = "ok" if code == 0 else f"код {code}"
    if code != 0:
        # Скелет — это не результат, но и не приговор: для HTML-сайтов
        # тело парсера пишет модель по образцам страниц.
        skeleton = "заглушка" in out or "TODO" in out
        ai_ok = False
        if skeleton and args.ai_html:
            from forge import SiteSpec

            spec = SiteSpec.from_yaml(FORGE_ROOT / "sites" / f"{site_id}.yaml")
            directory = module_dir(result["kind"], site_id)
            if directory:
                save_replay(site_id, aihtml.samples(spec.base_url, spec.selectors.get("list_path", "")))
                ai_ok, message = aihtml.build(spec, directory, REPOS[result["kind"]])
                print(f"  {'✔' if ai_ok else '✖'} {message}")
                result["notes"].append(message)
        if not ai_ok:
            result["status"] = "генерация не удалась"
            result["notes"].append(out[-600:])
            repo_path = REPOS[result["kind"]]
            buildwatch.sh(["git", "-C", str(repo_path), "checkout", "--", "."])
            buildwatch.sh(["git", "-C", str(repo_path), "clean", "-fdq", f"src/ru/{site_id}"])
            register_fail(site_id)
            print(f"  ✖ генерация: {out.strip().splitlines()[-1] if out.strip() else code}")
            return result
        result["steps"]["generate"] = "ai"
    print("  ✔ модуль и иконка готовы")

    # --- 3-5. сборка и стенд, до args.iterations кругов
    repo = REPOS[result["kind"]]
    repo_slug = buildwatch.slug(repo)
    for attempt in range(1, args.iterations + 1):
        print(f"  --- круг {attempt}")
        if not args.no_build and repo_slug:
            if not buildwatch.commit_push(repo, f"feat({site_id}): конвейер, круга {attempt}"):
                print("  ↷ нечего пушить")
            else:
                started_run = time.time()
                run_info = buildwatch.wait_run(repo_slug, timeout=args.build_timeout)
                result["steps"][f"build{attempt}"] = run_info.get("conclusion", "?")
                if run_info.get("conclusion") != "success":
                    errors = buildwatch.parse_errors(buildwatch.failed_log(repo_slug, run_info.get("id", 0)))
                    print(f"  ✖ сборка: {len(errors)} ошибок")
                    fixed = buildwatch.fix_mechanical(repo / "src" / "ru" / site_id, errors) if errors else []
                    if not fixed and errors:
                        fixed = buildwatch.fix_with_ai(repo / "src" / "ru" / site_id, errors)
                    for note in fixed:
                        result["notes"].append(note)
                    if fixed:
                        bump(result["kind"], site_id)
                        buildwatch.commit_push(repo, f"fix({site_id}): автопочинка сборки")
                        continue
                    for error in errors[:5]:
                        result["notes"].append(f"{error['file']}:{error['line']} {error['message'][:120]}")
                    result["status"] = "сборка падает"
                    return result
                print(f"  ✔ сборка за {int(time.time() - started_run)} с")

        if args.no_stand:
            result["status"] = "собран"
            return result
        if result["kind"] != "aniyomi":
            # Стенд работает через приложение с аниме-расширениями.
            result["status"] = f"собран ({result['kind']}: стенд не применим)"
            return result

        row = run_stand(result["kind"], site_id)
        total = len(row.get("steps", []))
        passed = row.get("passed", 0)
        previous = best_score(site_id)
        if previous.get("passed") is not None and passed < previous["passed"] and total:
            # Стало хуже — откатываем модуль к состоянию до правок.
            repo_path = REPOS[result["kind"]]
            buildwatch.sh(["git", "-C", str(repo_path), "checkout", "HEAD~1", "--", f"src/ru/{site_id}"])
            buildwatch.commit_push(repo_path, f"revert({site_id}): правки ухудшили стенд")
            result["status"] = f"откат: было {previous['passed']}/{previous.get('total', total)}, стало {passed}/{total}"
            print(f"  ↺ {result['status']}")
            return result
        if total and passed >= previous.get("passed", -1):
            set_best(site_id, passed, total)
        result["steps"][f"stand{attempt}"] = f"{passed}/{total}"
        print(f"  стенд: {passed}/{total}")
        if passed == total and total:
            result["status"] = "готово"
            result["passed"] = passed
            result["total"] = total
            result["seconds"] = int(time.time() - started)
            return result

        notes = feedback_text(row)
        result["notes"].extend(notes[:8])
        for note in notes[:8]:
            print(f"    · {note[:150]}")

        for step in failures(row):
            cause, fix = STEP_HINTS.get(step.get("name", ""), ("неизвестно", "разобрать вручную"))
            learn(step.get("name", ""), str(step.get("error") or ""), cause, fix,
                  result["kind"], site_id)

        fixed = ai_fix_module(result["kind"], site_id, row)
        if not fixed:
            result["status"] = f"стенд {passed}/{total}"
            return result
        for note in fixed:
            result["notes"].append(note)
            print(f"    ✎ {note}")
        bump(result["kind"], site_id)

    result["status"] = "исчерпаны круги"
    return result


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="forge pipe", description="Сайты → расширения без ручной работы")
    parser.add_argument("sources", nargs="*", help="URL, файл со списком или '-' для stdin")
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument("--no-build", action="store_true", help="без CI: только код и стенд")
    parser.add_argument("--no-stand", action="store_true")
    parser.add_argument("--only-kind", default="", help="aniyomi|mihon|lnreader")
    parser.add_argument("--force", action="store_true", help="перегенерировать существующие")
    parser.add_argument("--repair", default="", help="чинить существующий модуль: только стенд и правки")
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--build-timeout", type=int, default=1500)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pause", type=int, default=20, help="пауза между сайтами, с")
    parser.add_argument("--jobs", type=int, default=1, help="сколько сайтов вести одновременно")
    parser.add_argument("--ai-html", action="store_true", default=True, help="писать парсер для HTML-сайтов моделью")
    parser.add_argument("--no-ai-html", dest="ai_html", action="store_false")
    parser.add_argument("--issue", action="store_true", help="завести issue с итогом прогона")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    urls: list[str] = []
    for source in args.sources or ["-"]:
        if source == "-":
            urls += [line.strip() for line in sys.stdin if line.strip()]
        elif source.startswith("http"):
            urls.append(source)
        else:
            path = Path(source)
            if path.exists():
                urls += [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                         if line.strip() and not line.startswith("#")]
            else:
                print(f"✖ не найдено: {source}")
    if not urls:
        parser.print_help()
        return
    if args.limit:
        urls = urls[:args.limit]

    print(gemini.status())

    if args.repair:
        kind = args.only_kind or next((k for k in REPOS if module_dir(k, args.repair)), "")
        if not kind:
            print(f"✖ модуль {args.repair} не найден ни в одном репозитории")
            return
        row = {"url": f"repair:{args.repair}", "site_id": args.repair, "kind": kind,
               "status": "?", "notes": [], "steps": {}}
        if kind != "aniyomi":
            print(f"  стенд для {kind} не применим (только аниме)")
            return
        stand = run_stand(kind, args.repair)
        total, passed = len(stand.get("steps", [])), stand.get("passed", 0)
        row["steps"]["stand"] = f"{passed}/{total}"
        print(f"  стенд {args.repair}: {passed}/{total}")
        for note in feedback_text(stand):
            print(f"    · {note[:170]}")
            row["notes"].append(note)
        fixed = ai_fix_module(kind, args.repair, stand)
        for note in fixed:
            print(f"    ✎ {note}")
            row["notes"].append(note)
        if fixed and not args.no_build:
            bump(kind, args.repair)
            buildwatch.commit_push(REPOS[kind], f"fix({args.repair}): правки по стенду")
        row["status"] = "исправлено" if fixed else f"стенд {passed}/{total}"
        REPORTS.mkdir(exist_ok=True)
        out = Path(args.out) if args.out else REPORTS / f"repair-{args.repair}-{int(time.time())}.json"
        out.write_text(json.dumps([row], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nотчёт: {out}")
        return

    rows = []
    if args.jobs > 1:
        # Пока один сайт ждёт CI, второй уже генерируется; стенд всё равно
        # идёт по одному — устройство одно.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for row in pool.map(lambda item: _safe_process(item, args), urls):
                rows.append(row)
    else:
        for index, url in enumerate(urls, 1):
            print(f"\n[{index}/{len(urls)}]")
            rows.append(_safe_process(url, args))
            if index < len(urls):
                time.sleep(args.pause)

    REPORTS.mkdir(exist_ok=True)
    out_path = Path(args.out) if args.out else REPORTS / f"pipe-{int(time.time())}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== итог")
    for row in rows:
        print(f"  {row.get('status', '?'):22} {row.get('kind', ''):9} {row.get('url', '')}")
    print(f"\nотчёт: {out_path}")
    if args.issue:
        _file_issue(rows, out_path)


if __name__ == "__main__":
    main(sys.argv[1:])
