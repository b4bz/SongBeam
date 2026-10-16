"""Bounded two-pass localization and lossless/lossy output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.fft import next_fast_len

from . import __version__
from .audio import HALO, beam_fft, inspect, read_block
from .calibration import load_calibration
from .localize import track_blocks
from .profiles import load_profile
from .spatial import candidates, steering_samples


@dataclass(frozen=True)
class Settings:
    channel_map: tuple[int, ...] | None = None
    source_profile: str = "songbeam-4"
    input_layout: str | None = None
    calibration_file: str | None = None
    calibration_sha256: str | None = None
    calibration_id: str = "unverified-board"
    orientation_deg: float | None = None
    known_elevation_deg: float | None = None
    source_sector: tuple[float, float] | None = None
    gains: tuple[float, ...] | None = None
    polarities: tuple[int, ...] | None = None
    fixed_delays_samples: tuple[float, ...] | None = None
    manual_angles: tuple[float, ...] = ()
    manual_azimuths: tuple[float, ...] = ()
    manual_elevation: float = 0.0
    auto_beams: int = 1
    formats: tuple[str, ...] = ("flac",)
    wav_subtype: str = "PCM_24"
    flac_subtype: str = "PCM_24"
    mp3_bitrate: int = 192
    mp3_vbr_quality: int | None = None
    flac_level: int = 5
    block_seconds: float = 0.5
    analysis_window_seconds: float = 0.1
    analysis_hop_seconds: float = 0.05
    fmin: float = 2000
    fmax: float = 8000
    reserve_gib: float = 8.0
    speed: float = 343.0

    def validate(self) -> None:
        profile = load_profile(self.source_profile)
        n = len(profile.positions_m)
        if self.channel_map is not None and (
            len(self.channel_map) != n
            or len(set(self.channel_map)) != n
            or any(i < 0 or i >= 32 for i in self.channel_map)
        ):
            raise ValueError(
                "channel_map must select distinct valid channels for the profile"
            )
        if (
            self.gains is not None
            and (
                len(self.gains) != n
                or any(not math.isfinite(x) or x <= 0 or x > 4 for x in self.gains)
            )
            or self.polarities is not None
            and (
                len(self.polarities) != n
                or any(x not in (-1, 1) for x in self.polarities)
            )
            or self.fixed_delays_samples is not None
            and (
                len(self.fixed_delays_samples) != n
                or any(
                    not math.isfinite(x) or abs(x) > 32
                    for x in self.fixed_delays_samples
                )
            )
        ):
            raise ValueError(
                "calibration vectors must match selected microphones and be bounded"
            )
        if (
            self.auto_beams not in (1, 2)
            or (self.manual_angles or self.manual_azimuths)
            and self.auto_beams != 1
        ):
            raise ValueError("choose one/two automatic beams or manual angles")
        if self.manual_angles and (profile.rank != 1 or self.manual_azimuths):
            raise ValueError("manual-angle applies only to a linear array")
        if any(not math.isfinite(a) or abs(a) > 90 for a in self.manual_angles):
            raise ValueError("manual angles must be within ±90 degrees from broadside")
        if (
            any(not math.isfinite(a) or not 0 <= a < 360 for a in self.manual_azimuths)
            or not math.isfinite(self.manual_elevation)
            or abs(self.manual_elevation) > 90
        ):
            raise ValueError("manual source azimuth/elevation out of range")
        if self.orientation_deg is not None and (
            not math.isfinite(self.orientation_deg)
            or not 0 <= self.orientation_deg < 360
        ):
            raise ValueError(
                "orientation must be degrees clockwise from north for local +X"
            )
        if self.known_elevation_deg is not None and (
            not math.isfinite(self.known_elevation_deg)
            or abs(self.known_elevation_deg) > 90
        ):
            raise ValueError("known elevation must be within ±90 degrees")
        if self.source_sector is not None and (
            len(self.source_sector) != 2
            or any(not math.isfinite(v) or not 0 <= v < 360 for v in self.source_sector)
        ):
            raise ValueError("source sector must contain two local azimuths in [0,360)")
        if self.source_sector is not None and self.known_elevation_deg is None:
            raise ValueError("source sector needs independently known elevation")
        if (
            not self.formats
            or set(self.formats) - {"wav", "flac", "mp3"}
            or len(set(self.formats)) != len(self.formats)
        ):
            raise ValueError("formats must be unique WAV, FLAC or MP3")
        if self.wav_subtype not in {
            "PCM_16",
            "PCM_24",
            "FLOAT",
        } or self.flac_subtype not in {"PCM_16", "PCM_24"}:
            raise ValueError("unsupported PCM subtype")
        if self.mp3_bitrate not in {128, 192, 256} or not 0 <= self.flac_level <= 8:
            raise ValueError("unsupported codec quality")
        if self.mp3_vbr_quality is not None and not 0 <= self.mp3_vbr_quality <= 9:
            raise ValueError("MP3 VBR quality must be 0–9")
        if not 0.2 <= self.block_seconds <= 2 or not 0 <= self.fmin < self.fmax:
            raise ValueError("invalid analysis window or band")
        if (
            not 0.02
            <= self.analysis_hop_seconds
            <= self.analysis_window_seconds
            <= self.block_seconds
        ):
            raise ValueError("analysis hop/window must fit processing block")
        if not 300 <= self.speed <= 370 or self.reserve_gib < 0:
            raise ValueError("invalid sound speed or reserve")


class OutputSet:
    def __init__(
        self, root: Path, track: int, rate: int, frames: int, settings: Settings
    ):
        self.root, self.track, self.rate = root, track, rate
        self.settings = settings
        self.dither = np.random.default_rng(0x5342 + track)
        self.peak = 0.0
        self.clipped_frames = 0
        self.writers: dict[str, sf.SoundFile | subprocess.Popen] = {}
        self.paths: dict[str, Path] = {}
        if "mp3" in settings.formats and shutil.which("ffmpeg") is None:
            raise RuntimeError("FFmpeg with libmp3lame is required for MP3")
        for fmt in settings.formats:
            final = root / f"track-{track:03d}.{fmt}"
            if final.exists():
                raise FileExistsError(final)
            temp = root / f"track-{track:03d}.{fmt}.part"
            if temp.exists():
                raise FileExistsError(temp)
        for fmt in settings.formats:
            temp = root / f"track-{track:03d}.{fmt}.part"
            self.paths[fmt] = temp
            if fmt == "mp3":
                command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-f",
                    "f32le",
                    "-ar",
                    str(rate),
                    "-ac",
                    "1",
                    "-i",
                    "pipe:0",
                    "-c:a",
                    "libmp3lame",
                ]
                command += (
                    ["-b:a", f"{settings.mp3_bitrate}k"]
                    if settings.mp3_vbr_quality is None
                    else ["-q:a", str(settings.mp3_vbr_quality)]
                )
                command += ["-f", "mp3", str(temp)]
                self.writers[fmt] = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
                )
            else:
                subtype = (
                    settings.wav_subtype if fmt == "wav" else settings.flac_subtype
                )
                wav_bytes = self.settings.wav_subtype if fmt == "wav" else None
                long_wav = (
                    fmt == "wav"
                    and frames * ({"PCM_16": 2, "PCM_24": 3, "FLOAT": 4}[wav_bytes])
                    > 0xFFFFFFFF - 44
                )
                self.writers[fmt] = sf.SoundFile(
                    str(temp),
                    mode="x",
                    samplerate=rate,
                    channels=1,
                    format="RF64" if long_wav else fmt.upper(),
                    subtype=subtype,
                    compression_level=settings.flac_level / 8
                    if fmt == "flac"
                    else None,
                )

    def write(self, audio: np.ndarray) -> None:
        self.peak = max(self.peak, float(np.max(np.abs(audio))))
        self.clipped_frames += int(np.count_nonzero(np.abs(audio) >= 1.0))
        # Quantize each bit depth once so WAV and FLAC receive identical PCM.
        quantized: dict[int, np.ndarray] = {}
        for fmt, writer in self.writers.items():
            if fmt == "mp3":
                assert isinstance(writer, subprocess.Popen) and writer.stdin is not None
                writer.stdin.write(np.asarray(audio, dtype="<f4").tobytes())
            else:
                subtype = (
                    self.settings.wav_subtype
                    if fmt == "wav"
                    else self.settings.flac_subtype
                )
                if subtype == "FLOAT":
                    writer.write(audio)
                else:
                    bits = 16 if subtype == "PCM_16" else 24
                    if bits not in quantized:
                        scale = 1 << (bits - 1)
                        tpdf = self.dither.random(len(audio)) - self.dither.random(
                            len(audio)
                        )
                        integer = np.rint(
                            np.clip(audio * scale + tpdf, -scale, scale - 1)
                        ).astype(np.int32)
                        quantized[bits] = integer << (32 - bits)
                    writer.write(quantized[bits])

    def close(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for fmt, writer in self.writers.items():
            if fmt == "mp3":
                assert isinstance(writer, subprocess.Popen) and writer.stdin is not None
                writer.stdin.close()
                error = (
                    writer.stderr.read().decode("utf-8", "replace")
                    if writer.stderr
                    else ""
                )
                if writer.wait() != 0:
                    raise RuntimeError(f"MP3 encoder failed: {error[:500]}")
            else:
                writer.flush()
                writer.close()
            temp = self.paths[fmt]
            with temp.open("rb") as stream:
                os.fsync(stream.fileno())
            final = self.root / f"track-{self.track:03d}.{fmt}"
            # Hard-link creation fails if another writer created the final name.
            os.link(temp, final)
            temp.unlink()
            result[fmt] = {
                "name": final.name,
                "bytes": final.stat().st_size,
                "sha256": _sha256(final),
            }
        return result

    def abort(self) -> None:
        for writer in self.writers.values():
            if isinstance(writer, subprocess.Popen):
                if writer.poll() is None:
                    writer.terminate()
                if writer.stdin and not writer.stdin.closed:
                    writer.stdin.close()
                writer.wait(timeout=5)
                if writer.stderr:
                    writer.stderr.close()
            elif not writer.closed:
                writer.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _calibrate(block: np.ndarray, settings: Settings) -> np.ndarray:
    n = block.shape[1]
    gains = np.asarray(
        settings.gains if settings.gains is not None else (1.0,) * n
    ) * np.asarray(settings.polarities if settings.polarities is not None else (1,) * n)
    output = block * gains[None, :]
    shifts = np.asarray(
        settings.fixed_delays_samples
        if settings.fixed_delays_samples is not None
        else (0.0,) * n
    )
    if not np.any(shifts):
        return output.astype("float32")
    size = next_fast_len(len(output) + 2 * HALO)
    spectrum = np.fft.rfft(output, n=size, axis=0)
    bins = np.fft.rfftfreq(size)
    phase = np.exp(2j * np.pi * bins[:, None] * shifts[None, :])
    return np.fft.irfft(spectrum * phase, n=size, axis=0)[: len(block)].astype(
        "float32"
    )


def _space_required(frames: int, tracks: int, rate: int, settings: Settings) -> int:
    # Reserve for a source-sized temporary plus all lossless outputs and double the
    # estimated lossy size. FLAC is budgeted at uncompressed PCM size.
    per_track = 0
    if "wav" in settings.formats:
        per_track += (
            frames * {"PCM_16": 2, "PCM_24": 3, "FLOAT": 4}[settings.wav_subtype]
        )
    if "flac" in settings.formats:
        per_track += frames * {"PCM_16": 2, "PCM_24": 3}[settings.flac_subtype]
    if "mp3" in settings.formats:
        per_track += math.ceil(frames * settings.mp3_bitrate * 1000 / 8 / rate) * 2
    return int(1.2 * tracks * per_track + 16 * 1024 * 1024)


def _space_guard(parent: Path, required: int, reserve_gib: float) -> None:
    free = shutil.disk_usage(parent).free
    if free < required + reserve_gib * 1024**3:
        raise OSError(
            f"Insufficient storage: need {required} bytes plus {reserve_gib:g} GiB reserve; have {free}"
        )


def _mount_guard(parent: Path, mount: Path | None) -> None:
    if mount is None:
        return
    if not mount.is_mount():
        raise OSError("Required data filesystem is not mounted")
    resolved = mount.resolve(strict=True)
    if not parent.resolve(strict=True).is_relative_to(resolved):
        raise ValueError("Output is outside the required data filesystem")
    if parent.stat().st_dev != resolved.stat().st_dev:
        raise OSError("Output filesystem differs from required mount")


def _resolve_output(input_path: Path, output: Path) -> tuple[Path, Path]:
    source = input_path.resolve(strict=True)
    parent = output.parent.resolve(strict=True)
    if output.is_symlink() or output.exists():
        raise FileExistsError("Output directory already exists or is a symlink")
    if source == output or source.is_relative_to(parent / output.name):
        raise ValueError("Output may not contain source")
    return source, parent / output.name


def _annotate_direction(hit: dict, profile, settings: Settings) -> dict:
    result = dict(hit)
    if profile.rank == 1:
        if settings.known_elevation_deg is None:
            result["compatible_local_bearings_deg"] = None
            return result
        axis = np.linalg.svd(
            np.asarray(profile.positions_m) - np.mean(profile.positions_m, axis=0),
            full_matrices=False,
        )[2][0]
        if axis[np.argmax(abs(axis))] < 0:
            axis = -axis
        if abs(axis[2]) > 1e-6:
            result["bearing_status"] = "linear_constraint_requires_horizontal_axis"
            return result
        horizontal = math.cos(math.radians(settings.known_elevation_deg))
        ratio = -hit["u"] / horizontal if horizontal > 1e-6 else float("inf")
        if abs(ratio) > 1.001:
            result["bearing_status"] = "constraint_inconsistent"
            result["compatible_local_bearings_deg"] = []
            return result
        axis_bearing = math.degrees(math.atan2(axis[1], axis[0]))
        delta = math.degrees(math.acos(max(-1, min(1, ratio))))
        bearings = sorted(
            {round((axis_bearing + sign * delta) % 360, 2) for sign in (-1, 1)}
        )
        if settings.source_sector is not None:
            start, end = settings.source_sector
            bearings = [
                b
                for b in bearings
                if (start <= b <= end if start <= end else b >= start or b <= end)
            ]
        result["compatible_local_bearings_deg"] = bearings
        result["bearing_status"] = (
            "constrained_unique_bearing"
            if len(bearings) == 1
            else "constraint_inconsistent"
            if not bearings
            else "constrained_ambiguous_bearing"
        )
        result["azimuth_deg"] = bearings[0] if len(bearings) == 1 else None
    if settings.orientation_deg is not None and result.get("azimuth_deg") is not None:
        result["compass_bearing_deg"] = round(
            (settings.orientation_deg + result["azimuth_deg"]) % 360, 2
        )
    return result


def _block_candidates(
    block: np.ndarray, rate: int, profile, settings: Settings
) -> list[dict]:
    """Retain brief calls using overlapping short localization windows."""
    window = max(256, round(rate * settings.analysis_window_seconds))
    hop = max(128, round(rate * settings.analysis_hop_seconds))
    clusters: list[dict] = []
    for start in range(0, len(block), hop):
        segment = block[start : start + window]
        if len(segment) < window // 2:
            break
        for hit in candidates(
            segment,
            rate,
            profile,
            settings.auto_beams,
            settings.fmin,
            settings.fmax,
            settings.speed,
        ):
            q = np.asarray(hit["direction_q"])
            nearest = min(
                clusters,
                key=lambda old: np.linalg.norm(np.asarray(old["direction_q"]) - q),
                default=None,
            )
            if (
                nearest is None
                or np.linalg.norm(np.asarray(nearest["direction_q"]) - q) > 0.24
            ):
                clusters.append(dict(hit, evidence_windows=1))
            else:
                nearest["evidence_windows"] += 1
                if hit["score"] > nearest["score"]:
                    nearest.update(hit)
    clusters.sort(key=lambda hit: (hit["evidence_windows"], hit["score"]), reverse=True)
    if clusters and clusters[0]["evidence_windows"] < 1:
        return []
    if settings.auto_beams > 1 and clusters:
        first = clusters[0]
        others = [
            hit
            for hit in clusters[1:]
            if hit["evidence_windows"] >= 3 or hit["score"] >= 0.75 * first["score"]
        ]
        clusters = [first, *others]
    return [
        _annotate_direction(hit, profile, settings)
        for hit in clusters[: settings.auto_beams]
    ]


def process(
    input_path: Path,
    output: Path,
    settings: Settings,
    retry_of: str | None = None,
    required_mount: Path | None = None,
) -> dict:
    settings.validate()
    requested_settings = settings
    profile = load_profile(settings.source_profile)
    source, destination = _resolve_output(input_path, output)
    _mount_guard(destination.parent, required_mount)
    raw_info = sf.info(str(source))
    layout_name, layout_channels, nominal_map = profile.layout(
        settings.input_layout, raw_info.channels
    )
    if settings.calibration_file is not None:
        calibration = load_calibration(
            Path(settings.calibration_file), profile, layout_name, raw_info.samplerate
        )
        if (
            settings.channel_map is not None
            or settings.gains is not None
            or settings.polarities is not None
            or settings.fixed_delays_samples is not None
        ):
            raise ValueError(
                "Choose a calibration file or individual calibration overrides"
            )
        settings = replace(settings, **calibration)
        settings.validate()
    mapping = settings.channel_map if settings.channel_map is not None else nominal_map
    if any(i >= layout_channels for i in mapping):
        raise ValueError("Selected microphone channel exceeds input layout")
    details = inspect(source, mapping, layout_channels)
    rate = int(details["sample_rate"])
    if not profile.rate_range[0] <= rate <= profile.rate_range[1]:
        raise ValueError("Sample rate outside profile range")
    frames = int(details["frames"])
    block_len = max(1024, round(settings.block_seconds * rate))
    count = math.ceil(frames / block_len)
    positions = np.asarray(profile.positions_m)
    aperture = float(
        np.max(np.linalg.norm(positions[:, None, :] - positions[None, :, :], axis=2))
    )
    halo = max(HALO, math.ceil(aperture * rate / settings.speed) + 48)
    inferred: list[list[dict]] = []
    if settings.manual_angles:
        axis = np.linalg.svd(
            np.asarray(profile.positions_m) - np.mean(profile.positions_m, axis=0),
            full_matrices=False,
        )[2][0]
        if axis[np.argmax(abs(axis))] < 0:
            axis = -axis
        tracks = [
            [
                {
                    "u": round(float(math.sin(math.radians(angle))), 3),
                    "direction_q": (axis * math.sin(math.radians(angle))).tolist(),
                    "score": None,
                    "bearing_status": "ambiguous_linear_projection",
                    "azimuth_deg": None,
                }
                for _ in range(count)
            ]
            for angle in settings.manual_angles
        ]
    elif settings.manual_azimuths:
        tracks = []
        for azimuth in settings.manual_azimuths:
            a, e = math.radians(azimuth), math.radians(settings.manual_elevation)
            q = [-math.cos(e) * math.cos(a), -math.cos(e) * math.sin(a), -math.sin(e)]
            entry = {
                "direction_q": q,
                "score": None,
                "azimuth_deg": azimuth,
                "bearing_status": "manually_selected",
            }
            if profile.rank == 1:
                axis = np.linalg.svd(
                    np.asarray(profile.positions_m)
                    - np.mean(profile.positions_m, axis=0),
                    full_matrices=False,
                )[2][0]
                if axis[np.argmax(abs(axis))] < 0:
                    axis = -axis
                entry["u"] = float(np.dot(q, axis))
            tracks.append([entry.copy() for _ in range(count)])
    else:
        with sf.SoundFile(str(source)) as stream:
            for index in range(count):
                start = index * block_len
                length = min(block_len, frames - start)
                block, first = read_block(stream, start, length, mapping, halo=halo)
                calibrated = _calibrate(block, settings)
                inferred.append(
                    _block_candidates(
                        calibrated[start - first : start - first + length],
                        rate,
                        profile,
                        settings,
                    )
                )
        tracks = track_blocks(inferred, settings.auto_beams)
    if settings.manual_angles or settings.manual_azimuths:
        tracks = [
            [_annotate_direction(hit, profile, settings) for hit in row]
            for row in tracks
        ]
    _space_guard(
        destination.parent,
        _space_required(frames, len(tracks), rate, settings),
        settings.reserve_gib,
    )
    destination.mkdir(mode=0o750)
    manifest = {
        "schema": 2,
        "status": "running",
        "processor_version": __version__,
        "source": details,
        "calibration_id": settings.calibration_id,
        "profile": profile.as_dict(),
        "profile_sha256": profile.sha256,
        "input_layout": layout_name,
        "selected_raw_channels": list(mapping),
        "calibration_status": "unverified"
        if settings.calibration_id == "unverified-board"
        else "supplied-unverified",
        "calibration_sha256": settings.calibration_sha256,
        "geometry_m": [list(p) for p in profile.positions_m],
        "direction_convention": "q points toward later arrivals; source vector is -q; local XY azimuth from +X; linear direction has rotational ambiguity",
        "settings": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in vars(settings).items()
        },
        "request_settings": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in vars(requested_settings).items()
        },
        "block_frames": block_len,
        "analysis_window_frames": round(rate * settings.analysis_window_seconds),
        "analysis_hop_frames": round(rate * settings.analysis_hop_seconds),
        "tracks": [],
        "outputs": {},
        "output_diagnostics": {},
        "gain_policy": "equal_selected_channel_weights_no_automatic_gain",
        "pcm_dither": "deterministic_TPDF_at_target_bit_depth",
    }
    if retry_of is not None:
        manifest["retry_of"] = retry_of
    manifest["required_mount_checked"] = required_mount is not None
    writers: list[OutputSet] = []
    try:
        writers = [
            OutputSet(destination, i + 1, rate, frames, settings)
            for i in range(len(tracks))
        ]
        previous = [np.zeros(3) for _ in tracks]
        with sf.SoundFile(str(source)) as stream:
            for index in range(count):
                start = index * block_len
                length = min(block_len, frames - start)
                block, first = read_block(stream, start, length, mapping, halo=halo)
                block = _calibrate(block, settings)
                fft_len = next_fast_len(len(block) + 2 * HALO)
                spectrum = np.fft.rfft(block, n=fft_len, axis=0)
                for track_id, writer in enumerate(writers):
                    hit = tracks[track_id][index]
                    q = np.asarray(
                        hit["direction_q"] if hit is not None else previous[track_id],
                        dtype=float,
                    )
                    if track_id > 0 and hit is None:
                        audio = np.zeros(length, dtype="float32")
                    else:
                        audio = beam_fft(
                            block,
                            first,
                            start,
                            length,
                            rate,
                            0.0,
                            settings.speed,
                            spectrum,
                            steering_samples(profile, q, rate, settings.speed),
                        )
                        if (
                            index
                            and hit is not None
                            and np.linalg.norm(q - previous[track_id]) > 0.01
                        ):
                            fade_len = min(length, round(0.02 * rate))
                            old = beam_fft(
                                block,
                                first,
                                start,
                                length,
                                rate,
                                0.0,
                                settings.speed,
                                spectrum,
                                steering_samples(
                                    profile, previous[track_id], rate, settings.speed
                                ),
                            )
                            blend = np.linspace(0, 1, fade_len, dtype="float32")
                            audio[:fade_len] = (
                                old[:fade_len] * (1 - blend) + audio[:fade_len] * blend
                            )
                    if np.max(np.abs(audio)) > 1.00001:
                        raise ValueError("Beam clipping; use a calibrated lower gain")
                    writer.write(audio)
                    previous[track_id] = q
                    manifest["tracks"].append(
                        {
                            "track": track_id + 1,
                            "start_sample": start,
                            "frames": length,
                            "u": hit.get("u") if hit else None,
                            "direction_q": q.tolist(),
                            "azimuth_deg": hit.get("azimuth_deg") if hit else None,
                            "bearing_status": hit.get("bearing_status")
                            if hit
                            else "no_evidence",
                            "compatible_local_bearings_deg": hit.get(
                                "compatible_local_bearings_deg"
                            )
                            if hit
                            else None,
                            "compass_bearing_deg": hit.get("compass_bearing_deg")
                            if hit
                            else None,
                            "absolute_elevation_deg": hit.get("absolute_elevation_deg")
                            if hit
                            else None,
                            "angle_from_array_plane_magnitude_deg": hit.get(
                                "angle_from_array_plane_magnitude_deg"
                            )
                            if hit
                            else None,
                            "elevation_deg": hit.get("elevation_deg") if hit else None,
                            "active": hit is not None,
                            "score": hit["score"] if hit else None,
                        }
                    )
                if index % 30 == 0:
                    _mount_guard(destination, required_mount)
                    _space_guard(
                        destination,
                        min(
                            16 * 1024 * 1024,
                            _space_required(
                                frames - start, len(tracks), rate, settings
                            ),
                        ),
                        settings.reserve_gib,
                    )
        for i, writer in enumerate(writers):
            name = f"track-{i + 1:03d}"
            manifest["outputs"][name] = writer.close()
            manifest["output_diagnostics"][name] = {
                "peak_float": writer.peak,
                "clipped_frames": writer.clipped_frames,
            }
        manifest["status"] = "complete"
    except BaseException as error:
        for writer in writers:
            writer.abort()
        manifest["status"] = (
            "canceled" if isinstance(error, KeyboardInterrupt) else "failed"
        )
        manifest["error"] = type(error).__name__
        raise
    finally:
        temp = destination / "manifest.json.part"
        temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        temp.replace(destination / "manifest.json")
        directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return manifest
