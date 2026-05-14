# git-history-stretcher

A Python CLI tool to validate Git repositories and programmatically rewrite commit history by scaling or adjusting timestamps between commits, while preserving chronological consistency.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

## Features

- Full repository validation before any modification
- Scales inter-commit time gaps by a configurable factor
- Anchors the last commit to its original timestamp never pushes it into the future
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
python git_history_stretcher.py <repo> [--factor N] [--dry-run] [--validate-only]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `repo` | *(required)* | Path to the target Git repository |
| `--factor` | `2.0` | Scaling factor applied to every inter-commit gap |
| `--dry-run` | off | Preview new timestamps without writing anything |
| `--validate-only` | off | Only run repository health checks and exit |

### Examples

```bash
# Double the gap between every commit
python git_history_stretcher.py /path/to/repo --factor 2

# Triple all gaps - preview only
python git_history_stretcher.py /path/to/repo --factor 3 --dry-run

# Compress history to half the original spacing
python git_history_stretcher.py /path/to/repo --factor 0.5

# Only verify that the repository is healthy
python git_history_stretcher.py /path/to/repo --validate-only
```

### Sample output

```
Validating repository: /path/to/repo
  OK: Valid Git repository with 142 commit(s).

Loading commit history ...
  142 commit(s) loaded.

Scaling inter-commit intervals by factor 2.0 ...
  Original span: 2024-01-03  ->  2025-11-20
  New span:      2022-04-16  ->  2025-11-20

Rewriting history with git filter-branch ...
Done. History rewritten successfully.

IMPORTANT: If this repo has been pushed, run:
  git push --force
```

## How it works

1. **Validate** - verifies that the path exists, contains `.git`, passes `git rev-parse`, has at least one commit, and passes `git fsck` object-store integrity check.
2. **Load** - reads the full commit history from `HEAD` in oldest-first order, capturing hash, author, committer, timestamps, and subject.
3. **Scale** - computes the gap between each consecutive pair of committer timestamps and multiplies it by `--factor`. The last commit is used as the anchor (clamped to <= now). If any resulting timestamp would fall before the Unix epoch, the entire series is shifted forward.
4. **Rewrite** - generates a temporary `git filter-branch --env-filter` shell script that sets `GIT_AUTHOR_DATE` and `GIT_COMMITTER_DATE` for every commit, then removes the script after execution.

> **Warning:** `git filter-branch` rewrites history. If the repository has already been pushed to a remote, you will need `git push --force` afterwards. Inform collaborators before doing this on a shared branch.

## Project structure

```
git-history-stretcher/
+-- git_history_stretcher.py   # single-file CLI tool
```

## License

MIT License - Copyright - 2026 Seyyed Ali Mohammadiyeh (Max Base)
