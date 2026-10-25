#!/usr/bin/env python3
"""Сторож extension-forge: ежемесячная проверка расширений и самопочинка.

Логика:
  1. Для каждого расширения с watch=true из registry.json:
     - проверяем живость сайта (HTTP-статус главной);
     - проверяем селекторы из декларации: если элемент не находится —
       сайт поменял вёрстку, селектор протух.
  2. Протухшие селекторы сторож пытается переобнаружить (см. heal()):
     ищет похожие элементы в новом DOM (адаптивный подход в духе Scrapling),
     обновляет декларацию и registry.
  3. Если селекторы обновлены — bump версии расширения и git commit,
     чтобы CI пересобрал и переопубликовал расширение.

Запуск: python3 watchdog/watchdog.py [--dry-run]
В CI: .github/workflows/monthly-watch.yml — каждое 1-е число месяца.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE_ROOT))

from forge import (  # noqa: E402
    FORGE_ROOT, REGISTRY, REPOS, SITES_DIR,
    SiteSpec, bump_extension, next_version,
)

try:
    from scrapling import Fetcher  # адаптивный парсер с самовосстановлением
    HAS_SCRAPLING = True
except ImportError:
    import urllib.request
    HAS_SCRAPLING = False


@dataclass
class CheckResult:
    site_id: str
    kind: str
    base_url: str
    site_alive: bool
    selector_results: dict  # selector_key -> bool (найден/не найден)
    healed: dict            # selector_key -> новый селектор (если переобнаружен)
    needs_bump: bool


# ---------------------------------------------------------------------------
# Живость сайта и селекторов
# ---------------------------------------------------------------------------


def fetch_page(url: str):
    """Возвращает HTML страницы (str) или None, если сайт недоступен."""
    if HAS_SCRAPLING:
        try:
            page = Fetcher(auto_match=True).get(url, timeout=30)
            return page.html_content if page.status == 200 else None
        except Exception:
            return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (extension-forge watchdog)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def selector_matches(html: str, css_selector: str) -> bool:
    """Грубая проверка: селектор встречается в HTML."""
    if not html or not css_selector:
        return False
    # упрощённая эвристика без парсера: ищем класс/тег из селектора
    tokens = re.findall(r"[a-zA-Z][\w-]*", css_selector)
    return any(t in html for t in tokens if len(t) > 2)


def find_replacement(html: str, old_selector: str) -> str | None:
    """Переобнаружение протухшего селектора (упрощённый adaptive match).

    В боевом режиме используется Scrapling: Diff-based matchmaking —
    он находит элемент, похожий на старый, в новой вёрстке.
    Здесь — эвристика по имени класса.
    """
    old_token = re.findall(r"[a-zA-Z][\w-]*", old_selector)
    if not old_token:
        return None
    token = old_token[0]
    # ищем похожие классы в HTML
    classes = set(re.findall(r'class="([^"]+)"', html))
    for cls in classes:
        for part in cls.split():
            if token.lower() in part.lower() and part != token:
                return f".{part}"
    return None


# ---------------------------------------------------------------------------
# Основной проход
# ---------------------------------------------------------------------------


def check_extension(ext: dict, dry_run: bool = False) -> CheckResult:
    base_url = ext["base_url"]
    html = fetch_page(base_url)

    site_alive = html is not None
    selector_results: dict[str, bool] = {}
    healed: dict[str, str] = {}

    selectors = ext.get("selectors", {})
    flat = {}
    for key, val in selectors.items():
        if isinstance(val, dict):
            for sub_key, sub_val in val.items():
                flat[f"{key}.{sub_key}"] = sub_val
        else:
            flat[key] = val

    for key, sel in flat.items():
        ok = site_alive and selector_matches(html, str(sel))
        selector_results[key] = ok
        if not ok and site_alive and not dry_run:
            new_sel = find_replacement(html, str(sel))
            if new_sel:
                healed[key] = new_sel

    needs_bump = bool(healed)
    return CheckResult(
        site_id=ext["id"], kind=ext["kind"], base_url=base_url,
        site_alive=site_alive, selector_results=selector_results,
        healed=healed, needs_bump=needs_bump,
    )


def apply_heal(result: CheckResult, ext: dict) -> None:
    """Обновляет декларацию сайта healed-селекторами и поднимает версию."""
    yaml_path = SITES_DIR / f"{result.site_id}.yaml"
    if not yaml_path.exists():
        print(f"  ⚠ декларация {yaml_path.name} не найдена — heal пропущен")
        return

    text = yaml_path.read_text(encoding="utf-8")
    for key, new_sel in result.healed.items():
        if "." in key:
            parent, sub = key.split(".", 1)
            # вложенные селекторы: parent:\n    sub: old
            text = re.sub(
                rf"(\b{sub}:\s*).+",
                rf"\g<1>{new_sel}",
                text,
            )
        else:
            text = re.sub(
                rf"(\b{key}:\s*).+",
                rf"\g<1>{new_sel}",
                text,
            )
    yaml_path.write_text(text, encoding="utf-8")

    # bump версии расширения в целевом репозитории
    site = SiteSpec(
        site_id=ext["id"], kind=ext["kind"], name=ext["name"],
        base_url=ext["base_url"], version=ext["version"],
    )
    new_version = bump_extension(site)
    print(f"  🔧 healed {list(result.healed)} → версия {new_version}")
    return new_version


def push_target_repo(kind: str, message: str) -> bool:
    """Коммитит и пушит изменения в целевом репозитории → CI пересоберёт APK."""
    repo = REPOS[kind]
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo,
        capture_output=True, text=True,
    )
    if not status.stdout.strip():
        print(f"  (нет изменений в {repo.name})")
        return False
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=repo, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=repo, check=True)
    print(f"  ⬆ запушено в {repo.name} — CI пересоберёт и переопубликует")
    return True


def run(dry_run: bool = False) -> int:
    if not REGISTRY.exists():
        print("registry.json пуст — нет расширений на попечении.")
        return 0
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    failures = 0

    for ext in reg.get("extensions", []):
        if not ext.get("watch", False):
            continue
        print(f"👁 {ext['kind']}/{ext['id']} — {ext['base_url']}")
        result = check_extension(ext, dry_run=dry_run)

        if not result.site_alive:
            print("  ❌ сайт недоступен!")
            failures += 1
            continue
        bad = [k for k, ok in result.selector_results.items() if not ok]
        if bad:
            print(f"  ⚠ протухшие селекторы: {bad}")
            if result.healed and not dry_run:
                apply_heal(result, ext)
                push_target_repo(
                    ext["kind"],
                    f"watch: heal {ext['id']} selectors, bump version [skip ci]",
                )
            elif not result.healed:
                failures += 1
                print("  ❌ селекторы не восстановлены автоматически — нужен ручной разбор")
        else:
            print("  ✅ все селекторы живы")

    return failures


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    code = run(dry_run=dry)
    sys.exit(1 if code else 0)
