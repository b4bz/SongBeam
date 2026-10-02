# Security and data policy

This repository contains public code, design files, synthetic test generators and sanitized documentation. Field recordings, voices, exact wildlife locations, private site labels, calibration tied to a private deployment, credentials, databases, local configuration and run manifests belong outside the checkout. Do not put them in issues, pull requests, Actions logs or release assets.

## Repository controls

Local commits use `.githooks/pre-commit`; configure it with `git config core.hooksPath .githooks`. It requires Gitleaks 8.30.1 from the [official release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1), with SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` for the Linux x64 tarball. CI downloads and verifies that exact archive. CI and hooks run `scripts/check_repository_policy.py` plus Gitleaks.

As verified on 2026-10-01 for this fork, GitHub secret scanning and push protection, Dependabot security updates, and private vulnerability reporting are enabled. `main` requires a pull request and the `policy-and-secrets`, `processor`, and `firmware-build` checks; force pushes and deletions are disabled and protection applies to administrators. The feature branch's latest checks passed. Recheck repository settings after any account, plan, or rule change.

- `.gitignore` reduces accidental staging; it does not protect tracked files, old commits or force-added files. Inspect staged paths and content before every commit. Scan inherited history before first publication of new work; a fork inherits upstream history.
- Require secret scanning and a repository-content policy check in CI. Run the same checks locally before pushing. Enable GitHub secret scanning/push protection and protected-branch checks where available; record any account-level capability that is unavailable. Never claim a control is enabled without reading it back.
- Generate synthetic acoustic fixtures in temporary directories. Field examples require explicit publication permission, sanitized metadata and an explicit reviewed exception; the default is no audio in Git, including Git LFS.
- Public examples use paths such as `/path/to/input.wav` and synthetic identifiers. Reject new symlinks into local data, embedded credentials, large model/build files and unreviewed binaries. Keep dependencies locked and model downloads separate, with provenance and checksums.
- Preserve all upstream author and license notices. Audit added code and dependencies; record licenses for firmware, desktop processor, tools and model weights separately.

## Runtime controls

Process local files by default. Enable no telemetry, public server or automatic upload. Download code/models only through explicit setup, verify checksums where provided, and do not execute downloaded scripts blindly. Do not load pickle or other executable model/config formats from untrusted inputs.

Treat filenames, audio headers, metadata and configuration as untrusted. Validate sizes, channels, rates, frame alignment and finite values; bound memory, output size, process count and codec execution time. Invoke codecs with argument arrays and no shell interpolation. Normalize paths and enforce output-root containment, including symlinks; use exclusive creation and atomic finalization. Failed or canceled jobs must retain an honest status, never replace originals or appear complete.

Check the intended data filesystem and available space before and during writes. Do not fall back onto an unmounted directory on the OS disk. Disk checks are not quotas; concurrent jobs need admission control or reservations. Write private local outputs with restrictive permissions and redact location/path details from shareable reports.

## CI and releases

Use GitHub-hosted runners for untrusted contributions, minimal read-only token permissions and Actions pinned to full reviewed commit SHAs. No deployment secrets or local data mounts in pull-request tests. Do not combine privileged `pull_request_target` with checkout/execution of untrusted contributor code. Keep artifacts to an explicit allowlist of sanitized reports and deterministic synthetic results. Publish firmware binaries only from identified source/toolchain builds with hashes; never auto-flash hardware.

## Reporting and response

Use the repository's private vulnerability-reporting feature when enabled. If it is unavailable, open only a minimal issue requesting a private reporting channel; do not disclose exploit details, tokens, recordings or sensitive locations publicly. Maintainers should enable private reporting during setup and keep the channel documented. No response-time promise is made until a maintainer adopts one.

If a secret is committed, revoke/rotate it immediately, stop further publication, assess logs/artifacts and coordinate history cleanup. Adding an ignore rule or deleting the latest file does not remove exposure from existing history.
