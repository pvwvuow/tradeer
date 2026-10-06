# Faster build, release and publish (6 October 2026)

Until 0.23.4 one fix ran the whole test suite five times and the build three times before
its release was published (about 75 minutes):

| Stage | Before | After |
|---|---|---|
| Fix PR | tests, ci, then build after ci (about 20 min) | tests, ci and build in parallel (about 12 min) |
| Merge to main | tests and ci again | nothing (release-please only, on Linux) |
| Release PR | no CI until a docs commit, then everything again (about 20 min) | CI starts by itself, jobs skipped (seconds), merges by itself |
| Publish | check.ps1 and the clean-install test again, then the build (about 25 min) | build, self-check, pack and upload (about 12 min) |

`release.ps1` and `build.ps1` already work the new way (the publish job no longer runs the
checks and the clean-install test a second time). The rest needs three changes by hand,
because the bot's GitHub token may not change workflows:

1. Copy `ci.yml` and `release.yml` from this folder over `.github/workflows/ci.yml` and
   `.github/workflows/release.yml` (GitHub: open the file, Edit, paste, Commit to main).
2. Create a fine-grained personal access token for this repository only, with Contents and
   Pull requests: Read and write. Save it as the repository secret `RELEASE_PLEASE_TOKEN`
   (Settings > Secrets and variables > Actions). Release PRs made with it run CI.
3. Settings > General > Pull Requests: tick Allow auto-merge.

Without the token everything still works as before: the release PR then waits for a commit
that starts its CI and for a merge by hand.

The 0.23.4 publish hung in its second test run and uploaded nothing, so 0.23.5 carries its
fix (and pytest-timeout: a hung test now fails after 5 minutes).
