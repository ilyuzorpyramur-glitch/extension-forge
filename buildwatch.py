#!/usr/bin/env python3
"""Наблюдатель за сборкой: дождаться CI, разобрать ошибки, починить, перезапустить."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import gemini

IDENTITY = ("-c", "user.name=Ilyuzor")
EMAIL = ("-c", "user.email=346533302+ilyuzorpyramur-glitch@users.noreply.github.com")


def sh(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    return (result.stdout or "") + (result.stderr or "")


def slug(repo: Path) -> str:
    """owner/name из remote origin."""
    url = sh(["git", "-C", str(repo), "remote", "get-url", "origin"]).strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    return match.group(1) if match else ""


def commit_push(repo: Path, message: str, paths: list[str] | None = None) -> bool:
    """Коммит и пуш. True, если что-то уехало."""
    if paths:
        for path in paths:
            sh(["git", "-C", str(repo), "add", "-A", path])
    else:
        sh(["git", "-C", str(repo), "add", "-A"])
    if "nothing to commit" in sh(["git", "-C", str(repo), "status", "--porcelain"]):
        return False
    if not sh(["git", "-C", str(repo), "status", "--porcelain"]).strip():
        return False
    sh(["git", "-C", str(repo), *IDENTITY, *EMAIL, "commit", "-q", "-m", message])
    sh(["git", "-C", str(repo), "fetch", "-q", "origin"])
    sh(["git", "-C", str(repo), "pull", "--rebase", "-q", "origin", "main"])
    out = sh(["git", "-C", str(repo), "push", "-q", "origin", "main"])
    return "error" not in out.lower() and "rejected" not in out.lower()


def latest_run(repo_slug: str) -> dict:
    out = sh(["gh", "api", f"repos/{repo_slug}/actions/runs?per_page=1"])
    try:
        runs = json.loads(out).get("workflow_runs") or []
    except ValueError:
        return {}
    return runs[0] if runs else {}


def wait_run(repo_slug: str, run_id: int = 0, timeout: int = 1500, poll: int = 30,
             since_id: int = 0) -> dict:
    """Ждёт завершения сборки. Возвращает последний известный run.

    since_id — номер прошлой сборки: без него wait_run возвращал уже
    завершённый run, и конвейер шёл к стенду до того, как CI собрал APK.
    """
    deadline = time.time() + timeout
    run = {}
    while time.time() < deadline:
        if not run_id:
            run = latest_run(repo_slug)
            if not run:
                return {}
            fresh = run.get("id") or 0
            if since_id and fresh == since_id:
                time.sleep(10)
                continue
            run_id = fresh
            # Дать GitHub время создать run для только что запушенного коммита.
            time.sleep(8)
            continue
        run = json.loads(sh(["gh", "api", f"repos/{repo_slug}/actions/runs/{run_id}"]) or "{}")
        if run.get("status") == "completed":
            return run
        time.sleep(poll)
    return run


def failed_log(repo_slug: str, run_id: int) -> str:
    return sh(["gh", "run", "view", str(run_id), "-R", repo_slug, "--log-failed"])


ERROR_RE = re.compile(r"([\w./-]+\.kt):(\d+):(\d+)?:?\s*(?:error:\s*)?(.+)")
# spotless/ktlint печатает иначе: "Файл.kt:L116 ktlint(ktlint) Expecting ..."
KTlint_RE = re.compile(r"([\w./-]+\.kt):L(\d+)\s+(?:ktlint\([^)]*\)\s*)?(.+)")


def parse_errors(log: str) -> list[dict]:
    """Kotlin-ошибки и замечания ktlint из лога сборки."""
    found = []
    for line in log.splitlines():
        ktlint = KTlint_RE.search(line)
        if ktlint:
            path_name, line_no, message = ktlint.groups()
            found.append({"file": path_name, "line": int(line_no), "message": message.strip()[:300]})
            continue
        match = ERROR_RE.search(line)
        if not match:
            continue
        path, line_no, _col, message = match.groups()
        message = message.strip()
        if not message or message.startswith("warning:"):
            continue
        soft = re.search(r"should be|exceeds maximum|Unexpected |expected ", message)
        if "error" not in line.lower() and "violation" not in message.lower() and not soft:
            continue
        found.append({"file": path, "line": int(line_no), "message": message[:300]})
    # одинаковые сообщения по одному файлу не дублируем
    unique, seen = [], set()
    for item in found:
        key = (item["file"], item["line"], item["message"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _docs(module_dir: Path) -> list[Path]:
    return sorted(module_dir.rglob("*.kt"))


def fix_mechanical(module_dir: Path, errors: list[dict]) -> list[str]:
    """Правки, которые не требуют понимания кода: пробелы, табы, отступы, длина."""
    done = []
    for path in _docs(module_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        original = text
        text = text.replace("\t", "    ")
        text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
        while "\n\n\n\n" in text:
            text = text.replace("\n\n\n\n", "\n\n\n")
        if text != original:
            path.write_text(text, encoding="utf-8")
            done.append(f"{path.name}: пробелы/табы")

    for item in errors:
        message = item["message"]
        indent = re.search(r"Unexpected indentation \((\d+)\) \(should be (\d+)\)", message)
        if not indent:
            continue
        want = int(indent.group(2))
        path = module_dir / Path(item["file"]).name if not Path(item["file"]).exists() else Path(item["file"])
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        index = item["line"] - 1
        if 0 <= index < len(lines):
            lines[index] = " " * want + lines[index].lstrip()
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            done.append(f"{path.name}:{item['line']} отступ → {want}")
    return done


SPOTLESS_MARK = "The following files had format violations:"


def apply_spotless_diff(log: str, module_dir: Path) -> list[str]:
    """Применяет дифф, который печатает spotless.

    Это самый частый отказ сборки: лишний импорт, отступ, длина строки.
    spotless сам показывает нужные правки — берём их как есть, без модели.
    """
    if SPOTLESS_MARK not in log:
        return []

    lines = log.splitlines()
    start = next(index for index, line in enumerate(lines) if SPOTLESS_MARK in line)
    fixed: list[str] = []
    current: Path | None = None
    hunk_old: list[str] = []
    hunk_new: list[str] = []

    def flush() -> None:
        nonlocal hunk_old, hunk_new
        if current is None or not hunk_old:
            hunk_old, hunk_new = [], []
            return
        try:
            text = current.read_text(encoding="utf-8")
        except OSError:
            hunk_old, hunk_new = [], []
            return
        old_block = "\n".join(hunk_old)
        new_block = "\n".join(hunk_new)
        if old_block and old_block in text:
            current.write_text(text.replace(old_block, new_block, 1), encoding="utf-8")
            fixed.append(f"{current.name}: формат по spotless")
        hunk_old, hunk_new = [], []

    for raw in lines[start + 1:]:
        line = raw.replace("\u00b7", " ")
        # Формат лога GitHub: "job<TAB>step<TAB>2026-10-31T13:20:12.123Z текст".
        if "\t" in line:
            line = line.rsplit("\t", 1)[-1]
        line = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?", "", line)
        if line.strip().startswith("BUILD ") or line.startswith("##["):
            break
        if line.startswith("      ") and line.strip().endswith(".kt"):
            flush()
            name = line.strip()
            current = module_dir / Path(name).name
            if not current.exists():
                matches = list(module_dir.rglob(Path(name).name))
                current = matches[0] if matches else None
            continue
        if line.strip().startswith("@@"):
            flush()
            continue
        if current is None:
            continue
        # spotless печатает дифф с отступом: маркер стоит на 10-й колонке.
        marker_at = line.find("@@")
        marker_at = 10 if marker_at < 0 or marker_at > 12 else marker_at
        if len(line) <= marker_at or line[:marker_at].strip():
            continue
        marker, body = line[marker_at], line[marker_at + 1:]
        if marker == "-":
            hunk_old.append(body)
        elif marker == "+":
            hunk_new.append(body)
        elif marker == " ":
            hunk_old.append(body)
            hunk_new.append(body)
    flush()
    return fixed


AI_PROMPT = """Ты чинишь Kotlin-расширение для Aniyomi. Сборка упала.

