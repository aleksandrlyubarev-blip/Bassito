import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    completed = subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode == 0:
        print("Configured git hooks path to .githooks")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
