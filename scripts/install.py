#!/usr/bin/env python3
"""Install or update Sonder's code while preserving the author's memory."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def install(target):
    source = Path(__file__).resolve().parents[1]
    target = Path(target).expanduser().resolve()
    if target == source or source in target.parents:
        raise ValueError("choose an installation directory outside the source repository")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "LICENSE"):
        shutil.copy2(source / name, target / name)
    for name in ("library", "references", "scripts"):
        shutil.copytree(source / name, target / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Publish the new instructions only after their target engine is compatible.
    # This is a capability guard, not a transaction over the whole directory.
    try:
        checked = subprocess.run([sys.executable, '-B', '-I', str(target / 'scripts/check_runtime.py')],
                                 capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        raise ValueError('runtime verification timed out') from error
    if checked.returncode:
        raise ValueError('runtime verification failed: ' + checked.stderr.strip())
    shutil.copy2(source / 'SKILL.md', target / 'SKILL.md')
    memory = target / "memory/style-profile.md"
    memory.parent.mkdir(exist_ok=True)
    # Exclusive creation also preserves a memory file created concurrently.
    try:
        with memory.open("xb") as out:
            out.write((source / "memory/style-profile.md").read_bytes())
    except FileExistsError:
        pass
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path,
                        default=Path.home() / ".claude/skills/sonder")
    args = parser.parse_args()
    try:
        target = install(args.target)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Installation failed: {error}\n")
    print(f"Installed Sonder at {target}; runtime verified; existing author memory preserved.")


if __name__ == "__main__":
    main()
