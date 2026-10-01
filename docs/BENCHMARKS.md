# Synthetic benchmark record

Run 2026-10-01 on a local Linux x86-64 desktop. CPU timings were measured with `/usr/bin/time`; memory is peak resident set size. Inputs were deterministic repeating one-second 44.1 kHz, 16-bit, four-channel band-limited synthetic noise with short inter-channel delays. They are useful for throughput and memory scaling, but highly compressible and unsuitable for ecological accuracy claims.

| Duration | Tracks | Output formats | Elapsed | Peak RSS |
| --- | ---: | --- | ---: | ---: |
| 1 min | 1 automatic | FLAC24 | 1.41 s | 105 MiB |
| 10 min | 1 automatic | FLAC24 | 8.38 s | 107 MiB |
| 30 min | 1 automatic | FLAC24 | 23.97 s | 111 MiB |
| 30 min | 2 manual directions | WAV24 + FLAC24 + MP3 192 kb/s | 18.72 s | 112 MiB |

The 30-minute two-track run produced two 238,140,044-byte WAVs, two FLACs of 152,419,054 and 137,806,771 bytes, and two 43,201,768-byte MP3s. The source and outputs stayed on the OS-drive scratch area for this benchmark. A second automatic track was separately exercised on two independent planted source directions in short synthetic tests; the long benchmark used manual directions because its repeated source contains only one caller. The algorithm is an offline CPU processor, so faster-than-real-time wall clock here does not establish battery-device capability.

Run from a clean install: `uv sync --directory processor --extra dev --locked` and `uv run --directory processor pytest -q`. To reproduce a speed profile, generate four-channel synthetic WAVs outside Git, then run `songbeam process INPUT --output NEW_DIRECTORY --channel-map 0,1,2,3 --format flac`. Capture elapsed time, peak RSS, source hash, configuration, formats and output hashes. A field benchmark must use actual SongBeam recordings, with separate decoding/localization/encoding timings and held-out bird annotations.
