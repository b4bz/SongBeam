# SongBeam fork and processing research

Checked 2026-10-01 against public GitHub branch/compare APIs, fork source, primary software documentation and research papers. This is a design recommendation, not a measured field improvement.

## Public forks

| Fork | Verified branch state | Useful work | Adoption decision |
| --- | --- | --- | --- |
| [DD4WH/SongBeam](https://github.com/DD4WH/SongBeam) | `main` at `c917b711abe843bf9af79ff68d5dbcdb070a3620`, 9 commits ahead of original | New `code/0_SongBeam256.ino`: 128-sample blocks, CMSIS FFT convolution, fractional-delay FIRs, geometry `[0,45,75,120]` mm, averaged four-channel output, FAT timestamps and actual SAI1/SAI2 clock configuration. Author reports testing 24/48 kHz. | Best source of implementation ideas. Credit Frank Dziock. Port individual changes with tests. Fixed steering angle, MQS output, independent RMS gains and hard limiting make this a listening prototype; recording and live processing are alternative modes. It retains the original malformed final buffer/counting and filename overwrite patterns. Do not adopt wholesale or call it a validated field recorder. |
| [MarcosQOliva/SongBeam](https://github.com/MarcosQOliva/SongBeam) | `main` at `cae08af1fa1acd3cac7e799ca7a8e27e6809d6fd`, 31 commits ahead | Hardware redesign/daughterboards; `Teensy_farms-8mic` and `audiodebugger_m8` use eight-channel `AudioInputI2SOct`. Debugger reserves 44 WAV-header bytes before audio; serial record/stop supports bench diagnosis. | Borrow the header-reservation and diagnostics ideas. Different capture hardware/channel layout; not a drop-in firmware upgrade for the current four-mic board. Main eight-channel sketch still has problematic tail writes; debugger stops without draining remaining queues. No inspected multi-bird separation implementation. |
| [WMXZ-EU/SongBeam](https://github.com/WMXZ-EU/SongBeam) | `main` identical to original `f1ee97e37906403cce5ea79b3501abc3c4fa7c52` | No divergent default-branch changes | No additional implementation to adopt. |

Inspected all listed branches of the three direct public SongBeam forks; each had one branch. GitHub's Marcos comparison reached its 300-file listing cap, so code paths were also fetched directly; this is not a complete audit of all hardware assets or history.

For the seven direct public Luscinia forks, default-branch comparisons found no commits ahead of upstream `a08dcb7`: ChenHH730 identical; Guru-learn, frebur, jkbeam, Adiaba and codeaudit three commits behind; fzyukio eight behind. Five also expose the same `gh-pages` head `8332321`; those are documentation branches, not a newer processor. This search does not cover private forks or every unrelated repository with copied code.

## Additional recorder finding

The original `startRecording()` opens/preallocates the file and immediately writes PCM; there is no initial 44-byte header reservation. Finalization seeks to zero and writes the header over that PCM. At four channels × two bytes, 44 bytes is 5.5 frames, so the remaining declared data can begin halfway through an original frame, affecting channel identity and relative alignment throughout the file. This is a source-level finding, not a claim about an inspected physical recorder. The Marcos debugger explicitly reserves the header. Include this regression alongside tail interleaving/counts before firmware use; do not automatically reorder or repair unknown legacy field files.

## Recommended algorithms

| Approach | Purpose and tradeoff | Priority |
| --- | --- | --- |
| Calibrated fractional-delay delay-and-sum | Predictable reference beam; normalized weights preserve headroom. Apply bounded FIR/FFT convolution or validated STFT weights with overlap-add, carrying state between chunks. | Required baseline and fallback. |
| All-pair GCC-PHAT / SRP-PHAT | Use all six microphone pairs to score possible direction cosines. Add energy/coherence weighting so PHAT does not amplify noise-only bins; retain multiple peaks. | Preferred automatic steering candidate. Compare with corrected Luscinia estimator. |
| Loaded MVDR, then optional LCMV | Suppress competing direction(s) while preserving target response. Needs reliable steering/covariance, diagonal loading, a white-noise-gain limit and a delay-and-sum fallback; bad estimates can suppress the wanted call. | Experimental second stage after two-source benchmark. |
| MUSIC / NormMUSIC | Useful independent localization comparator, but source-count/covariance assumptions and correlated reflections matter with four sensors. | Benchmark candidate, not default. |
| SVD-PHAT / hierarchical search | Reduce search work; published multi-source SVD-PHAT exists. Four sensors and a one-dimensional grid are already small. | Only if profiling shows localization is the bottleneck. |
| ODAS / pyroomacoustics | ODAS provides optimized C localization/tracking/separation; pyroomacoustics provides simulation and beamformer references. | Evaluate as independent baselines; do not add a service or mandatory framework merely to expand features. |

Implementation should start in a small Python/NumPy/SciPy processor. Decode each block once, use float32 audio, accumulate covariance safely, reuse buffers/FFT plans and steering vectors, share the four input FFTs across beams, and bound queues and worker count. One inverse transform per output is sufficient for an STFT implementation. Avoid whole-file tenfold upsampling, repeated decode/resample per beam, nested BLAS/process oversubscription, and reloading BirdNET for every short clip. Optimize only measured hot paths; keep an explicit fractional-delay reference for numerical tests.

Use separate bands for direction estimation and exported audio. Candidate localization bands should include 2–6 and 2–8 kHz, plus user-specified call bands; low-frequency callers must not be discarded because the default localization band has no evidence. Preserve the native sample rate and the wanted audio band. A real 48 kHz derivative may be produced once for BirdNET. Do not change only a WAV header or apply independent time-varying AGC to channels before localization. Calibrate channel gain, polarity, fixed delay and microphone mapping; retain calibration uncertainty. Temperature-dependent sound speed can be an optional scalar, not a new live sensor dependency.

## Multiple simultaneous birds

The 120 mm linear array measures a direction cosine along its axis. It cannot uniquely recover three-dimensional bearing, resolve front/back symmetry, distinguish two sources with the same projected delay, or infer range from a far-field model. Narrowband spatial ambiguities and strong/weak source imbalance also matter. Nonuniform 45/30/45 mm spacing does not eliminate ambiguity.

Suggested initial scope: up to **two simultaneous directional tracks**, configurable cap with no promise of one output per bird. Find stable distinct peaks across frequency bands and time, suppress peaks consistent with sidelobes, associate tracks using projected direction plus continuity, and allow the answer “unresolved mixture.” Use frequency-dependent separation criteria based on the measured/simulated array response rather than a fixed angle threshold. Track identities must survive brief gaps without switching to the loudest caller; log ambiguity at crossings. Let a reviewer request fixed directions when automatic tracking fails.

Each accepted track gets its own mono export on a common sample timeline, accompanied by activity/confidence intervals. If an event clip is requested, include its original start sample and pre/post-roll. Do not silently concatenate calls, equate beam count with bird count, or name a track after a species without a separate classifier/human label. Run BirdNET on each lossless track and one original-channel control; link likely duplicate detections across beams without merging species predictions into physical individual identities.

An ideal free-field calculation for positions `[0,.045,.075,.120]` metres, sound speed 343 m/s and equal weights gives full broadside −3 dB widths of **60.2° at 2 kHz, 29.0° at 4 kHz, and 14.4° at 8 kHz**. These are monochromatic main-lobe widths, not a guaranteed two-source resolution or field performance. High-frequency sidelobes, reflections, wind, mapping errors and channel mismatch can defeat apparent separation. Calculation: `abs(mean(exp(2j*pi*f*positions*sin(theta)/343)))`; find the first crossing of `1/sqrt(2)` on either side of broadside.

## Output formats and defaults

| Format | Suggested option | Use and cost |
| --- | --- | --- |
| WAV | Native-rate mono PCM24 default for a derived beam; PCM16 compatibility option; float32 optional intermediate. Split or RF64 before RIFF size limits. | Uncompressed editing/interchange. At 44.1 kHz: PCM16 ≈317.5 MB/hour/beam; PCM24 ≈476.3 MB; float32 ≈635.0 MB. |
| FLAC | Native-rate mono PCM24, compression level 5 default; levels 0–8 selectable | Preferred stored derivative. Level affects encoding effort/size, not decoded PCM quality. No fixed bitrate or guaranteed compression ratio. FLAC is lossless relative to its quantized PCM input, not the internal floating-point calculations. |
| MP3 | Mono LAME **192 kb/s CBR** listening default; 128 preview; 256 optional higher bitrate. Optional VBR `-q:a 2` with measured, variable file size. | Listening/sharing derivative: 128/192/256 kb/s ≈57.6/86.4/115.2 MB/hour/beam. Not the analysis master. Encoder may low-pass; bitrate does not guarantee preservation of bird-call detail. |

Keep original four-channel WAV unchanged. Optional four-channel FLAC archival conversion must decode to identical PCM with channel map retained; it does not justify deleting the source. A 24-bit derivative avoids repeated coarse quantization but does not create additional recorded resolution. Apply one documented gain policy across a run and beams, track clipping, dither when reducing bit depth, and never silently rescale each beam to appear equally loud. Use WAV/FLAC for measurement and BirdNET; decode/check MP3 duration and encoder delay/padding for aligned playback. Export multiple formats from the same processed signal, not through successive lossy conversions. Budget all beam/format outputs using worst-case PCM and a reserve.

## Primary sources

- [DD4WH pinned sketch](https://github.com/DD4WH/SongBeam/blob/c917b711abe843bf9af79ff68d5dbcdb070a3620/code/0_SongBeam256.ino) and [fork comparison](https://github.com/lzandberg/SongBeam/compare/main...DD4WH:SongBeam:main).
- [Marcos eight-mic debugger](https://github.com/MarcosQOliva/SongBeam/blob/cae08af1fa1acd3cac7e799ca7a8e27e6809d6fd/code/audiodebugger_m8/audiodebugger_m8.ino).
- [SRP tutorial and implementation review](https://arxiv.org/html/2405.02991v2), [multi-source SVD-PHAT paper](https://arxiv.org/abs/1906.11913).
- [Pyroomacoustics beamforming API](https://pyroomacoustics.readthedocs.io/en/stable/pyroomacoustics.beamforming.html), [SRP API](https://pyroomacoustics.readthedocs.io/en/stable/pyroomacoustics.doa.srp.html), [ODAS](https://github.com/introlab/odas).
- [Xiph FLAC options](https://www.xiph.org/flac/documentation_tools_flac.html), [FFmpeg codec options](https://ffmpeg.org/ffmpeg-codecs.html#libmp3lame).
- [Git ignore behavior](https://docs.github.com/en/get-started/git-basics/ignoring-files), [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use), [fork visibility](https://docs.github.com/en/pull-requests/reference/forks).
