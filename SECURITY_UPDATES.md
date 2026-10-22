# Dependency updates and deployment

The central pinned Renovate job runs at 06:17 and 18:17 Europe/Oslo every day
and opens real security-fix PRs. Dependabot retains dependency alert coverage;
its routine version PRs are disabled. Native security PRs remain enabled alongside
Renovate; check for overlapping PRs before merging. GitHub's live validator
rejects Dependabot cron intervals below 24 hours, so it cannot own this cadence.
Existing trusted sources, release-age policies and security release-age exceptions
continue to apply in CI. Existing GardenOps CI tests each PR.

Merge a passing PR to main. The optional host deployment timer checks every
15 minutes for a merged dependency update. It requires successful **push CI on
the exact current main SHA**, ancestry from the installed revision, and a diff
limited to Python/npm manifests and locks plus Dependabot configuration.
Application, migration or unrelated configuration changes use normal deployment.
They are reported as requiring that deployment rather than silently bundled.

The existing atomic deployer prepares the release, migrates/checks integrity,
activates it and retains the previous release. This poller rechecks main and
the current release after preparation and verifies the running process's cwd,
release receipt and public HTTPS health after activation. It never merges PRs,
changes source constraints, starts dormant apps, or creates database copies.

## Install on the actual production host

First deploy current main through the normal release process. Use the already
installed atomic deployer and a dedicated clean control repository that can
fetch origin/main. Confirm its host-specific settings match the example JSON.
Copy the Python helper to `/usr/local/lib/gardenops-security-deploy.py` as
root:root 0755, and the three unit files into `/etc/systemd/system` root:root
0644. Copy/adapt the example to `/etc/gardenops-security-deploy.json`, root:root
0600, including the real HTTPS health URL. Do not install with the placeholder.
No new GitHub deployment secret is needed: metadata is read from this public
repository; Git fetch uses the server's existing repository access.

Install the matching updated `gardenops-atomic-deploy` too: the poller requires
its `binding` and `activate-if-current REVISION PREVIOUS` commands. Binding must
match the configured repository, release root and service. The expected previous
revision is checked while holding the canonical deployment lock, so a concurrent
manual release cannot be overwritten. Manifest script/build-source changes are
normal releases, not dependency-only updates. Public health must be HTTPS,
non-redirecting JSON with `status: ok`.

Run the helper without `--apply` first. Verify its selected commit and CI run.
Then run `systemctl daemon-reload` and
`systemctl enable --now gardenops-security-deploy.timer`. Verify the unit schedule
and run the service once; a no-op current-release result validates connectivity
and running health, **not** an actual update deployment. After the next merged
fix, require `last.json` phase `deployed`, the target SHA and healthy live app.

The service is serialized; systemd kills its whole process group on timeout.
GitHub outages, rate limits, CI failures and revision drift fail visibly in the
service journal. `OnFailure` emits an error to the host's existing syslog lane.
External alert delivery must be checked on that host; syslog alone is not a
promise of a phone/push notification. No automatic blind activation retry:
`activating` or `failed` receipts block further attempts until inspected. Check
the actual selected/running release and atomic deploy logs, recover using the
existing deployer if needed, then archive the inspected receipt to clear it.
Never clear it just to make the timer green.

## Focused tests

`python3 -B -m unittest discover -s tests -p test_security_autodeploy.py -v`

These use tiny temporary fixtures and mock the deployer/GitHub API. They prove
failed CI, mixed code changes, migrations, head movement, failed health,
uncertain activation and stale crash receipts cannot report successful deploys.
They do not prove the remote server is configured or reachable.
