# CI notes

- 8 October 2026, release PR #85 (0.26.0): the Windows `ci` job stopped after 3 min 49 s
  with exit code 1 and no ruff, mypy or pytest annotation, while the Linux tests passed on
  the same commit and both merged PRs (#83, #84) had passed `ci` on their own. Treated as a
  runner problem and run again with this commit.
- 8 October 2026, PR #86 (docs only, AI Desk spec review 2): "Tests (Linux, Qt offscreen)"
  ended with exit code 139 (a segmentation fault in the offscreen Qt platform) after 2 min,
  while the Windows `ci` job ran the full suite green on the same commit. No code changed,
  so it was run again with this commit.
- Same PR, next commit: Linux green, but the Windows `ci` job was still running after 17
  minutes (it normally takes about 9). Treated as a hung runner and run again.
- 8 October 2026, PR #89 (docs only, news watch spec): Windows `ci` still running after 14
  minutes with parallel tests (5 to 6 minutes on #87 and #86). Run again. Three hangs in a
  day on docs-only commits: the CI version 2 `changes` job (scripts/ci/workflows) would have
  skipped all of them.
