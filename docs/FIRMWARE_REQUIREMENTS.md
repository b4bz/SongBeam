# Recorder firmware requirements and verification

This is the target for a SongBeam 3.1 carrying a Teensy 4.1. A composite USB implementation now exists in source, but has not passed device or host audio validation; the status table in the root README distinguishes implemented code from verified behavior. Keep private recordings and configuration outside this public repository.

## Operating modes

| Power/connection | Default action | Host control |
| --- | --- | --- |
| No enumerated USB host | Begin four-channel 16-bit PCM capture to microSD as soon as the card and RTC are ready; split into bounded WAV files | None required |
| USB host enumerated | Controlled idle, with no automatic SD writes | USB Serial commands to inspect status, select capture/stream mode, start and stop |
| USB four-channel mode | Send framed, interleaved, lossless 44.1 kHz PCM to the desktop receiver | Explicit start/stop; no simultaneous SD writing by default |
| USB stereo mode | Send framed, interleaved stereo PCM to the desktop receiver | Explicit start/stop; nominal left and right groups each average two microphones |
| USB card mode | Give the host MTP file access after all recorder files have been closed | Explicit `CARD` command; safely finish host transfers, then power-cycle before recording again |

The desktop receiver handles both four-channel and two-channel streaming. Standard **USB microphones** are different, unimplemented interfaces. Teensyduino 1.62 provides a stock Serial + MTP Disk (Experimental) composite profile; it does not include USB Audio at the same time. MTP lets the computer browse/copy the card as a portable device rather than mount it as a block disk. Firmware updates continue through the Teensy USB Program/HalfKay path and a versioned, checksum-identified HEX. The physical Program button remains the recovery method. Linux host browse, a small write/read/delete and checksum readback of 18 WAVs passed. A raw post-transfer exFAT check remains deferred because it requires the card reader; long transfers and a healthy replacement card still need validation.

The sketch waits three seconds after setup for USB enumeration before starting autonomous SD capture. If a host appears during a file, it stops and closes that file before entering host-controlled mode. This is a bounded startup delay, not a requirement to press a button in field use.

Autonomous capture still respects the board's TPL5110 on-time/power-off control. Starting immediately does not by itself mean uninterrupted recording until the batteries are removed. The requested duty cycle, file length and restart gap need a physical battery-only test and an explicit field configuration before deployment.

The experimental stream uses `SB4C/1` frames. Each has a 24-byte little-endian header (`SB4C` magic, version, kind, 128-frame count, sequence, milliseconds from stream start, cumulative dropped blocks, CRC32) followed by 1024 bytes of interleaved four-channel PCM16 or 512 bytes of stereo PCM16. The host receiver rejects version, sequence, CRC, truncation and loss errors; it does not invent missing audio. `STREAM 4` or `STREAM 2` is acknowledged before binary frames begin; `STOP` emits an end frame. This contract and the desktop parser have synthetic tests, but USB throughput and reconnect behavior require hardware tests.

On the reviewed PCB, microphones A1/A2 share one ADAU7002 and A3/A4 share the other. Their nominal PCB X positions are 0, 45, 75 and 120 mm relative to A1. The provisional stereo left group is A1+A2 and right group A3+A4, each at 0.5 gain to avoid overflow. The physical channel order, polarity, group orientation in an enclosure, and I2S synchronization must be measured before labeling a stream calibrated. Summing separated microphones can attenuate off-axis frequencies; a selectable one-microphone-per-side monitor option should remain available. Preserve all four raw channels as the scientific master.

## Integrity and field behavior

- Keep SD recording and USB streaming separate until a concurrent stress test proves both can run without gaps. Never let host backpressure silently stall capture; report sequence gaps or fail the session.
- Latch an SD open/allocation/write or capture-integrity failure for the current power session and stop autonomous retries. Keep USB status and MTP recovery access available; do not flood a failing card with new file attempts. Verify this behavior with an induced fault before field use.
- Treat `CARD` as an exclusive, one-way mode until safe host eject and power cycle. The tested MTP implementation creates its own `mtpindex.dat` on the SD card even when the host only browses; do not describe that operation as strictly read-only.
- Use a versioned packet header for the four-channel desktop stream: sync word, sequence, frame count, capture timestamp, PCM format and CRC. The receiver must detect gaps, reject bad packets, and mark incomplete files honestly. It must not insert invented samples or overwrite raw data.
- Start the file-duration timer after open/preallocation and queue activation. Emit per-file preparation time, captured frames, maximum queue/audio-memory use, SD write count, worst write latency and the SD controller's error code/data when available. Treat audio-memory exhaustion or material frame shortfall as a failed capture; leave a `.failed` marker next to an incomplete WAV when the card still accepts writes.
- Maintain bounded internal RAM2 buffers and contiguous SD preallocation; benchmark any CPU clock change against recording integrity **and** battery current. The 384 KiB RAM2 ring buffers roughly 1.1 seconds of four-channel audio and reports overflow. This is a latency cushion, not proof that no samples were lost: the tested Samsung EVO MB-MP64D card later stalled for more than 3 seconds, returned short writes and refused file opens. A different card and a power/slot check are required before unattended recording.
- Require a healthy card, enough free capacity, valid RTC or an explicit unsynchronized-time policy, collision-proof filenames, and an unambiguous LED/USB error status. Close and sync each WAV before shutdown. Do not report an incomplete recording as complete.
- Define USB and battery power isolation on the actual SongBeam 3.1 assembly before simultaneous connection. Until then, main D-cell batteries remain disconnected during USB work.

## Acceptance gates

1. Record at least ten consecutive one-minute, four-channel files on the actual board and card. Each WAV must have a valid header, aligned channels and a duration within the measured start/stop tolerance; no unexplained shortfall, queue overflow, saturated pool or failed write is allowed. Repeat with realistic long file lengths and another card.
2. Power-cycle with the CR2032 installed and verify RTC retention, filename chronology, immediate battery-only start and clean completion/power-off. Measure battery current at idle and capture clocks. Bench USB success alone does not pass this gate.
3. For four-channel and stereo USB, verify sustained host capture, sequence/CRC continuity, channel identity and recovery after unplug or receiver termination. Inspect both stereo pair sums with a controlled source at each microphone. For MTP, verify host listing, readback and a small configuration write on a known-good card, safe host eject and no concurrent recorder access.
4. Reflash by USB, check reported firmware version/hash, and demonstrate Program-button recovery. Keep a last-known-good image. Do not deploy or call the recorder field-ready until the above gates pass.

Useful subsequent specifications are a versioned configuration schema with explicit defaults, UTC time and offset metadata, battery voltage and storage headroom reporting, per-channel level/clip monitoring, a watchdog with an incomplete-session marker, and a host command that safely closes the current file before power removal.
