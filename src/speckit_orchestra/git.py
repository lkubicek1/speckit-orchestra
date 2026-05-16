from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result


def is_repo(cwd: Path) -> bool:
    return git(["rev-parse", "--is-inside-work-tree"], cwd, check=False).returncode == 0


def head(cwd: Path) -> str | None:
    result = git(["rev-parse", "HEAD"], cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def status_porcelain(cwd: Path) -> str:
    return git(["status", "--porcelain=v1", "--untracked-files=all"], cwd).stdout


def is_clean(cwd: Path) -> bool:
    return status_porcelain(cwd).strip() == ""


def has_conflicts(cwd: Path) -> bool:
    for line in status_porcelain(cwd).splitlines():
        if not line:
            continue
        code = line[:2]
        if "U" in code or code in {"AA", "DD"}:
            return True
    return False


def changed_files(cwd: Path) -> list[str]:
    files: list[str] = []
    for line in status_porcelain(cwd).splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return sorted(set(files))


def diff_patch(cwd: Path, files: list[str] | None = None) -> str:
    if files is None:
        result = git(["diff", "--binary", "HEAD"], cwd, check=False)
        patch = result.stdout if result.returncode == 0 else git(["diff", "--binary"], cwd, check=False).stdout
        selected = None
    else:
        selected = sorted(set(files))
        if not selected:
            return ""
        result = git(["diff", "--binary", "HEAD", "--", *selected], cwd, check=False)
        patch = result.stdout if result.returncode == 0 else ""

    untracked = [line[3:] for line in status_porcelain(cwd).splitlines() if line.startswith("?? ")]
    if selected is not None:
        untracked = [path for path in untracked if path in selected]
    for path in untracked:
        file_path = cwd / path
        if not file_path.is_file():
            continue
        untracked_diff = git(["diff", "--no-index", "--binary", "--", "/dev/null", path], cwd, check=False)
        if untracked_diff.stdout:
            patch += ("\n" if patch and not patch.endswith("\n") else "") + untracked_diff.stdout
    return patch


def commit_changes(cwd: Path, files: list[str], message: str) -> str:
    if not files:
        raise GitError("no files to commit")
    git(["add", "--", *files], cwd)
    result = git(["commit", "-m", message], cwd, check=False)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip() or "git commit failed")
    commit = git(["rev-parse", "--short", "HEAD"], cwd).stdout.strip()
    return commit
