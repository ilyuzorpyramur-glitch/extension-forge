"""Сводит прогон стенда в понятную карту ошибок.

Первый сбойный шаг определяет причину, остальные падения ниже по цепочке
(«нет тайтла», «нет деталей», «нет серии») — каскад, а не отдельные баги.

Категории:
  сеть    — SSL, DNS, таймауты, обрыв (IP/блокировка, не код);
  стенд   — NPE корутин, класс не найден, нет метода (ошибка инструмента);
  сайт    — 403/404/5xx, пустой ответ (источник сломался/сменил разметку);
  парсинг — всё остальное (реально не разобрали страницу).
"""
import json
import re
import sys
from datetime import date

CATEGORIES = [
    # Сеть проверяем первой: если suspend-путь сорвался на нашем Continuation,
    # а legacy-путь упал по сети, реальная причина — сеть.
    ("сеть", r"SSLHandshake|SocketTimeout|UnknownHost|ConnectException|connection closed"
             r"|failed to connect|Network is unreachable|Read timed out"),
    ("стенд", r"CoroutineContext|класс не найден|нет метода|нет suspend|много аргументов|ClassNotFound"
              r"|getClass\(\)' on a null"),
    ("сайт", r"HTTP (?:40[0-9]|5[0-9][0-9])|Forbidden|Unauthorized|нет карточек|0 карточек"),
]


def classify(error: str) -> str:
    for name, pattern in CATEGORIES:
        if re.search(pattern, error, re.I):
            return name
    return "парсинг"


def short(error: str, category: str) -> str:
    """Показываем ту часть сообщения, которая объясняет причину.

    Стенд склеивает «suspend-путь | legacy-путь»; если причина сетевая,
    бесполезно показывать NPE от нашего Continuation.
    """
    pattern = dict(CATEGORIES).get(category)
    chunks = []
    for part in re.split(r"\s*\|\|\s*", error):
        chunks.extend(re.split(r"\s\|\s", part))
    if pattern:
        for chunk in chunks:
            if re.search(pattern, chunk, re.I):
                return re.sub(r"\s+", " ", chunk)[:110]
    return re.sub(r"\s+", " ", chunks[0])[:110]


def analyse(path: str):
    rows = json.load(open(path, encoding="utf-8"))
    result = {}
    for row in rows:
        steps = row.get("steps") or []
        module = row.get("module", "?")
        fails = [s for s in steps if not s.get("ok")]
        info = {"passed": row.get("passed", 0), "total": len(steps), "root": None, "cascade": []}
        if fails:
            first = fails[0]
            error = str(first.get("error") or first.get("value") or "")
            info["root"] = {
                "step": first["name"],
                "category": classify(error),
                "error": short(error, classify(error)),
            }
            for step in fails[1:]:
                info["cascade"].append(step["name"])
        result[module] = info
    return result


def main(path: str) -> None:
    data = analyse(path)
    clean = sorted(m for m, v in data.items() if v["root"] is None)
    broken = sorted((m for m, v in data.items() if v["root"]), key=lambda m: data[m]["passed"])

    lines = [f"# Прогон стенда: {date.today().isoformat()}", "",
             f"Всего расширений: {len(data)}. Прошли целиком: {len(clean)}. Со сбоями: {len(broken)}.", ""]

    lines += ["## Прошли целиком", "", (", ".join(clean) if clean else "—"), ""]

    groups = {"парсинг": [], "сайт": [], "сеть": [], "стенд": []}
    for module in broken:
        groups[data[module]["root"]["category"]].append(module)

    titles = {
        "парсинг": "Баг расширения: не разбирается страница",
        "сайт": "Источник отвечает ошибкой",
        "сеть": "Сеть или IP: не код расширения",
        "стенд": "Ошибка инструмента (стенд)",
    }
    for key in ("парсинг", "сайт", "сеть", "стенд"):
        if not groups[key]:
            continue
        lines += [f"## {titles[key]} ({len(groups[key])})", ""]
        for module in groups[key]:
            info = data[module]
            root = info["root"]
            lines.append(f"- **{module}** — {info['passed']}/{info['total']}, шаг `{root['step']}`: {root['error']}")
            if info["cascade"]:
                lines.append(f"  - каскад: {', '.join(info['cascade'])}")
        lines.append("")

    text = "\n".join(lines)
    print(text)
    out = f"/home/work/reports/audit-{date.today().isoformat()}.md"
    import os

    os.makedirs("/home/work/reports", exist_ok=True)
    open(out, "w", encoding="utf-8").write(text)
    print(f"\nсохранено: {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/audit_all.json")
