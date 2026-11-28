"""Changed paths relative to the scheduler project, including paths containing whitespace."""
import os
import subprocess
from pathlib import Path


def changed_files(root: Path, base: str | None) -> list[str]:
    revision = f"{base}...HEAD" if base else "HEAD"
    result = subprocess.run(
        ["git", "diff", "--relative", "--name-only", "-z", revision, "--", "."],
        cwd=root, capture_output=True, check=False,
    )
    if result.returncode:
        raise SystemExit(f"git diff failed: {os.fsdecode(result.stderr).strip()}")
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
