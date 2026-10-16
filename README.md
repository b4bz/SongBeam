# SongBeam — recorder and offline array processing

SongBeam is an open bioacoustic recorder created by **Lies Zandberg and Robert Lachlan**, associated with Royal Holloway University of London. This is [a fork of the original SongBeam project](https://github.com/lzandberg/SongBeam). The repository retains its hardware designs, enclosure and Teensy recording sketch, and adds an independent offline processor. Original project documentation is at [CuCo](https://www.cuco.group/songbeam); hardware is distributed by [LabMaker](https://www.labmaker.org/products/songbeam).

## Original capabilities

The reviewed design uses a Teensy 4.1 and four IM69D130 microphones with two ADAU7002 converters. The stock sketch schedules recordings using a microSD `config.txt` and writes nominal 44.1 kHz, 16-bit, four-channel WAV audio. Offline directional inspection is provided by [Luscinia](https://github.com/rflachlan/Luscinia). The stock sketch does not produce a beamformed audio file or network stream.

Upstream baseline: `f1ee97e37906403cce5ea79b3501abc3c4fa7c52`. Code review identified recorder header/tail-write problems and a delay-estimation/export issue in Luscinia `v2.22.12.01.01`. Treat source availability as distinct from validated field operation. In the reviewed Luscinia release, **Save Sound exports original audio**, not the processed beamforming result.

## Extension status

The independent processor is version 0.2.0 in this checkout. Device calibration and outdoor performance remain to be measured.

| Feature | Status | Practical purpose |
| --- | --- | --- |
| Correct WAV header reservation, interleaved final buffers and checked writes | Physical USB bench produced three readable four-channel WAVs; endurance and field validation pending | Preserve channel alignment and detect SD failures. |
| Recording timer after SD allocation and internal-RAM capture ring | Ten one-minute USB-powered files passed on a 32 GB SanDisk Ultra; long field and battery tests pending | Remove allocation time from WAV duration and absorb long SD write stalls without losing channel alignment. |
| Automatic battery-mode recording and USB-controlled capture | USB idle/command behavior verified; RTC-only and recorder retention passed after header repair; autonomous battery test pending | Start four-channel capture on power-up without a computer; prevent unintended SD writes during USB control. |
| Selectable four-channel or summed stereo USB Serial stream | 75-second four-channel and eight-second stereo physical transport tests passed; endurance and acoustic channel mapping pending | Feed the desktop receiver raw channels or a lighter two-channel monitoring stream. |
| Exclusive USB MTP card-access mode | Host browse, small write/read/delete, record-command refusal and repeat WAV checksum readbacks on two cards passed; raw filesystem check deferred | Browse and copy SD files over USB after recording has stopped; a power cycle exits card mode. |
| Strict input validation and explicit channel layout | Implemented for profiled PCM16 WAV, including 4/6/8-channel layouts; physical mapping unverified | Reject malformed files and report dead/clipped selected microphones. |
| Versioned array profiles and source selection | Implemented; synthetic-tested | One processor supports SongBeam, Sipeed D80 and bounded custom geometry. |
| Direction estimation for both arrays | Implemented; synthetic-tested | SongBeam signed projection/angle and compatible bearings; Sipeed local azimuth and elevation magnitude with mirror ambiguity. |
| Streaming mono beamformed exporter | Implemented and synthetic-tested | Process long recordings with bounded memory and reproducible output. |
| One automatic beam and manually selected directions | Implemented and synthetic-tested | Improve a chosen calling direction with an inspectable fallback. |
| Up to two tracked directional exports | Experimental, synthetic-tested | Make overlapping callers easier to review when spatially resolvable. |
| WAV, FLAC and MP3 output choices | Implemented and synthetic-tested | Support analysis, archival storage and listening. |
| BirdNET input/result validation and failed-job restart | Implemented for files and flat mono batches | Prevent silent downmixing and false-success jobs. |
| Privacy controls, ignore rules and security policy | Active in the fork; see `SECURITY.md` | Keep field data, secrets and private site information out of a public fork. |

## Intended processing workflow

Validate the original WAV → select source profile and input layout → apply separately verified device calibration when available → estimate supported direction(s) → export one or more mono directional tracks → optionally classify lossless tracks with BirdNET → review results alongside a selected original channel. Preserve original WAVs and a manifest linking every derivative to source, profile, parameters and software versions.

SongBeam's array is linear, with reviewed microphone centers at 0, 45, 75 and 120 mm; it measures a direction projection and has equal-projection ambiguity. The nominal Sipeed D80 profile uses six outer microphones on a 40 mm radius ring. Its planar geometry has an above/below ambiguity. Separate tracks can retain other birds and noise. A track is not an identified individual bird, and multiple exported beams do not establish a bird count. Same-direction callers may remain an unresolved mixture.

## Planned output choices

| Output | Default policy | Intended use |
| --- | --- | --- |
| WAV | Native rate, mono PCM24; PCM16 compatibility or float32 intermediate optional | Editing, interchange and measurements. |
| FLAC | Native rate, mono PCM24, compression level 5 | Stored lossless PCM derivative; preferred default. |
| MP3 | Mono 192 kb/s CBR; 128 kb/s preview or 256 kb/s optional; VBR quality 2 optional | Listening and sharing. Keep a lossless analysis master. |

FLAC compression level changes speed/size, not decoded PCM quality. Derived 24-bit output does not add captured microphone resolution. Preserve native sampling unless an explicitly recorded resampling step is required. MP3 may change high-frequency detail and timing through encoder delay/padding. Separate mono files per track plus a manifest are the interoperable multi-beam output.

Install the independent processor in an isolated environment with a locked dependency set:

```sh
uv sync --directory processor --extra dev --locked
uv run --directory processor songbeam inspect /path/to/raw.wav --channel-map 0,1,2,3
uv run --directory processor songbeam process /path/to/raw.wav --output /path/to/new-run \
  --channel-map 0,1,2,3 --auto-beams 2 --format flac --format wav --format mp3
uv run --directory processor songbeam process /path/to/raw.wav --output /path/to/manual-run \
  --channel-map 0,1,2,3 --manual-angle 20 --manual-angle -30 --format flac
```

For a production data drive, add `--require-mount /path/to/data-drive` and place `--output` under that mount. The command stops if that filesystem is absent or changes during processing. The default scratch example above requires an explicitly chosen output directory.

Use `--source` to select `songbeam-4`, `sipeed-d80` or a local profile JSON. The layout chooses nominal raw channels; a device calibration JSON or explicit overrides record measured mapping, gains, polarities and fixed delays. Defaults are nominal and require physical verification. The input validator rejects malformed/inconsistent WAVs and unsupported formats; it never repairs the source. Each new output directory contains separate full-timeline mono tracks plus `manifest.json` with hashes, profile/layout/calibration settings, direction estimates and activity. Existing output directories are refused. Source WAV stays unchanged. An experimental second track can retain a brief distinct caller; it may contain bleed or track swaps. The active algorithm is normalized delay-and-sum after pairwise SRP-PHAT direction scoring. MVDR/LCMV remain research candidates, not shipped modes. See [processor details](processor/README.md) for direction conventions, constraints and Sipeed examples.

The processor checks expected free space, reserves 8 GiB by default, checks while writing and uses partial files before finalization. It requires enough extra space for all requested output formats. Configure `--reserve-gib` only for a deliberate scratch test or a larger data drive; it is not a filesystem quota. A canceled run is marked canceled; a failed run is marked failed. `--resume` on a failed/canceled output verifies source hash, version and settings, then restarts the file into a new sibling directory while preserving the old attempt. Synthetic tests cover two sources, first/last WAV frames, codecs, failures and no-overwrite behavior. Field accuracy, channel mapping and recorder power-cycle behavior remain untested. See [processor details](processor/README.md), [SECURITY.md](SECURITY.md) and [the research review](docs/RESEARCH.md).

### Recorder bench status

A supervised SongBeam 3.1 / Teensy 4.1 USB test produced three readable 44.1 kHz, 16-bit, four-channel WAVs from one-minute settings. Their lengths were 59.849, 58.863 and 59.788 seconds. The recording timer was starting before the SD card's variable file preallocation, which directly explains the shortened nominal capture window. Audio memory also reached the entire 120-block allocation during the second file. A timer/512-block/batched-write revision produced two files at about 59.997 seconds but later met a 587 ms SD write stall and reported lost capture time. A 384 KiB internal-RAM ring in the audio update preserved later files through a 1.01 s SD stall and explicitly reported overflow, but the tested Samsung EVO MB-MP64D card also produced a >3 s stall, a short write and repeated file-open failures. The card's exFAT metadata checked clean after removal; that does not validate it for real-time recording. The RTC failed multiple power-off retention checks. **Continuous field recording remains unverified and this card should not be used unattended.** The later USB/MTP build below passed short transport/file tests, not SD or battery endurance. Raw recordings and private bench details are kept outside this public repository.

The latest source also latches storage or capture-integrity failures for that power session: autonomous recording stops, `STATUS` reports `FATAL=1` with an SD error code when available, and an incomplete WAV receives a `.failed` marker if the card still accepts writes. This behavior compiles but needs a controlled hardware fault test before being treated as verified.

A comparison with a freshly formatted 32 GB SanDisk Ultra on the same SongBeam/Teensy assembly passed ten consecutive one-minute, four-channel captures. There were zero reported dropped ring blocks, missing input blocks or SD controller errors; the worst measured write was 19.195 ms. All ten WAVs had consistent headers and frame counts, durations of 59.997–60.000 seconds, four varying unclipped channels, and stable checksums over repeat MTP readback. This supports using the SanDisk for further controlled tests; it does not establish long-term media health or field reliability. RTC retention failed again in a subsequent power cycle: both the active RTC and the low-power backup RTC returned to the Teensy core's January 2019 fallback. No firmware upload or time-setting command occurred between the baseline and the failed readback. The user clarified that the 3.25 V measurement was on the SongBeam side of the connector and confirmed that the Teensy RTC header was missing. After fitting the three-pin header, an RTC-only diagnostic retained both clocks through a measured 47-second USB power-off interval, within one second of the host UTC clock. This supports the missing connection as the cause of the observed backup failure. After restoring the recording firmware, one additional one-minute capture passed with zero reported drops or SD errors, a consistent 59.997-second four-channel WAV and matching repeat MTP readback checksums. The restored recorder then retained both clocks through a measured 122-second power-off interval, within one second of host UTC, and restarted with healthy SD status in USB idle. Longer unpowered intervals remain to be checked. Autonomous battery operation remains a separate gate.

The current file rotation stops capture between files and includes about 1.5 seconds of deliberate delays plus card preparation time. The above checks establish capture integrity inside each file; gapless file rotation is a useful pending improvement. The board's TPL5110 power cycle can also introduce restart gaps independently of software rotation.

`STATUS` reports the configured device ID, file length, active RTC epoch, backup RTC epoch and backup-clock enable state. These values help distinguish loss of VBAT-backed time from startup synchronization. SD diagnostics retain the controller's full 32-bit error data rather than the SdFat convenience wrapper's truncated value, so a replacement-card test can preserve useful failure evidence.

### Experimental USB modes

The composite firmware build uses Teensyduino's `Serial + MTP Disk (Experimental)` USB type. When the host enumerates USB, the firmware waits for a USB Serial command and does not automatically write to SD. `STATUS` reports basic readiness, `TIME <UTC epoch>` sets the Teensy RTC, `RECORD 1` writes one four-channel SD file, `STREAM 4` and `STREAM 2` start framed PCM streams to `songbeam-usb-capture`, and `STOP` ends a stream. `STREAM 2` provisionally averages A1+A2 for left and A3+A4 for right at half gain; verify physical mapping and acoustic effect before relying on it. `CARD` exposes the card through MTP only after recording has stopped. The host must safely finish/eject MTP transfers and power-cycle to leave card mode; the sketch will not resume recording in that session. MTP appears as a portable device rather than a block-mounted disk. Neither PCM stream appears as a standard USB sound card. A device powered without an enumerated USB host starts four-channel SD recording by default; `AutoRecordOnBoot: 0` in `config.txt` restores scheduled mode. See the [requirements and acceptance gates](docs/FIRMWARE_REQUIREMENTS.md). A 75-second four-channel stream during fixed-phone bird playback and an eight-second stereo stream passed sequence/CRC and reported-loss checks; MTP file operations also passed on the physical Teensy, but continuous recording, battery power and acoustic channel mapping have not passed. RTC retention passed the short post-repair checks described above.

For local USB control in the source checkout, install the optional `usb` extra and run `uv run --directory processor --extra usb songbeam-usb-control /dev/ttyACM0 status` or replace `status` with `card` or `time`. `card` is exclusive until safe host unmount and power cycle. The computer may show the card as a portable device; it is not a block disk for `fsck` or formatting. The tested MTP implementation creates `mtpindex.dat` on the card even when the host only browses.

## Attribution and licensing

Retain the original `LICENSE.md` (CC BY 4.0) and author notices for inherited SongBeam material. Credit individual contributions with commit provenance. [DD4WH's fork](https://github.com/DD4WH/SongBeam) by Frank Dziock is a useful reference for real-time FIR/FFT beamforming and configurable clocks; [MarcosQOliva's fork](https://github.com/MarcosQOliva/SongBeam) explores new hardware and eight-channel recording. Their code requires review and attribution before incorporation.

Luscinia source carries GPL 2.0 notices; do not imply that it is covered by SongBeam's CC license. A license for newly written processor code must be documented before its first publication. BirdNET-Analyzer source is MIT, while its model weights have separate CC BY-NC-SA 4.0 terms; do not bundle or relicense weights. Record all incorporated dependency and code licenses in a component-level notice file.

Original project/publication information: [CuCo SongBeam](https://www.cuco.group/songbeam), [upstream repository](https://github.com/lzandberg/SongBeam), and the publication linked from [LabMaker](https://www.labmaker.org/products/songbeam). Publish performance claims only with a stated baseline, dataset, method and limitations.
