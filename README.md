# git-history-stretcher

A Python CLI tool to validate Git repositories and programmatically rewrite commit history by scaling or adjusting timestamps between commits, while preserving chronological consistency.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

## Features

- Full repository validation before any modification
- Scales inter-commit time gaps by a configurable factor
- Anchors the last commit to its original timestamp — never pushes it into the future
- **`--last N`** — restrict scaling to the last N commits; commits before the window are shifted earlier if needed to keep chronological order
- **`--first N`** — restrict scaling to the first N commits; commits after the window are shifted later if needed to keep chronological order
- Commits outside a window are never re-scaled — only time-shifted when necessary
- Preserves the author/committer time offset for every commit
- Dry-run mode to preview all changes safely
- Zero third-party dependencies

## Requirements

- Python 3.10+
- Git installed and available on `PATH`

## Installation

```bash
git clone https://github.com/BaseMax/git-history-stretcher
cd git-history-stretcher
```

No dependencies to install. The tool uses only the Python standard library.

## Usage

```bash
python git_history_stretcher.py <repo> [--factor N] [--last N] [--first N] [--dry-run] [--validate-only]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `repo` | *(required)* | Path to the target Git repository |
| `--factor` | `2.0` | Scaling factor applied to every inter-commit gap |
| `--last N` | off | Restrict scaling to the **last** N commits only |
| `--first N` | off | Restrict scaling to the **first** N commits only |
| `--dry-run` | off | Preview new timestamps without writing anything |
| `--validate-only` | off | Only run repository health checks and exit |

> `--last` and `--first` cannot be combined.

### Examples

```bash
# Double the gap between every commit
python git_history_stretcher.py /path/to/repo --factor 2

# Triple all gaps — preview only
python git_history_stretcher.py /path/to/repo --factor 3 --dry-run

# Compress history to half the original spacing
python git_history_stretcher.py /path/to/repo --factor 0.5

# Stretch only the last 10 commits (commits before them shift if needed)
python git_history_stretcher.py /path/to/repo --factor 3 --last 10

# Stretch only the first 5 commits (commits after them shift if needed)
python git_history_stretcher.py /path/to/repo --factor 2 --first 5

# Preview stretching the last 20 commits without writing anything
python git_history_stretcher.py /path/to/repo --factor 2 --last 20 --dry-run

# Only verify that the repository is healthy
python git_history_stretcher.py /path/to/repo --validate-only
```

### Sample output

```
Validating repository: /path/to/repo
  OK: Valid Git repository with 142 commit(s).

Loading commit history ...
  142 commit(s) loaded.

Scaling last 20 commit(s) by factor 3.0 …
  Original span: 2024-01-03  →  2025-11-20
  New span:      2023-07-11  →  2025-11-20
  122 commit(s) outside the window were time-shifted to preserve order.

Rewriting history with git filter-branch ...
Done. History rewritten successfully.

IMPORTANT: If this repo has been pushed, run:
  git push --force
```

## How it works

1. **Validate** - verifies that the path exists, contains `.git`, passes `git rev-parse`, has at least one commit, and passes `git fsck` object-store integrity check.
2. **Load** - reads the full commit history from `HEAD` in oldest-first order, capturing hash, author, committer, timestamps, and subject.
3. **Select window** - by default the entire history is the window.  With `--last N` the window is the last N commits; with `--first N` it is the first N commits.
4. **Scale** - within the window, computes the gap between each consecutive pair of committer timestamps and multiplies it by `--factor`.
   - For the default / `--last N` mode the **last** commit in the window is the anchor (clamped to ≤ now); gaps expand backwards.
   - For `--first N` mode the **first** commit in the window is the anchor; gaps expand forwards.
   - If any resulting timestamp would fall before the Unix epoch, the entire series is shifted forward.
5. **Shift neighbours** - if the scaled window's boundary commit has moved past an adjacent commit outside the window, all commits on that side are shifted by the same delta (their inter-commit gaps stay intact).
6. **Rewrite** - generates a temporary `git filter-branch --env-filter` shell script that sets `GIT_AUTHOR_DATE` and `GIT_COMMITTER_DATE` for every affected commit, then removes the script after execution.

> **Warning:** `git filter-branch` rewrites history. If the repository has already been pushed to a remote, you will need `git push --force` afterwards. Inform collaborators before doing this on a shared branch.

## Project structure

```
git-history-stretcher/
+-- git_history_stretcher.py   # single-file CLI tool
```

## License

MIT License - Copyright - 2026 Seyyed Ali Mohammadiyeh (Max Base)
