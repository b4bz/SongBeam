# Recorder firmware requirements and verification

This is the target for a SongBeam 3.1 carrying a Teensy 4.1. A composite USB implementation now exists in source, but has not passed device or host audio validation; the status table in the root README distinguishes implemented code from verified behavior. Keep private recordings and configuration outside this public repository.

## Operating modes

| Power/connection | Default action | Host control |
| --- | --- | --- |
| No enumerated USB host | Begin four-channel 16-bit PCM capture to microSD as soon as the card and RTC are ready; split into bounded WAV files | None required |
| USB host enumerated | Controlled idle, with no automatic SD writes | USB Serial commands to inspect status, select capture/stream mode, start and stop |
| USB four-channel mode | Send framed, interleaved, lossless 44.1 kHz PCM to the desktop receiver | Explicit start/stop; no simultaneous SD writing by default |
| USB stereo mode | Appear as a standard stereo USB audio input | Explicit start/stop; nominal left and right groups each average two microphones |

The desktop receiver is the selected four-channel streaming interface. A standard **four-channel USB microphone** is a different, unimplemented interface that would require custom USB descriptors and host testing. Teensyduino 1.62 offers class-compliant stereo USB Audio, and its ready-made Serial + MIDI + Audio composite mode supplies a separate control/data port; the MIDI interface is unused. Firmware updates continue through the Teensy USB Program/HalfKay path and a versioned, checksum-identified HEX. The physical Program button remains the recovery method.

The experimental four-channel stream uses `SB4C/1` frames. Each has a 24-byte little-endian header (`SB4C` magic, version, kind, 128-frame count, sequence, milliseconds from stream start, cumulative dropped blocks, CRC32) followed by 1024 bytes of interleaved four-channel PCM16. The host receiver rejects version, sequence, CRC, truncation and loss errors; it does not invent missing audio. `STREAM 4` is acknowledged before binary frames begin; `STOP` emits an end frame. This contract and the desktop parser have synthetic tests, but USB throughput and reconnect behavior require hardware tests.

On the reviewed PCB, microphones A1/A2 share one ADAU7002 and A3/A4 share the other. Their nominal PCB X positions are 0, 45, 75 and 120 mm relative to A1. The provisional stereo left group is A1+A2 and right group A3+A4, each at 0.5 gain to avoid overflow. The physical channel order, polarity, group orientation in an enclosure, and I2S synchronization must be measured before labeling a stream calibrated. Summing separated microphones can attenuate off-axis frequencies; a selectable one-microphone-per-side monitor option should remain available. Preserve all four raw channels as the scientific master.

## Integrity and field behavior

- Keep SD recording and USB streaming separate until a concurrent stress test proves both can run without gaps. Never let host backpressure silently stall capture; report sequence gaps or fail the session.
- Use a versioned packet header for the four-channel desktop stream: sync word, sequence, frame count, capture timestamp, PCM format and CRC. The receiver must detect gaps, reject bad packets, and mark incomplete files honestly. It must not insert invented samples or overwrite raw data.
- Start the file-duration timer after open/preallocation and queue activation. Emit per-file preparation time, captured frames, maximum queue/audio-memory use, SD write count and worst write latency. Treat audio-memory exhaustion or material frame shortfall as a failed capture.
- Maintain bounded internal RAM2 buffers and contiguous SD preallocation; benchmark any CPU clock change against recording integrity **and** battery current. The 384 KiB RAM2 ring buffers roughly 1.1 seconds of four-channel audio and reports overflow. This is a latency cushion, not proof that no samples were lost: one tested card later stalled for more than 3 seconds and returned a short write.
- Require a healthy card, enough free capacity, valid RTC or an explicit unsynchronized-time policy, collision-proof filenames, and an unambiguous LED/USB error status. Close and sync each WAV before shutdown. Do not report an incomplete recording as complete.
- Define USB and battery power isolation on the actual SongBeam 3.1 assembly before simultaneous connection. Until then, main D-cell batteries remain disconnected during USB work.

## Acceptance gates

1. Record at least ten consecutive one-minute, four-channel files on the actual board and card. Each WAV must have a valid header, aligned channels and a duration within the measured start/stop tolerance; no unexplained shortfall, queue overflow, saturated pool or failed write is allowed. Repeat with realistic long file lengths and another card.
2. Power-cycle with the CR2032 installed and verify RTC retention, filename chronology, immediate battery-only start and clean completion/power-off. Measure battery current at idle and capture clocks. Bench USB success alone does not pass this gate.
3. For four-channel USB, verify sustained host capture, sequence/CRC continuity, channel identity and recovery after unplug or receiver termination. For stereo USB, verify the host sees 44.1 kHz stereo PCM and inspect both pair sums with a controlled source at each microphone.
4. Reflash by USB, check reported firmware version/hash, and demonstrate Program-button recovery. Keep a last-known-good image. Do not deploy or call the recorder field-ready until the above gates pass.

Useful subsequent specifications are a versioned configuration schema with explicit defaults, UTC time and offset metadata, battery voltage and storage headroom reporting, per-channel level/clip monitoring, a watchdog with an incomplete-session marker, and a host command that safely closes the current file before power removal.
