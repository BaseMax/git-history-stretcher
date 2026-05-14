#!/usr/bin/env python3
"""
git-history-stretcher
Rewrite Git commit timestamps by scaling intervals between commits.
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path


def run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_git_repo(path: str) -> tuple[bool, str]:
    """
    Fully validate that *path* is a healthy, usable Git repository.
    Returns (ok: bool, message: str).
    """
    repo = Path(path).resolve()

    if not repo.exists():
        return False, f"Path does not exist: {repo}"

    if not repo.is_dir():
        return False, f"Path is not a directory: {repo}"

    # Check that .git exists (works for normal repos; bare repos are skipped)
    git_dir = repo / ".git"
    if not git_dir.exists():
        return False, f"No .git directory found in: {repo}"

    # git rev-parse --git-dir  — verifies Git considers this a valid repo
    result = run_git(["rev-parse", "--git-dir"], str(repo))
    if result.returncode != 0:
        return False, f"Not a valid Git repository: {result.stderr.strip()}"

    # Check that the repo has at least one commit
    result = run_git(["rev-list", "--count", "HEAD"], str(repo))
    if result.returncode != 0:
        return False, "Repository has no commits (empty repo)."

    commit_count = int(result.stdout.strip())
    if commit_count == 0:
        return False, "Repository has no commits."

    # Verify object store integrity (fast check)
    result = run_git(["fsck", "--no-progress", "--no-dangling"], str(repo))
    if result.returncode != 0:
        return False, f"Git fsck reported errors:\n{result.stderr.strip()}"

    return True, f"Valid Git repository with {commit_count} commit(s)."


# ---------------------------------------------------------------------------
# Commit history helpers
# ---------------------------------------------------------------------------

def get_commits(repo: str) -> list[dict]:
    """
    Return all commits on HEAD in oldest-first order.
    Each item: {hash, author_name, author_email, author_ts,
                committer_name, committer_email, committer_ts, subject}
    Timestamps are Unix epoch integers.
    """
    fmt = "%H%x00%an%x00%ae%x00%at%x00%cn%x00%ce%x00%ct%x00%s"
    result = run_git(
        ["log", "--reverse", "--format=" + fmt, "HEAD"],
        repo,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git log failed: {result.stderr.strip()}")

    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) < 8:
            continue
        commits.append(
            {
                "hash": parts[0],
                "author_name": parts[1],
                "author_email": parts[2],
                "author_ts": int(parts[3]),
                "committer_name": parts[4],
                "committer_email": parts[5],
                "committer_ts": int(parts[6]),
                "subject": parts[7],
            }
        )
    return commits


# ---------------------------------------------------------------------------
# Timestamp scaling
# ---------------------------------------------------------------------------

def scale_timestamps(commits: list[dict], factor: float) -> list[dict]:
    """
    Scale inter-commit intervals by *factor*, anchored so that the last
    commit keeps its original timestamp (≤ now).

    Algorithm:
      1. Compute gaps between consecutive committer timestamps.
      2. Multiply every gap by factor.
      3. Rebuild absolute timestamps from the last commit backwards.
      4. Mirror the author offset relative to committer for each commit.
      5. Clamp: if the first computed timestamp would be negative, shift
         the whole series forward so it starts at epoch 0.
    """
    if len(commits) <= 1:
        return commits

    now_ts = int(datetime.now(timezone.utc).timestamp())
    last_committer_ts = commits[-1]["committer_ts"]

    # If the last commit is already in the future, clamp it to now
    anchor = min(last_committer_ts, now_ts)

    # Gaps (oldest→newest), length = n-1
    orig_committer = [c["committer_ts"] for c in commits]
    gaps = [orig_committer[i + 1] - orig_committer[i] for i in range(len(commits) - 1)]
    scaled_gaps = [max(0, int(g * factor)) for g in gaps]

    # Rebuild from anchor backwards
    new_committer = [0] * len(commits)
    new_committer[-1] = anchor
    for i in range(len(commits) - 2, -1, -1):
        new_committer[i] = new_committer[i + 1] - scaled_gaps[i]

    # Clamp: shift up if any timestamp < 0
    min_ts = min(new_committer)
    if min_ts < 0:
        shift = -min_ts
        new_committer = [t + shift for t in new_committer]

    # Apply and preserve author-vs-committer offset
    result = []
    for i, commit in enumerate(commits):
        author_offset = commit["author_ts"] - commit["committer_ts"]
        new_c = dict(commit)
        new_c["committer_ts"] = new_committer[i]
        new_c["author_ts"] = new_committer[i] + author_offset
        result.append(new_c)

    return result


# ---------------------------------------------------------------------------
# Rewriting history
# ---------------------------------------------------------------------------

def rewrite_history(repo: str, new_commits: list[dict], dry_run: bool = False) -> None:
    """
    Use `git filter-branch` (via env-filter) to rewrite commit dates.
    Requires that the working tree is clean and HEAD is the branch tip.
    """
    if dry_run:
        print("\n[DRY RUN] Would rewrite the following commits:\n")
        for c in new_commits:
            author_dt = datetime.fromtimestamp(c["author_ts"], tz=timezone.utc)
            commit_dt = datetime.fromtimestamp(c["committer_ts"], tz=timezone.utc)
            print(
                f"  {c['hash'][:10]}  author={author_dt.isoformat()}  "
                f"commit={commit_dt.isoformat()}  {c['subject'][:60]}"
            )
        return

    # Build a lookup: hash → new timestamps
    ts_map: dict[str, dict] = {c["hash"]: c for c in new_commits}

    # Write a temporary env-filter script
    lines = ["#!/bin/sh"]
    for h, c in ts_map.items():
        author_date = datetime.fromtimestamp(c["author_ts"], tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        commit_date = datetime.fromtimestamp(c["committer_ts"], tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        lines.append(
            f'[ "$GIT_COMMIT" = "{h}" ] && '
            f'export GIT_AUTHOR_DATE="{author_date}" '
            f'GIT_COMMITTER_DATE="{commit_date}"'
        )

    script_path = Path(repo) / ".git" / "_stretch_filter.sh"
    script_path.write_text("\n".join(lines) + "\n")
    script_path.chmod(0o755)

    try:
        print("\nRewriting history with git filter-branch …")
        result = run_git(
            [
                "filter-branch",
                "-f",
                "--env-filter",
                str(script_path),
                "--",
                "--all",
            ],
            repo,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git filter-branch failed:\n{result.stderr.strip()}"
            )
        print("Done. History rewritten successfully.")
        print(
            "\nIMPORTANT: This rewrites history. "
            "If this repo has already been pushed, you will need:\n"
            "  git push --force\n"
        )
    finally:
        script_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stretch or compress the commit timeline of a Git repository "
            "by scaling inter-commit intervals."
        )
    )
    parser.add_argument(
        "repo",
        help="Path to the Git repository.",
    )
    parser.add_argument(
        "--factor",
        type=float,
        default=2.0,
        help=(
            "Scaling factor for inter-commit time gaps. "
            ">1 stretches history, <1 compresses it (default: 2.0)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without modifying the repository.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate the repository and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.factor <= 0:
        print("Error: --factor must be a positive number.", file=sys.stderr)
        sys.exit(1)

    # --- Step 1: validate ---------------------------------------------------
    print(f"Validating repository: {args.repo}")
    ok, message = validate_git_repo(args.repo)
    print(f"  {'OK' if ok else 'FAIL'}: {message}")

    if not ok:
        sys.exit(1)

    if args.validate_only:
        sys.exit(0)

    # --- Step 2: load commits -----------------------------------------------
    print("\nLoading commit history …")
    commits = get_commits(args.repo)
    print(f"  {len(commits)} commit(s) loaded.")

    if len(commits) < 2:
        print("Nothing to rewrite: fewer than 2 commits.")
        sys.exit(0)

    # --- Step 3: compute new timestamps -------------------------------------
    print(f"\nScaling inter-commit intervals by factor {args.factor} …")
    new_commits = scale_timestamps(commits, args.factor)

    first_orig = datetime.fromtimestamp(commits[0]["committer_ts"], tz=timezone.utc)
    last_orig = datetime.fromtimestamp(commits[-1]["committer_ts"], tz=timezone.utc)
    first_new = datetime.fromtimestamp(new_commits[0]["committer_ts"], tz=timezone.utc)
    last_new = datetime.fromtimestamp(new_commits[-1]["committer_ts"], tz=timezone.utc)

    print(f"  Original span: {first_orig.date()}  →  {last_orig.date()}")
    print(f"  New span:      {first_new.date()}  →  {last_new.date()}")

    # --- Step 4: rewrite ----------------------------------------------------
    rewrite_history(args.repo, new_commits, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
