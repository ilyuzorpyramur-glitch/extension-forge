#!/usr/bin/env python3
"""Мост «аудит → конвейер»: ответы Telegram-бота становятся заданием на правку.

Бот (tg-audit-bot) собирает дефекты по расширению. Здесь они превращаются в
план работ: что именно сломано, видно ли это по коду (probe), какая категория
и что должен сделать генератор. Файл читают forge/buildwatch и человек.

Usage:
  python3 auditfix.py                 — план по всем расширениям с ответами
  python3 auditfix.py --module amedia — только одно
  python3 auditfix.py --json          — машинный вывод
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parent
REPO = Path(os.environ.get("AUDIT_REPO", "/home/work/repos/aniyomi-ru-anime-repo"))
BOT_DB = Path(os.environ.get("AUDIT_DB", os.path.expanduser("~/.tg-audit-bot/state.db")))
BOT_REPORTS = Path(os.path.expanduser("~/.tg-audit-bot/reports"))
OUT = FORGE_ROOT / "reports"

# Что конвейер умеет править сам, а что требует решения по сайту.
AUTO = {
    "lists": "развести адреса популярного и недавнего",
    "scroll": "проверить пагинацию: pager/rel=next, иначе hasNextPage=false",
    "thumbs": "брать постер из карточки, а не из ссылки",
    "cover": "постер из og:image карточки тайтла",
    "status": "парсить статус со страницы тайтла",
    "rating": "парсить оценку и писать в описание",
    "filters": "собрать фильтры из формы сайта (жанр, статус)",
    "quality": "добавить выбор качества",
    "labels": "убрать озвучку из названий качеств",
    "audio": "разложить озвучки по звуковым дорожкам",
    "subs": "отдавать subtitleTracks",
    "order": "сортировать серии по убыванию",
    "epthumbs": "превью серий: если сайт не даёт кадры — не подставлять чужие",
    "eptitles": "брать названия серий из источника",
    "epdate": "дата серии из источника, иначе не выставлять",
    "video": "починить разбор плеера",
    "icon": "перегенерировать иконку",
    "nsfw": "выставить флаг 18+",
}


def answers_by_module() -> dict[str, dict[str, str]]:
    if BOT_REPORTS.joinpath("latest.json").exists():
        payload = json.loads(BOT_REPORTS.joinpath("latest.json").read_text(encoding="utf-8"))
        return {
            pkg: item.get("answers", {})
            for pkg, item in payload.get("extensions", {}).items()
        }
    if not BOT_DB.exists():
        return {}
    db = sqlite3.connect(BOT_DB)
    result: dict[str, dict[str, str]] = {}
    for ext, screen, value in db.execute("SELECT ext, screen, value FROM answers"):
        result.setdefault(ext, {})[screen] = value
    return result


def module_of(pkg: str) -> str:
    return pkg.rsplit(".", 1)[-1]


def probe(module: str) -> dict:
    """Факты из кода — тем же разбором, что и в боте."""
    sys.path.insert(0, str(Path(os.path.expanduser("~/repos/tg-audit-bot"))))
    code = (
        "import probe,json,sys;"
        f"print(json.dumps(probe.analyse('eu.kanade.tachiyomi.animeextension.ru.{module}'),"
        " ensure_ascii=False))"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if run.returncode != 0:
        return {"found": False}
    try:
        return json.loads(run.stdout.strip().splitlines()[-1])
    except Exception:
        return {"found": False}


def plan_for(module: str, answers: dict[str, str], steps) -> dict:
    problems = []
    for key in steps.ORDER:
        if key not in answers:
            continue
        if not steps.visible(key, answers):
            continue
        if answers[key] in steps.GOOD.get(key, set()):
            continue
        screen = steps.BY_KEY[key]
        cat = screen.get("cat", "")
        problems.append({
            "screen": key,
            "title": screen["title"],
            "answer": answers[key],
            "category": cat,
            "where": steps.WHERE.get(key, ""),
            "action": AUTO.get(key, AUTO.get(cat, "решить по сайту")),
        })
    facts = probe(module)
    return {
        "module": module,
        "problems": problems,
        "categories": sorted({item["category"] for item in problems if item["category"]}),
        "facts": {k: v for k, v in facts.items() if k != "files"},
        "lines": facts.get("lines", 0),
    }


def brief(plan: dict) -> str:
    lines = [f"# {plan['module']}", ""]
    if not plan["problems"]:
        lines.append("Дефектов нет.")
        return "\n".join(lines)
    lines.append(f"Дефектов: {len(plan['problems'])} · категории: {', '.join(plan['categories'])}")
    lines.append("")
    lines.append("| Пункт | Ответ | Что делать |")
    lines.append("|---|---|---|")
    for item in plan["problems"]:
        lines.append(f"| {item['title']} | {item['answer']} | {item['action']} |")
    lines.append("")
    if plan["lines"]:
        lines.append(f"Код: {plan['lines']} строк, фильтров {plan['facts'].get('filter_count', 0)}, "
                     f"дорожки: {'да' if plan['facts'].get('audio_tracks') else 'нет'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(os.path.expanduser("~/repos/tg-audit-bot"))))
    import steps  # noqa: E402

    data = answers_by_module()
    if not data:
        print("нет ответов аудита", file=sys.stderr)
        return 1

    plans = []
    for pkg, answers in data.items():
        module = module_of(pkg)
        if args.module and module != args.module:
            continue
        plans.append(plan_for(module, answers, steps))

    if args.json:
        print(json.dumps(plans, ensure_ascii=False, indent=2))
        return 0

    OUT.mkdir(exist_ok=True)
    for plan in plans:
        text = brief(plan)
        (OUT / f"fix-{plan['module']}.md").write_text(text, encoding="utf-8")
        print(text)
        print()
    print(f"планы: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
