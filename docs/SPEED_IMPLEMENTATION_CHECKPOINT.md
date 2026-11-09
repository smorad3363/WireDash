# Speed policy implementation checkpoint — 2026-11-09

This is an **unfinished implementation branch**, not a release. Do not merge or
install it as a completed feature. The production panel is unchanged.

Authoritative specification: `WireDash-speed-policy-renewal-roadmap.md`, Library
identity `libfile_6641da1450bc81918beeeab6cdcb27b0`, plan
`wiredash-fup-renewal-20261109-v1`. Read it completely before proceeding.

## Verified this execution

- Desktop Commander target `ubuntu-8gb-fsn1-1` /
  `fe3babf6-8dcf-4b3f-822c-ab37bb49c485` was online.
- GitHub main and healthy production image both on
  `fe2d63de49c21ceb54dafb595782afa24f66be5a`.
- Snapshot `/root/wiredash-speed-20261109-090313`: SQLite online backup of each
  main/jobs/log database, each destination quick_check returned ok. These are
  DB snapshots, not a complete configuration/key rollback archive. Obtain and
  rehearse the full deployment snapshot before production work.
- Disposable container `wiredash-speed-kernel-probe` exited and was removed.
  On its isolated dummy interface: HTB root/class, IFB link, ingress flower and
  mirred redirect commands succeeded. No production queue was touched.
  Host ifb/sch_htb modules loaded. This is **capability only**, not throughput,
  WireGuard continuity, restart or latency verification.
- Tests: 11 pure policy + 7 real SQLite store + 6 Flask private-route authorization
  cases passed. Existing API AST guard passed against baseline because no legacy
  application handler has been changed.

## Code on this branch

- `PeerSpeedPolicy.py`: independent thresholds/rates, validation, fixed periods,
  cumulative deltas, partial/full reset, conservative historic migration,
  delayed period-duration edit. Clock injected; no billing writes.
- `PeerSpeedStore.py`: additive portable tables; optimistic revisions;
  preserve/overwrite settings, peer generation, reset-stage facts/history,
  pending reset, durable kernel registry. **Not integrated into runtime.**
- `PeerSpeedUI.py`: separate blueprint with request-local session/cookie checks,
  API-key rejection, CSRF + Origin. **Not registered in dashboard.**
- `PeerTrafficControl.py`: prototype owned per-peer HTB/IFB classifier.
  **Not wired to the poller and not approved for production.**

## Required correctness work before any release

1. TC reconciler must recover from every intermediate failed/crashed install,
   not infer fully applied filters from a persisted intent. Currently registry
   may exist before all filters install and a reconstructed adapter may wrongly
   skip the remaining commands. Verify actual kernel rules; separate desired,
   pending and applied states; persist operations before mutation and applied
   only after verification. Handle interface recreation/ifindex changes, stale
   IFB rules, deletion/IP reuse, ownership conflicts, idempotent cleanup and
   teardown of only owned objects. Tests with a real namespace are mandatory.
2. Counter-decrease handling currently preserves the previous metered maximum.
   Validate against existing durable traffic-ledger behavior; never double count
   rollback/recovery samples or grant fresh allowance. Expand long-gap and exact
   boundary estimation tests. Do not silently discard completeness across gaps.
3. Store currently has reset facts and explicit pending state, but no cross-DB
   reset outbox reconciliation, ledger-stage idempotency or production hooks.
   Design and test these before changing existing model reset methods. Maintain
   old API return conventions. Zero-to-zero resets require explicit events.
4. Expiry facts are not hooked; add durable job-DB outbox in the actual expiry
   transaction and idempotent projection. Correlate reset+extension in either
   order, with factual source labels. No paid-renewal claim from a reset alone,
   initial entitlement, manual/scheduler reset or incomplete restore. Do not
   interpret `observe_expiry` prototype as implemented renewal history.
5. Add create/delete/restrict/restore/configuration rename/backup integration;
   no feature attrs on Peer serialization or legacy DashboardConfig. Preserve
   all keys, billing, quota/expiry jobs and existing configuration locks.
6. Settings preview counts need true affected counts, explicit confirmation,
   audit and concurrency guards (including a peer edited after preview).
   No per-peer DDL/one-query-per-peer read-model regression. Current store writes
   each sample individually inside a batch transaction; benchmark 5000 peers.
7. Build actual global/per-peer Vue controls, last renewal/reset/cycle/history,
   custom/exempt options, pending/error status. Test all specified layouts and
   API-key-only denial; no cross-server mode bypass. Choose no real-user Mbps.
8. Add controller, bounded retry/diff updates, startup restore/status, master
   disable, persistence bounds. Ensure it never enables restricted users.
9. Add full failure/retry/concurrency/API payload fixtures, restored-backup,
   5000-peer/50-client scale, real encrypted WG TCP/UDP capped + uncapped control
   tests and cleanup. Record actual measured rates and continuity.
10. Update CI release gates; build/test exact immutable commit. Only then merge
    main, publish/promote release pointer, canonical installer upgrade, compare
    GitHub SHA / release SHA / running SHA and verify production. None of these
    publication/deployment phases has happened for this feature.

## Local checks

```
python3 tests/test_speed_policy.py
python3 tests/test_speed_store.py  # requires SQLAlchemy + Flask
python3 tests/test_speed_ui.py    # requires SQLAlchemy + Flask
python3 tests/test_api_contract_unchanged.py fe2d63de49c21ceb54dafb595782afa24f66be5a
```

The local transient venv is `/workspace/scratch/5e981bc09c5b/validation/venv`.
Rebuild it if unavailable. Do not reuse current test results as evidence for
subsequent changes. Do not mark the roadmap's implementation/release phases
complete based on this checkpoint.
