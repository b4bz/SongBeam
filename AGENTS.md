# Development rules

Read `README.md` and `SECURITY.md` before changes. Treat the repository as public.

- Keep raw/derived audio, model weights, databases, private configuration, site information and secrets outside Git. Use synthetic fixture generators. Check ignore behavior and staged files before commits; run the required secret and content-policy checks.
- Preserve upstream provenance and licenses. Never copy GPL-covered Luscinia code into a differently licensed component without an explicit compatible licensing decision and notices.
- Implement against a versioned data contract: native sample clock, explicit channel map and array geometry, source/output hashes, processing parameters, time offsets, model identity and failure status.
- Keep raw samples immutable. Never repair malformed recordings silently, invent missing channels, downmix multichannel input implicitly, or equate multiple beams with multiple identified birds.
- Benchmark algorithm changes against deterministic synthetic signals and paired original-channel recordings. Keep source code, tool versions and seeds with reproducible tests. Mark unverified field behavior honestly.
- Require focused regressions for header/frame integrity, known fractional delays, silence, multiple sources, chunk boundaries, codecs, low storage, cancellation and failure status.
- Maintain bounded memory and disk use. Enforce no-overwrite/atomic outputs and honest errors. Avoid global environment, service or system-library changes.
- Firmware changes may be compiled and tested in isolation. Device connection, wiring, flashing, live recorder changes and production-data migration need explicit task authorization.
- Keep the README feature-status table current. Proposed commands and features must be marked planned until implemented and verified. Update security and rollback notes when behavior changes.
