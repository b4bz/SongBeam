# SongBeam — recorder and proposed offline processing extensions

SongBeam is an open bioacoustic recorder created by **Lies Zandberg and Robert Lachlan**, associated with Royal Holloway University of London. This README is prepared for a fork of [the original SongBeam project](https://github.com/lzandberg/SongBeam). The repository contains its hardware designs, enclosure and Teensy recording sketch. Original project documentation is at [CuCo](https://www.cuco.group/songbeam); hardware is distributed by [LabMaker](https://www.labmaker.org/products/songbeam).

## Original capabilities

The reviewed design uses a Teensy 4.1 and four IM69D130 microphones with two ADAU7002 converters. The stock sketch schedules recordings using a microSD `config.txt` and writes nominal 44.1 kHz, 16-bit, four-channel WAV audio. Offline directional inspection is provided by [Luscinia](https://github.com/rflachlan/Luscinia). The stock sketch does not produce a beamformed audio file or network stream.

Upstream baseline: `f1ee97e37906403cce5ea79b3501abc3c4fa7c52`. Code review identified recorder header/tail-write problems and a delay-estimation/export issue in Luscinia `v2.22.12.01.01`. Treat source availability as distinct from validated field operation. In the reviewed Luscinia release, **Save Sound exports original audio**, not the processed beamforming result.

## Extension status

These are proposed changes. They are not implemented by this documentation commit; update each row as its acceptance tests pass.

| Feature | Status | Practical purpose |
| --- | --- | --- |
| Correct WAV header reservation, interleaved final buffers and checked writes | Planned | Preserve channel alignment and detect SD failures. |
| Input validation and channel calibration | Planned | Detect malformed files, swapped/dead microphones and incorrect phase alignment. |
| Streaming mono beamformed exporter | Planned | Process long recordings with bounded memory and reproducible output. |
| One automatic beam and manually selected directions | Planned | Improve a chosen calling direction with an inspectable fallback. |
| Up to two tracked directional exports | Experimental plan | Make overlapping callers easier to review when spatially resolvable. |
| WAV, FLAC and MP3 output choices | Planned | Support analysis, archival storage and listening. |
| BirdNET input/result validation and resumable jobs | Planned | Prevent silent downmixing, false-success jobs and repeated work. |
| Privacy controls, ignore rules and security policy | Drafted; enforcement checks pending | Keep field data, secrets and private site information out of a public fork. |

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

No new processor installation or CLI commands are available yet. Until implemented, follow upstream recorder instructions only after hardware/firmware validation. Keep recordings and model weights outside this checkout. See [SECURITY.md](SECURITY.md) for repository and runtime policy.

## Attribution and licensing

Retain the original `LICENSE.md` (CC BY 4.0) and author notices for inherited SongBeam material. Credit individual contributions with commit provenance. [DD4WH's fork](https://github.com/DD4WH/SongBeam) by Frank Dziock is a useful reference for real-time FIR/FFT beamforming and configurable clocks; [MarcosQOliva's fork](https://github.com/MarcosQOliva/SongBeam) explores new hardware and eight-channel recording. Their code requires review and attribution before incorporation.

Luscinia source carries GPL 2.0 notices; do not imply that it is covered by SongBeam's CC license. A license for newly written processor code must be documented before its first publication. BirdNET-Analyzer source is MIT, while its model weights have separate CC BY-NC-SA 4.0 terms; do not bundle or relicense weights. Record all incorporated dependency and code licenses in a component-level notice file.

Original project/publication information: [CuCo SongBeam](https://www.cuco.group/songbeam), [upstream repository](https://github.com/lzandberg/SongBeam), and the publication linked from [LabMaker](https://www.labmaker.org/products/songbeam). Publish performance claims only with a stated baseline, dataset, method and limitations.
