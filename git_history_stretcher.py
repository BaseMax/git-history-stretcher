#!/usr/bin/env python3

import sys
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path


def run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def git_ok(args: list[str], cwd: str) -> bool:
    return run_git(args, cwd).returncode == 0


def git_output(args: list[str], cwd: str) -> str:
    return run_git(args, cwd).stdout.strip()


def check_path(repo: Path) -> tuple[bool, str]:
    if not repo.exists():
        return False, f"Path does not exist: {repo}"
    if not repo.is_dir():
        return False, f"Path is not a directory: {repo}"
    if not (repo / ".git").exists():
        return False, f"No .git directory found in: {repo}"
    return True, ""


def check_git_validity(repo: Path) -> tuple[bool, str]:
    if not git_ok(["rev-parse", "--git-dir"], str(repo)):
        return False, "Not a valid Git repository."
    result = run_git(["rev-list", "--count", "HEAD"], str(repo))
    if result.returncode != 0:
        return False, "Repository has no commits."
    count = int(result.stdout.strip())
    if count == 0:
        return False, "Repository has no commits."
    return True, str(count)


def check_integrity(repo: Path) -> tuple[bool, str]:
    result = run_git(["fsck", "--no-progress", "--no-dangling"], str(repo))
    if result.returncode != 0:
        return False, f"Git fsck reported errors:\n{result.stderr.strip()}"
    return True, ""


def validate_git_repo(path: str) -> tuple[bool, str]:
    repo = Path(path).resolve()

    ok, msg = check_path(repo)
    if not ok:
        return False, msg

    ok, msg = check_git_validity(repo)
    if not ok:
        return False, msg
    commit_count = msg

    ok, msg = check_integrity(repo)
    if not ok:
        return False, msg

    return True, f"Valid Git repository with {commit_count} commit(s)."


def parse_commit_line(line: str) -> dict | None:
    parts = line.split("\x00")
    if len(parts) < 8:
        return None
    return {
        "hash": parts[0],
        "author_name": parts[1],
        "author_email": parts[2],
        "author_ts": int(parts[3]),
        "committer_name": parts[4],
        "committer_email": parts[5],
        "committer_ts": int(parts[6]),
        "subject": parts[7],
    }


def get_commits(repo: str) -> list[dict]:
    fmt = "%H%x00%an%x00%ae%x00%at%x00%cn%x00%ce%x00%ct%x00%s"
    result = run_git(["log", "--reverse", "--format=" + fmt, "HEAD"], repo)
    if result.returncode != 0:
        raise RuntimeError(f"git log failed: {result.stderr.strip()}")
    return [c for line in result.stdout.splitlines() if (c := parse_commit_line(line))]


def compute_scaled_committer_timestamps(commits: list[dict], factor: float) -> list[int]:
    anchor = min(commits[-1]["committer_ts"], int(datetime.now(timezone.utc).timestamp()))
    orig = [c["committer_ts"] for c in commits]
    scaled_gaps = [max(0, int((orig[i + 1] - orig[i]) * factor)) for i in range(len(orig) - 1)]

    new_ts = [0] * len(commits)
    new_ts[-1] = anchor
    for i in range(len(commits) - 2, -1, -1):
        new_ts[i] = new_ts[i + 1] - scaled_gaps[i]

    min_ts = min(new_ts)
    if min_ts < 0:
        new_ts = [t - min_ts for t in new_ts]

    return new_ts


def apply_new_timestamps(commits: list[dict], new_committer_ts: list[int]) -> list[dict]:
    result = []
    for i, commit in enumerate(commits):
        author_offset = commit["author_ts"] - commit["committer_ts"]
        c = dict(commit)
        c["committer_ts"] = new_committer_ts[i]
        c["author_ts"] = new_committer_ts[i] + author_offset
        result.append(c)
    return result


def scale_timestamps(commits: list[dict], factor: float) -> list[dict]:
    if len(commits) <= 1:
        return commits
    new_committer_ts = compute_scaled_committer_timestamps(commits, factor)
    return apply_new_timestamps(commits, new_committer_ts)


def ts_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def print_dry_run(commits: list[dict]) -> None:
    print("\n[DRY RUN] Would rewrite the following commits:\n")
    for c in commits:
        print(
            f"  {c['hash'][:10]}  author={ts_to_iso(c['author_ts'])}  "
            f"commit={ts_to_iso(c['committer_ts'])}  {c['subject'][:60]}"
        )


def build_filter_script(commits: list[dict]) -> str:
    lines = ["#!/bin/sh"]
    for c in commits:
        lines.append(
            f'[ "$GIT_COMMIT" = "{c["hash"]}" ] && '
            f'export GIT_AUTHOR_DATE="{ts_to_iso(c["author_ts"])}" '
            f'GIT_COMMITTER_DATE="{ts_to_iso(c["committer_ts"])}"'
        )
    return "\n".join(lines) + "\n"


def to_git_bash_path(path: Path) -> str:
    posix = path.as_posix()
    if len(posix) >= 2 and posix[1] == ':':
        posix = '/' + posix[0].lower() + posix[2:]
    return posix


def run_filter_branch(repo: str, script_path: Path) -> None:
    result = run_git(
        ["filter-branch", "-f", "--env-filter", to_git_bash_path(script_path), "--", "--all"],
        repo,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git filter-branch failed:\n{result.stderr.strip()}")


def rewrite_history(repo: str, commits: list[dict], dry_run: bool = False) -> None:
    if dry_run:
        print_dry_run(commits)
        return

    script_path = Path(repo) / ".git" / "_stretch_filter.sh"
    script_path.write_text(build_filter_script(commits))
    script_path.chmod(0o755)

    try:
        print("\nRewriting history with git filter-branch …")
        run_filter_branch(repo, script_path)
        print("Done. History rewritten successfully.")
        print("\nIMPORTANT: If this repo has been pushed, run:\n  git push --force\n")
    finally:
        script_path.unlink(missing_ok=True)


def span_str(commits: list[dict], key: str) -> str:
    first = datetime.fromtimestamp(commits[0][key], tz=timezone.utc).date()
    last = datetime.fromtimestamp(commits[-1][key], tz=timezone.utc).date()
    return f"{first}  →  {last}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stretch or compress a Git commit timeline by scaling inter-commit intervals."
    )
    parser.add_argument("repo", help="Path to the Git repository.")
    parser.add_argument("--factor", type=float, default=2.0, help="Scaling factor (default: 2.0).")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying the repo.")
    parser.add_argument("--validate-only", action="store_true", help="Only validate the repository and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.factor <= 0:
        print("Error: --factor must be a positive number.", file=sys.stderr)
        sys.exit(1)

    print(f"Validating repository: {args.repo}")
    ok, message = validate_git_repo(args.repo)
    print(f"  {'OK' if ok else 'FAIL'}: {message}")

    if not ok:
        sys.exit(1)
    if args.validate_only:
        sys.exit(0)

    print("\nLoading commit history …")
    commits = get_commits(args.repo)
    print(f"  {len(commits)} commit(s) loaded.")

    if len(commits) < 2:
        print("Nothing to rewrite: fewer than 2 commits.")
        sys.exit(0)

    print(f"\nScaling inter-commit intervals by factor {args.factor} …")
    new_commits = scale_timestamps(commits, args.factor)
    print(f"  Original span: {span_str(commits, 'committer_ts')}")
    print(f"  New span:      {span_str(new_commits, 'committer_ts')}")

    rewrite_history(args.repo, new_commits, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
