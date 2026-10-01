# SongBeam — recorder and proposed offline processing extensions

SongBeam is an open bioacoustic recorder created by **Lies Zandberg and Robert Lachlan**, associated with Royal Holloway University of London. This is [a fork of the original SongBeam project](https://github.com/lzandberg/SongBeam). The repository retains its hardware designs, enclosure and Teensy recording sketch, and adds an independent offline processor. Original project documentation is at [CuCo](https://www.cuco.group/songbeam); hardware is distributed by [LabMaker](https://www.labmaker.org/products/songbeam).

## Original capabilities

The reviewed design uses a Teensy 4.1 and four IM69D130 microphones with two ADAU7002 converters. The stock sketch schedules recordings using a microSD `config.txt` and writes nominal 44.1 kHz, 16-bit, four-channel WAV audio. Offline directional inspection is provided by [Luscinia](https://github.com/rflachlan/Luscinia). The stock sketch does not produce a beamformed audio file or network stream.

Upstream baseline: `f1ee97e37906403cce5ea79b3501abc3c4fa7c52`. Code review identified recorder header/tail-write problems and a delay-estimation/export issue in Luscinia `v2.22.12.01.01`. Treat source availability as distinct from validated field operation. In the reviewed Luscinia release, **Save Sound exports original audio**, not the processed beamforming result.

## Extension status

These are proposed changes. They are not implemented by this documentation commit; update each row as its acceptance tests pass.

| Feature | Status | Practical purpose |
| --- | --- | --- |
| Correct WAV header reservation, interleaved final buffers and checked writes | Compiles; physical bench validation pending | Preserve channel alignment and detect SD failures. |
| Strict input validation and explicit channel map | Implemented for four-channel PCM16 WAV; physical channel mapping unverified | Reject malformed files and report dead/clipped channels. |
| Streaming mono beamformed exporter | Implemented and synthetic-tested | Process long recordings with bounded memory and reproducible output. |
| One automatic beam and manually selected directions | Implemented and synthetic-tested | Improve a chosen calling direction with an inspectable fallback. |
| Up to two tracked directional exports | Experimental, synthetic-tested | Make overlapping callers easier to review when spatially resolvable. |
| WAV, FLAC and MP3 output choices | Implemented and synthetic-tested | Support analysis, archival storage and listening. |
| BirdNET input/result validation and failed-job restart | Implemented for files and flat mono batches | Prevent silent downmixing and false-success jobs. |
| Privacy controls, ignore rules and security policy | Active in the fork; see `SECURITY.md` | Keep field data, secrets and private site information out of a public fork. |

## Intended processing workflow

Validate original four-channel WAV → apply calibrated channel mapping → estimate stable direction(s) → export one or more mono directional tracks → optionally classify lossless tracks with BirdNET → review results alongside a single original channel. Preserve original WAVs and a manifest linking every derivative to source, parameters and software versions.

The array is linear, with reviewed microphone centers at 0, 45, 75 and 120 mm. Direction is measured relative to that axis; front/back and other equal-projection ambiguities remain. Separate tracks can retain other birds and noise. A track is not an identified individual bird, and multiple exported beams do not establish a bird count. Same-direction callers may remain an unresolved mixture.

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

`--channel-map` is explicit because the file-to-physical-microphone wiring has not been verified for this user's board. Optional `--gains`, `--polarities`, `--fixed-delays-samples` and `--calibration-id` record and apply four-channel calibration; defaults are identity and require physical verification. Angles are from array broadside and retain front/back ambiguity. The input validator rejects missing/extra RIFF bytes and unsupported four-channel formats; it never repairs the source. Each new output directory contains separate full-timeline mono tracks plus `manifest.json` with hashes, processing settings, direction estimates and activity. Existing output directories are refused. Source WAV stays unchanged. Track 2 is emitted only when three consecutive analysis blocks support a distinct second direction; uncertain intervals are marked inactive. The two-track result is experimental and may contain bleed or track swaps. The active algorithm is normalized delay-and-sum after all-six-pair SRP-PHAT direction scoring. MVDR/LCMV remain research candidates, not shipped modes.

The processor checks expected free space, reserves 8 GiB by default, checks while writing and uses partial files before finalization. It requires enough extra space for all requested output formats. Configure `--reserve-gib` only for a deliberate scratch test or a larger data drive; it is not a filesystem quota. A canceled run is marked canceled; a failed run is marked failed. `--resume` on a failed/canceled output verifies source hash, version and settings, then restarts the file into a new sibling directory while preserving the old attempt. Synthetic tests cover two sources, first/last WAV frames, codecs, failures and no-overwrite behavior. Field accuracy, channel mapping and recorder power-cycle behavior remain untested. See [processor details](processor/README.md), [SECURITY.md](SECURITY.md) and [the research review](docs/RESEARCH.md).

## Attribution and licensing

Retain the original `LICENSE.md` (CC BY 4.0) and author notices for inherited SongBeam material. Credit individual contributions with commit provenance. [DD4WH's fork](https://github.com/DD4WH/SongBeam) by Frank Dziock is a useful reference for real-time FIR/FFT beamforming and configurable clocks; [MarcosQOliva's fork](https://github.com/MarcosQOliva/SongBeam) explores new hardware and eight-channel recording. Their code requires review and attribution before incorporation.

Luscinia source carries GPL 2.0 notices; do not imply that it is covered by SongBeam's CC license. A license for newly written processor code must be documented before its first publication. BirdNET-Analyzer source is MIT, while its model weights have separate CC BY-NC-SA 4.0 terms; do not bundle or relicense weights. Record all incorporated dependency and code licenses in a component-level notice file.

Original project/publication information: [CuCo SongBeam](https://www.cuco.group/songbeam), [upstream repository](https://github.com/lzandberg/SongBeam), and the publication linked from [LabMaker](https://www.labmaker.org/products/songbeam). Publish performance claims only with a stated baseline, dataset, method and limitations.
