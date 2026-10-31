import re
import subprocess
from pathlib import Path

ROOT = Path(".")
PATTERNS = {
    "google_api": r"AIza[0-9A-Za-z_\-]{35}",
    "github_pat": r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}",
    "openai": r"sk-[A-Za-z0-9]{20,}",
    "telegram": r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "basic_auth": r"(?:https?|socks5h?)://[^\s:@/]+:[^\s@/]+@",
    "kodik_token": r"(?:kodik[_-]?token|token)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9]{20,}[\"']",
    "gemini_key_file": r"\.gemini_key|GEMINI_API_KEY\s*=\s*[\"'][^\"']{10,}",
    "password": r"(?:password|passwd|secret)\s*[:=]\s*[\"'][^\"'\s]{6,}[\"']",
}


def mask(value):
    return f"{value[:4]}…{len(value)} симв."


def scan_text(text, where):
    hits = []
    for name, pattern in PATTERNS.items():
        for match in re.finditer(pattern, text, re.I):
            hits.append((name, where, mask(match.group(0))))
    return hits


tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
print(f"проверяю файлов: {len(tracked)}")
hits = []
for name in tracked:
    path = ROOT / name
    if not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    hits += scan_text(text, name)

print("\n=== текущие файлы:")
for item in hits:
    print("  ", item)
if not hits:
    print("   чисто")

log = subprocess.run(
    ["git", "log", "-p", "--all", "--no-color"], cwd=ROOT, capture_output=True, text=True
).stdout
history = scan_text(log, "история git")
print("\n=== история коммитов:")
for item in history[:20]:
    print("  ", item)
if not history:
    print("   чисто")

print("\n=== подозрительные файлы в каталоге:")
for path in ROOT.rglob("*"):
    name = str(path.relative_to(ROOT))
    if path.is_file() and re.search(r"(\.env|\.gemini_key|\.pem|\.key|id_rsa|secrets?\.(json|ya?ml))$", name):
        print("  ", name)
