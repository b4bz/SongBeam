"""Strict source inspection and bounded directional audio transforms."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.fft import next_fast_len

POSITIONS_M = np.array([0.0, 0.045, 0.075, 0.120], dtype=np.float64)
HALO = 96


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def riff_pcm(path: Path) -> dict:
    """Reject truncated, inconsistent, compressed or multi-RIFF input."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        head = stream.read(12)
        if len(head) != 12 or head[:4] != b"RIFF" or head[8:] != b"WAVE":
            raise ValueError("Expected a RIFF/WAVE file")
        declared = struct.unpack_from("<I", head, 4)[0] + 8
        if declared != size:
            raise ValueError(f"RIFF size mismatch: declared {declared}, actual {size}")
        fmt = None
        data = None
        while stream.tell() + 8 <= size:
            chunk_head = stream.read(8)
            chunk_size = struct.unpack_from("<I", chunk_head, 4)[0]
            start = stream.tell()
            end = start + chunk_size
            if end > size:
                raise ValueError("Truncated WAV chunk")
            if chunk_head[:4] == b"fmt ":
                payload = stream.read(min(chunk_size, 40))
                if len(payload) < 16:
                    raise ValueError("Incomplete WAV format")
                fmt = struct.unpack_from("<HHIIHH", payload)
            elif chunk_head[:4] == b"data":
                if data is not None:
                    raise ValueError("Multiple WAV data chunks need explicit review")
                data = (start, chunk_size)
            stream.seek(end + chunk_size % 2)
    if fmt is None or data is None:
        raise ValueError("Missing WAV format or data chunk")
    code, channels, rate, byte_rate, frame_bytes, bits = fmt
    if code != 1 or bits != 16 or channels != 4:
        raise ValueError("Expected four-channel 16-bit PCM SongBeam source")
    if (
        frame_bytes != channels * 2
        or byte_rate != rate * frame_bytes
        or rate < 16000
        or rate > 96000
    ):
        raise ValueError("Invalid WAV rate or frame fields")
    if data[1] == 0 or data[1] % frame_bytes:
        raise ValueError("Empty or non-integral WAV frames")
    info = sf.info(str(path))
    if (
        info.channels != channels
        or info.samplerate != rate
        or info.frames != data[1] // frame_bytes
    ):
        raise ValueError("Decoder and RIFF metadata disagree")
    return {
        "channels": channels,
        "sample_rate": rate,
        "frames": info.frames,
        "subtype": info.subtype,
        "data_offset": data[0],
        "data_bytes": data[1],
        "file_bytes": size,
        "source_sha256": sha256_file(path),
    }


def inspect(path: Path, mapping: tuple[int, ...] = (0, 1, 2, 3)) -> dict:
    report = riff_pcm(path)
    peaks = np.zeros(4, dtype=float)
    power = np.zeros(4, dtype=float)
    sums = np.zeros(4, dtype=float)
    samples = 0
    with sf.SoundFile(str(path)) as stream:
        while True:
            block = stream.read(65536, dtype="float32", always_2d=True)
            if not len(block):
                break
            block = block[:, mapping].astype(np.float64)
            peaks = np.maximum(peaks, np.max(np.abs(block), axis=0))
            power += np.sum(block * block, axis=0)
            sums += np.sum(block, axis=0)
            samples += len(block)
    rms = np.sqrt(power / samples)
    report.update(
        channel_map=list(mapping),
        peak=[round(float(x), 6) for x in peaks],
        rms=[round(float(x), 6) for x in rms],
        dc=[round(float(x / samples), 6) for x in sums],
        silent_channels=[i for i, x in enumerate(rms) if x < 1e-5],
        clipped_channels=[i for i, x in enumerate(peaks) if x >= 0.9999],
    )
    return report


def read_block(
    stream: sf.SoundFile,
    start: int,
    count: int,
    mapping: tuple[int, ...],
    halo: int = HALO,
) -> tuple[np.ndarray, int]:
    first = max(0, start - halo)
    last = min(len(stream), start + count + halo)
    stream.seek(first)
    data = stream.read(last - first, dtype="float32", always_2d=True)
    return data[:, mapping], first


def beam_fft(
    block: np.ndarray,
    first: int,
    start: int,
    count: int,
    rate: int,
    direction_cosine: float,
    speed: float = 343.0,
    spectrum: np.ndarray | None = None,
) -> np.ndarray:
    """Far-field delay-and-sum with zero-padded fractional shifts."""
    if not np.isfinite(direction_cosine) or abs(direction_cosine) > 1:
        raise ValueError("Direction cosine must be in [-1, 1]")
    length = next_fast_len(len(block) + 2 * HALO)
    spec = np.fft.rfft(block, n=length, axis=0) if spectrum is None else spectrum
    delays = POSITIONS_M * direction_cosine / speed * rate
    bins = np.fft.rfftfreq(length)
    shifted = (
        np.sum(spec * np.exp(2j * np.pi * bins[:, None] * delays[None, :]), axis=1)
        / 4.0
    )
    audio = np.fft.irfft(shifted, n=length)
    offset = start - first
    result = audio[offset : offset + count].astype("float32")
    if not np.all(np.isfinite(result)) or len(result) != count:
        raise ValueError("Beam output invalid")
    return result
