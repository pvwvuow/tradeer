# Faster CI, version 2 (8 October 2026)

Measured on 8 October: a docs-only PR (#86) took 30+ minutes, with a Linux segfault (exit
139) and a Windows `ci` job hung for 20 minutes. Every PR, even docs, ran everything:

| Stage | Now | Version 2 |
|---|---|---|
| Docs-only PR | Linux tests + Windows lint/mypy/tests (~9 min) + build after ci (~10 min): ~20 min | `changes` sees docs only, the three jobs are skipped: under 1 min |
| Code PR | ~20 min, build waits for ci | ci and build in parallel: ~10 min (the build is the longest part) |
| Install | `pip install` of PySide6, lightgbm, PyInstaller... on every job (~2 min on Windows) | `uv` with a cache: ~15 s |
| Lint and mypy | on Windows (slow start) | on Linux, mypy with `--platform win32` |
| Tests | one process, one crash kills the run | `pytest-xdist` workers (`-n auto`), a crashed worker only fails its test |
| Hung job | 30 min timeout | 15 min (`ci`), 12 min (Linux), plus the 5 min per-test timeout |
| Release PR | needs a commit to start CI, then everything again | skipped in seconds (and auto-merge with the token) |

Already in the repo (no hand work): `pytest-xdist` and `-n auto` in pyproject.toml,
`check.ps1 -SkipInstall -SkipLint`, `build.ps1 -SkipInstall`.

## What you do once (5 minutes, GitHub website)

1. Open `.github/workflows/ci.yml` on GitHub, Edit, replace everything with
   `scripts/ci/workflows/ci.yml` from this folder, Commit to main. Same for `release.yml`.
2. Optional, for releases without waiting: a fine-grained token for this repository only
   (Contents and Pull requests: Read and write) saved as the secret `RELEASE_PLEASE_TOKEN`,
   and Settings > General > Allow auto-merge.

The required checks keep their names (`ci`, `build`, `Tests (Linux, Qt offscreen)`), so the
branch protection needs no change. A skipped job counts as passed; if the `changes` job
itself fails, the three jobs run anyway, so a broken filter never lets untested code in.

If `mypy --platform win32` reports something on Linux that Windows did not, remove
`--platform win32` from the Linux job and run `check.ps1 -SkipInstall` (with lint) on
Windows instead.
