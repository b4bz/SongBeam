"""Bounded two-pass localization and lossless/lossy output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.fft import next_fast_len

from . import __version__
from .audio import HALO, beam_fft, inspect, read_block
from .localize import candidates, track_blocks


@dataclass(frozen=True)
class Settings:
    channel_map: tuple[int, int, int, int]
    calibration_id: str = "unverified-board"
    gains: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    polarities: tuple[int, int, int, int] = (1, 1, 1, 1)
    fixed_delays_samples: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    manual_angles: tuple[float, ...] = ()
    auto_beams: int = 1
    formats: tuple[str, ...] = ("flac",)
    wav_subtype: str = "PCM_24"
    flac_subtype: str = "PCM_24"
    mp3_bitrate: int = 192
    mp3_vbr_quality: int | None = None
    flac_level: int = 5
    block_seconds: float = 0.5
    fmin: float = 2000
    fmax: float = 8000
    reserve_gib: float = 8.0
    speed: float = 343.0

    def validate(self) -> None:
        if sorted(self.channel_map) != [0, 1, 2, 3]:
            raise ValueError("channel_map must be a permutation of 0,1,2,3")
        if (
            len(self.gains) != 4
            or any(not math.isfinite(x) or x <= 0 or x > 4 for x in self.gains)
            or len(self.polarities) != 4
            or any(x not in (-1, 1) for x in self.polarities)
            or len(self.fixed_delays_samples) != 4
            or any(
                not math.isfinite(x) or abs(x) > 32 for x in self.fixed_delays_samples
            )
        ):
            raise ValueError(
                "calibration needs four bounded gain, polarity and delay values"
            )
        if self.auto_beams not in (1, 2) or self.manual_angles and self.auto_beams != 1:
            raise ValueError("choose one/two automatic beams or manual angles")
        if any(not math.isfinite(a) or abs(a) > 90 for a in self.manual_angles):
            raise ValueError("manual angles must be within ±90 degrees from broadside")
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
    gains = np.asarray(settings.gains) * np.asarray(settings.polarities)
    output = block * gains[None, :]
    shifts = np.asarray(settings.fixed_delays_samples)
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


def process(
    input_path: Path,
    output: Path,
    settings: Settings,
    retry_of: str | None = None,
    required_mount: Path | None = None,
) -> dict:
    settings.validate()
    source, destination = _resolve_output(input_path, output)
    _mount_guard(destination.parent, required_mount)
    details = inspect(source, settings.channel_map)
    rate = int(details["sample_rate"])
    frames = int(details["frames"])
    block_len = max(1024, round(settings.block_seconds * rate))
    count = math.ceil(frames / block_len)
    inferred: list[list[dict]] = []
    if settings.manual_angles:
        tracks = [
            [
                {"u": round(float(math.sin(math.radians(angle))), 3), "score": None}
                for _ in range(count)
            ]
            for angle in settings.manual_angles
        ]
    else:
        with sf.SoundFile(str(source)) as stream:
            while True:
                block = stream.read(block_len, dtype="float32", always_2d=True)
                if not len(block):
                    break
                inferred.append(
                    candidates(
                        _calibrate(block[:, settings.channel_map], settings),
                        rate,
                        settings.auto_beams,
                        settings.fmin,
                        settings.fmax,
                    )
                )
        tracks = track_blocks(inferred, settings.auto_beams)
    _space_guard(
        destination.parent,
        _space_required(frames, len(tracks), rate, settings),
        settings.reserve_gib,
    )
    destination.mkdir(mode=0o750)
    manifest = {
        "schema": 1,
        "status": "running",
        "processor_version": __version__,
        "source": details,
        "calibration_id": settings.calibration_id,
        "geometry_m": [0, 0.045, 0.075, 0.12],
        "direction_convention": "degrees from array broadside; equal projection is ambiguous",
        "settings": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in vars(settings).items()
        },
        "block_frames": block_len,
        "tracks": [],
        "outputs": {},
        "output_diagnostics": {},
        "gain_policy": "equal_four_channel_weights_no_automatic_gain",
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
        previous = [0.0] * len(tracks)
        with sf.SoundFile(str(source)) as stream:
            for index in range(count):
                start = index * block_len
                length = min(block_len, frames - start)
                block, first = read_block(stream, start, length, settings.channel_map)
                block = _calibrate(block, settings)
                fft_len = next_fast_len(len(block) + 2 * HALO)
                spectrum = np.fft.rfft(block, n=fft_len, axis=0)
                for track_id, writer in enumerate(writers):
                    hit = tracks[track_id][index]
                    u = hit["u"] if hit is not None else previous[track_id]
                    if track_id > 0 and hit is None:
                        audio = np.zeros(length, dtype="float32")
                    else:
                        audio = beam_fft(
                            block,
                            first,
                            start,
                            length,
                            rate,
                            u,
                            settings.speed,
                            spectrum,
                        )
                        if (
                            index
                            and hit is not None
                            and abs(u - previous[track_id]) > 0.01
                        ):
                            fade_len = min(length, round(0.02 * rate))
                            old = beam_fft(
                                block,
                                first,
                                start,
                                length,
                                rate,
                                previous[track_id],
                                settings.speed,
                                spectrum,
                            )
                            blend = np.linspace(0, 1, fade_len, dtype="float32")
                            audio[:fade_len] = (
                                old[:fade_len] * (1 - blend) + audio[:fade_len] * blend
                            )
                    if np.max(np.abs(audio)) > 1.00001:
                        raise ValueError("Beam clipping; use a calibrated lower gain")
                    writer.write(audio)
                    previous[track_id] = u
                    manifest["tracks"].append(
                        {
                            "track": track_id + 1,
                            "start_sample": start,
                            "frames": length,
                            "u": u,
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
