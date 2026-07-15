# Journey Validation Ledger

This ledger records durable completion evidence for the phased journey program in
`docs/superpowers/plans/2026-07-11-complete-journey-verification-optimization.md`.
Raw traces, screenshots, timings, and reviewer work products remain under the
gitignored `research/optimization-map/` tree.

| Phase | PR | Final head | Focused validation | Cumulative validation | Independent reviewer | Disposition | Limitations |
|---:|---|---|---|---|---|---|---|
| 0 | [#101](https://github.com/nazrm/gardenops/pull/101) | `dc27b9069362205538b325562bd5e6075dd01086` | 43 focused tests passed through disposable PostgreSQL | Clean desktop and Pixel 7 cumulative Playwright run passed | Route `gpt-5.6-sol`, `ultra`, priority; visible identity Codex/GPT-5 family | PASS WITH DOCUMENTED LIMITATIONS | Delayed stale-response and broader-role proof remain Phase 1; full accessible-name and interaction enforcement remains Phase 8 |
| 1 | [#104](https://github.com/nazrm/gardenops/pull/104) | `2b730b4c5b4ab80024c1b139b6185f63c6b734fb` | 45 journey contract tests plus focused ownership, import, restore, map-object, and role regressions passed through disposable PostgreSQL | 10-profile desktop and Pixel 7 Playwright matrix passed; persisted eight-profile Phase 1 manifest replayed; four-shard backend suite and frontend production build passed | Route `gpt-5.6-sol`, `ultra`, priority; visible identity Codex/GPT-5 family | PASS | A1 remains Phase 5; broader offline and destructive recovery remains Phase 6; providers and terrain remain Phase 7; accessibility remains Phase 8; performance and full closure remain Phase 9 |
| 2 | Pending publication | Review baseline `d10a906d45f267e4268b650a0f001b85f58ab832`; accepted fixes pending publication commit | 143 focused backend tests and 142 journey harness/coverage tests passed through disposable PostgreSQL; frontend production build passed; the accepted rain-recurrence finding adds 23 passing focused rain/watering regressions | Exact Phase 2 passed 12 profiles/traces and cumulative Phase 0-2 passed 22 profiles/traces at `1fc0c68`; a later 12-profile remediation run completed all browser traces with zero backend errors and exposed only documented late-checker replay limitations, so the bounded protocol used focused checker tests instead of another browser replay | Route `gpt-5.6-sol`, `ultra`, priority; reviewer Chandrasekhar; one full-phase review at `d10a906` | Initial BLOCK: two Important and one Minor findings; all accepted findings resolved with focused validation, with no second review required by the bounded protocol | Read-only permutation probes do not establish mutation-order independence; late-checker replay remains a harness limitation; external provider variants remain Phase 7; complete accessibility remains Phase 8; measured performance and final closure remain Phase 9 |
| 3 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |
| 4 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |
| 5 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |
| 6 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |
| 7 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |
| 8 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |
| 9 | Not started | Not started | Not started | Not started | Not started | Not started | Not started |

Each completed row names the reviewed Git SHA, accepted orchestration route, and
visible reviewer identity. A phase receives one independent GPT-5.6 Sol Ultra
review after full implementation. Accepted findings are fixed and validated with
focused tests; those review-remediation changes are recorded in the same row and
do not trigger a second review or a repeated browser matrix.
