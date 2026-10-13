# Changelog

## 0.1.0 – 2026-10-13
- First MAGBOT KVM lifecycle stack alongside existing MAG services
- `spawnbot`, `despawnbot`, `magbot doctor`, `status`, `exec`, `logs`
- Cloud-init Ubuntu Noble guest with unprivileged bot user and Playwright Chromium
- Serialized conservative resource admission and hard fail on unsupported KVM, CPU or disk I/O controls
- VM-specific full logical data cleanup, ownership marker checks and orphan protection
- No host restart and no modifications to existing MAG Docker/AI stack
- Unit tests for validation, limits, readiness and teardown
- Built-in throwaway real-VM/browser smoke-test with EXIT cleanup
- Base cloud image SHA-256 checked against official Ubuntu HTTPS checksum list

## 2026-10-13 — libvirt locale/idempotency hotfix

- Force C locale for installer and all libvirt CLI calls; localized `Aktiv: ja` no longer triggers a duplicate `net-start` or breaks VM readiness and teardown.
- Handle a concurrent already-active default libvirt network without aborting installation.
- Add regression tests for environment and network idempotency.

## 1.0.2 – 2026-10-13

- Publish bot metadata atomically (avoid partial reads during provisioning).
- Read-only `magbot inspect ID` for distinguishing missing registry, guest disk and libvirt domain.
- Smoke test uses a unique bot ID, blocks concurrent smoke tests, captures host and guest diagnostics on failure, and verifies both VM/domain and directory disappear during cleanup.
- Live readiness and full browser initialization must still be confirmed on the actual MAG host. No claim of a completed live E2E test.

- Added non-disruptive `upgrade-magbot.sh` with tests, backup and automatic rollback on failed `doctor`, avoiding a redundant apt/libvirt network re-installation on an already functional host.
