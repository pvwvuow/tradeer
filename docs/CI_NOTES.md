# CI notes

- 8 October 2026, release PR #85 (0.26.0): the Windows `ci` job stopped after 3 min 49 s
  with exit code 1 and no ruff, mypy or pytest annotation, while the Linux tests passed on
  the same commit and both merged PRs (#83, #84) had passed `ci` on their own. Treated as a
  runner problem and run again with this commit.
