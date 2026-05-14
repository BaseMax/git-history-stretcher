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


def compute_scaled_committer_timestamps_forward(commits: list[dict], factor: float) -> list[int]:
    """Anchor at the FIRST commit; scale gaps forward (oldest-first order)."""
    anchor = commits[0]["committer_ts"]
    orig = [c["committer_ts"] for c in commits]
    scaled_gaps = [max(0, int((orig[i + 1] - orig[i]) * factor)) for i in range(len(orig) - 1)]

    new_ts = [0] * len(commits)
    new_ts[0] = anchor
    for i in range(1, len(commits)):
        new_ts[i] = new_ts[i - 1] + scaled_gaps[i - 1]

    return new_ts


def shift_commits(commits: list[dict], delta: int) -> list[dict]:
    """Shift every commit by delta seconds, preserving the author/committer offset."""
    result = []
    for c in commits:
        nc = dict(c)
        author_offset = c["author_ts"] - c["committer_ts"]
        nc["committer_ts"] = c["committer_ts"] + delta
        nc["author_ts"] = nc["committer_ts"] + author_offset
        result.append(nc)
    return result


def scale_timestamps_last_n(all_commits: list[dict], n: int, factor: float) -> tuple[list[dict], int]:
    """Scale only the last N commits (anchored at the last commit, gaps expand backwards).

    If the scaled window's first commit moves earlier than the preceding commit,
    all preceding commits are shifted backwards by the same delta so chronological
    order is preserved.  Their inter-commit gaps are left untouched.

    Returns (new_all_commits, shifted_count) where shifted_count is the number of
    commits outside the window that were time-shifted.
    """
    if n >= len(all_commits):
        return scale_timestamps(all_commits, factor), 0

    window = all_commits[-n:]
    rest = all_commits[:-n]

    new_committer_ts = compute_scaled_committer_timestamps(window, factor)
    new_window = apply_new_timestamps(window, new_committer_ts)

    new_window_first_ts = new_window[0]["committer_ts"]
    rest_last_ts = rest[-1]["committer_ts"]

    if new_window_first_ts < rest_last_ts:
        delta = new_window_first_ts - rest_last_ts  # negative → shift earlier
        new_rest = shift_commits(rest, delta)
        shifted = len(rest)
    else:
        new_rest = list(rest)
        shifted = 0

    return new_rest + new_window, shifted


def scale_timestamps_first_n(all_commits: list[dict], n: int, factor: float) -> tuple[list[dict], int]:
    """Scale only the first N commits (anchored at the first commit, gaps expand forwards).

    If the scaled window's last commit moves later than the following commit,
    all following commits are shifted forwards by the same delta so chronological
    order is preserved.  Their inter-commit gaps are left untouched.

    Returns (new_all_commits, shifted_count) where shifted_count is the number of
    commits outside the window that were time-shifted.
    """
    if n >= len(all_commits):
        new_committer_ts = compute_scaled_committer_timestamps_forward(all_commits, factor)
        return apply_new_timestamps(all_commits, new_committer_ts), 0

    window = all_commits[:n]
    rest = all_commits[n:]

    new_committer_ts = compute_scaled_committer_timestamps_forward(window, factor)
    new_window = apply_new_timestamps(window, new_committer_ts)

    new_window_last_ts = new_window[-1]["committer_ts"]
    rest_first_ts = rest[0]["committer_ts"]

    if new_window_last_ts > rest_first_ts:
        delta = new_window_last_ts - rest_first_ts  # positive → shift later
        new_rest = shift_commits(rest, delta)
        shifted = len(rest)
    else:
        new_rest = list(rest)
        shifted = 0

    return new_window + new_rest, shifted


def ts_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def print_dry_run(commits: list[dict]) -> None:
    print("\n[DRY RUN] Would rewrite the following commits:\n")
    for c in commits:
        print(
            f"  {c['hash'][:10]}  author={ts_to_iso(c['author_ts'])}  "
            f"commit={ts_to_iso(c['committer_ts'])}  {c['subject'][:60]}"
        )


def build_env_filter(commits: list[dict]) -> str:
    parts = []
    for c in commits:
        parts.append(
            f'[ "$GIT_COMMIT" = "{c["hash"]}" ] && '
            f'export GIT_AUTHOR_DATE="{ts_to_iso(c["author_ts"])}" '
            f'GIT_COMMITTER_DATE="{ts_to_iso(c["committer_ts"])}" || :'
        )
    return "\n".join(parts)


def run_filter_branch(repo: str, env_filter: str) -> None:
    result = run_git(
        ["filter-branch", "-f", "--env-filter", env_filter, "--", "--all"],
        repo,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git filter-branch failed:\n{result.stderr.strip()}")


def rewrite_history(repo: str, commits: list[dict], dry_run: bool = False) -> None:
    if dry_run:
        print_dry_run(commits)
        return

    print("\nRewriting history with git filter-branch …")
    run_filter_branch(repo, build_env_filter(commits))
    print("Done. History rewritten successfully.")
    print("\nIMPORTANT: If this repo has been pushed, run:\n  git push --force\n")


def span_str(commits: list[dict], key: str) -> str:
    first = datetime.fromtimestamp(commits[0][key], tz=timezone.utc).date()
    last = datetime.fromtimestamp(commits[-1][key], tz=timezone.utc).date()
    return f"{first}  →  {last}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stretch or compress a Git commit timeline by scaling inter-commit intervals.\n\n"
            "By default all commits are processed.  Use --last or --first to restrict\n"
            "the scaling to a specific number of commits at the end or the beginning of\n"
            "history.  Commits outside the selected window are never re-scaled; they are\n"
            "only shifted in time when necessary to preserve chronological order."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo", help="Path to the Git repository.")
    parser.add_argument(
        "--factor", type=float, default=2.0,
        help="Scaling factor applied to every inter-commit gap (default: 2.0).",
    )
    parser.add_argument(
        "--last", type=int, default=None, metavar="N",
        help=(
            "Restrict scaling to the last N commits only.  "
            "Commits before the window are shifted earlier if needed."
        ),
    )
    parser.add_argument(
        "--first", type=int, default=None, metavar="N",
        help=(
            "Restrict scaling to the first N commits only.  "
            "Commits after the window are shifted later if needed."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview new timestamps without modifying the repository.",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Only run repository health checks and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.factor <= 0:
        print("Error: --factor must be a positive number.", file=sys.stderr)
        sys.exit(1)

    if args.first is not None and args.last is not None:
        print("Error: --first and --last cannot be used together.", file=sys.stderr)
        sys.exit(1)

    if args.first is not None and args.first < 1:
        print("Error: --first must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    if args.last is not None and args.last < 1:
        print("Error: --last must be a positive integer.", file=sys.stderr)
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

    shifted = 0
    if args.last is not None:
        n = min(args.last, len(commits))
        print(f"\nScaling last {n} commit(s) by factor {args.factor} …")
        new_commits, shifted = scale_timestamps_last_n(commits, n, args.factor)
    elif args.first is not None:
        n = min(args.first, len(commits))
        print(f"\nScaling first {n} commit(s) by factor {args.factor} …")
        new_commits, shifted = scale_timestamps_first_n(commits, n, args.factor)
    else:
        print(f"\nScaling inter-commit intervals by factor {args.factor} …")
        new_commits = scale_timestamps(commits, args.factor)

    print(f"  Original span: {span_str(commits, 'committer_ts')}")
    print(f"  New span:      {span_str(new_commits, 'committer_ts')}")
    if shifted:
        print(f"  {shifted} commit(s) outside the window were time-shifted to preserve order.")

    rewrite_history(args.repo, new_commits, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
