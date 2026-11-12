"""Report fork-only changes on an upstream PR branch.

Run with: uv run --no-project python _bmad-output/scripts/check_pr_branch.py [base]

Reads ``git diff <base>...HEAD`` (base defaults to ``upstream/main``) and reports:
fork-only paths, changes to files upstream's maintainer writes in release commits
(``docs/release-notes-next.md``), added lines carrying a ``+fork`` version segment, and
added lines containing a Windows user path. Exits 1 on any finding, 0 if clean, 2 if git failed.
It sees paths and markers only; it cannot recognise a fork-only behaviour change,
so read each picked commit as well. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_BASE = "upstream/main"
FORK_ONLY_DIRS = (".agents", ".claude")
FORK_ONLY_NAMES = ("CLAUDE.md", "AGENTS.md")
FORK_ONLY_FILES = ("skills-lock.json",)
# Upstream's maintainer writes these in release commits; a PR carries the text in its description.
MAINTAINER_ONLY_FILES = ("docs/release-notes-next.md",)
FORK_MARKER = "+fork"
SEP = "[" + chr(92) * 2 + "/]"
USER_PATH = re.compile(
    "(?i)(?:[a-z]:" + SEP + "+|/[a-z]/|/mnt/[a-z]/)users" + SEP + "+[^" + chr(92) * 2 + "/" + chr(92) + "s]+"
)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class GitError(Exception):
    """Git could not produce the diff; the check did not run."""


DIFF_FLAGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--text",
    "--no-renames",
    "--diff-filter=ACMRT",
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-c", "diff.noprefix=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def is_fork_only(path: str) -> bool:
    parts = path.split("/")
    return (
        path in FORK_ONLY_FILES
        or parts[-1] in FORK_ONLY_NAMES
        or parts[0].startswith("_bmad")
        or (len(parts) > 1 and parts[0] in FORK_ONLY_DIRS)
    )


def check(repo: Path, base: str) -> list[str]:
    spec = f"{base}...HEAD"
    findings: list[str] = []
    paths = git(repo, "diff", *DIFF_FLAGS, "--name-only", spec)
    for path in paths.splitlines():
        if is_fork_only(path):
            findings.append(f"{path}: fork-only path")
        elif path in MAINTAINER_ONLY_FILES:
            findings.append(f"{path}: upstream's maintainer writes this file in release commits")
    current = ""
    lineno = 0
    in_header = False
    for line in git(repo, "diff", *DIFF_FLAGS, "-U0", spec).splitlines():
        if line.startswith("diff "):
            in_header = True
            current = ""
            continue
        if in_header:
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else ""
            elif line.startswith("@@"):
                in_header = False
            if in_header:
                continue
        hunk = HUNK.match(line)
        if hunk:
            lineno = int(hunk.group(1))
            continue
        if not line.startswith("+") or not current:
            continue
        text = line[1:]
        if FORK_MARKER in text:
            findings.append(f"{current}:{lineno}: added line contains {FORK_MARKER}")
        if USER_PATH.search(text):
            findings.append(f"{current}:{lineno}: added line contains a Windows user path")
        lineno += 1
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", nargs="?", default=DEFAULT_BASE, help="base ref")
    parser.add_argument("--repo", default=".", help="repository folder")
    args = parser.parse_args(argv)
    try:
        findings = check(Path(args.repo), args.base)
    except GitError as error:
        print(f"check could not run: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} fork-only finding(s) against {args.base}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
