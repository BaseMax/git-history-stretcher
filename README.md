# git-history-stretcher

A Python CLI tool that validates a Git repository and rewrites its commit
history by scaling the time intervals between commits.

## Requirements

- Python 3.10+
- Git installed and on `PATH`

## Usage

```bash
python git_history_stretcher.py <repo_path> [--factor N] [--dry-run] [--validate-only]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `repo` | *(required)* | Path to the Git repository |
| `--factor` | `2.0` | Scaling factor for inter-commit gaps. `2` doubles every gap, `0.5` halves them |
| `--dry-run` | off | Print new timestamps without modifying the repo |
| `--validate-only` | off | Only check repository health and exit |

### Examples

```bash
# Double the time between every commit (factor = 2)
python git_history_stretcher.py /path/to/my-repo --factor 2

# Preview what would happen without touching anything
python git_history_stretcher.py /path/to/my-repo --factor 3 --dry-run

# Just check whether the repo is valid
python git_history_stretcher.py /path/to/my-repo --validate-only

# Compress history: halve all gaps
python git_history_stretcher.py /path/to/my-repo --factor 0.5
```

## How it works

1. **Validation** — checks that the path exists, contains `.git`, passes
   `git rev-parse`, has at least one commit, and passes `git fsck`.
2. **Load history** — reads all commits on `HEAD` in chronological order.
3. **Scale gaps** — takes the gap between each consecutive pair of committer
   timestamps and multiplies it by `--factor`.  The last commit is anchored
   to its original timestamp (never pushed into the future).  If scaling
   pushes the first commit before the Unix epoch, the whole series is shifted
   forward to keep timestamps positive.
4. **Rewrite** — uses `git filter-branch --env-filter` to apply the new
   `GIT_AUTHOR_DATE` / `GIT_COMMITTER_DATE` values.

> **Warning:** `git filter-branch` rewrites history.  If the repository has
> already been pushed to a remote, a `git push --force` will be required
> afterwards.
