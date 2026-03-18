import json
import pathlib
import re
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
API_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"xai-[A-Za-z0-9\-_]{12,}"),
]


def run_optional(command: list[str], cwd: pathlib.Path) -> int:
    try:
        completed = subprocess.run(command, cwd=cwd, check=False)
    except FileNotFoundError:
        return 0
    return completed.returncode


def collect_git_diff() -> str:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def ensure_no_test_keys() -> list[str]:
    diff = collect_git_diff()
    hits: list[str] = []
    for pattern in API_KEY_PATTERNS:
        for match in pattern.finditer(diff):
            hits.append(match.group(0))
    return hits


def lint_frontend_roots() -> int:
    exit_code = 0
    for package_file in ROOT.rglob("package.json"):
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        scripts = package.get("scripts", {})
        if "lint" not in scripts:
            continue
        package_manager = "npm.cmd" if shutil.which("npm.cmd") else "npm"
        exit_code = max(exit_code, run_optional([package_manager, "run", "lint"], package_file.parent))
    return exit_code


def check_rust() -> int:
    exit_code = 0
    for cargo_file in ROOT.rglob("Cargo.toml"):
        cargo = "cargo.exe" if shutil.which("cargo.exe") else "cargo"
        exit_code = max(exit_code, run_optional([cargo, "check", "--quiet"], cargo_file.parent))
    return exit_code


def main() -> int:
    leaked = ensure_no_test_keys()
    if leaked:
        print("Blocked commit: staged diff contains API-key-shaped secrets.", file=sys.stderr)
        for secret in leaked:
            preview = f"{secret[:6]}...{secret[-4:]}" if len(secret) > 12 else secret
            print(f"  - {preview}", file=sys.stderr)
        return 1

    rust_code = check_rust()
    lint_code = lint_frontend_roots()
    if rust_code or lint_code:
        print("Blocked commit: pre-commit checks failed.", file=sys.stderr)
        return 1

    print("Pre-commit guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