Ошибки:
{errors}

Файл {file}:
```kotlin
{code}
```

Верни JSON: {{"patches":[{{"file":"имя.kt","search":"точный фрагмент","replace":"замена"}}]}}
Только минимальные правки, без переписывания файла целиком."""


def fix_with_ai(module_dir: Path, errors: list[dict]) -> list[str]:
    if not gemini.available() or not errors:
        return []
    target = module_dir / Path(errors[0]["file"]).name
    if not target.exists():
        candidates = _docs(module_dir)
        if not candidates:
            return []
        target = candidates[0]
    code = target.read_text(encoding="utf-8")
    prompt = AI_PROMPT.format(
        errors="\n".join(f"- {e['file']}:{e['line']}: {e['message']}" for e in errors[:6]),
        file=target.name,
        code=code[:6000],
    )
    answer = gemini.ask_json(prompt, max_tokens=2000)
    applied = []
    for patch in answer.get("patches", [])[:5]:
        name = patch.get("file", "")
        search = patch.get("search", "")
        replace = patch.get("replace", "")
        if not search:
            continue
        path = module_dir / Path(name).name if name else target
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if search not in text:
            continue
        path.write_text(text.replace(search, replace, 1), encoding="utf-8")
        applied.append(f"{path.name}: правка ИИ")
    return applied
